import asyncio
import logging
import os
from datetime import datetime, timedelta
from sqlalchemy.orm import sessionmaker
from ..models.database import init_database, get_session, ScheduledMessage, User, UserGroup
from .helpers import setup_logging
from .telegram_api import verifier
import json

logger = setup_logging()

# Global message queue for rate limiting across all users
# Stores only message IDs, not full objects to avoid SQLAlchemy detached instance issues
_message_queue = asyncio.Queue()
_queue_processor_started = False
_queue_processor_lock = asyncio.Lock()
_queue_worker_count = max(2, int(os.getenv("SCHEDULER_WORKERS", "4")))

async def process_message_queue():
    """Start queue worker pool once and keep it alive."""
    global _queue_processor_started

    async with _queue_processor_lock:
        if _queue_processor_started:
            logger.debug("Queue processor already running, skipping")
            return
        _queue_processor_started = True

    logger.info(f"Message queue worker pool started: workers={_queue_worker_count}")
    for worker_idx in range(_queue_worker_count):
        asyncio.create_task(_queue_worker(worker_idx + 1))


async def _queue_worker(worker_id: int):
    """Queue worker: each worker handles one scheduled item at a time."""
    while True:
        try:
            message_id = await _message_queue.get()
            from ..models.database import get_session, ScheduledMessage
            db_session = get_session()
            try:
                message = db_session.query(ScheduledMessage).filter(
                    ScheduledMessage.id == message_id,
                    ScheduledMessage.is_active == True
                ).first()
                
                if message:
                    await send_scheduled_message_isolated(message)
                else:
                    logger.warning(f"Message {message_id} not found or inactive")
            finally:
                db_session.close()

            # Small pacing per worker to reduce burst requests.
            await asyncio.sleep(0.35)
            _message_queue.task_done()
        except Exception as e:
            logger.error(f"Queue worker-{worker_id} error: {e}")
            await asyncio.sleep(2)

def cleanup_session_file(phone_number):
    """Clean up session file for a phone number"""
    if not phone_number:
        return
    try:
        sessions_dir = 'sessions'
        phone_clean = phone_number.replace('+', '')
        session_file = f"{sessions_dir}/{phone_clean}_session.session"
        if os.path.exists(session_file):
            os.remove(session_file)
            logger.info(f"Cleaned up session file: {session_file}")
    except Exception as e:
        logger.error(f"Error cleaning up session file: {e}")

async def cleanup_old_data():
    """Clean up old data to prevent server overload"""
    logger.info("Starting old data cleanup...")
    db_session = get_session()
    try:
        now = datetime.utcnow()
        
        # 1. Delete old sent messages (older than 24 hours)
        one_day_ago = now - timedelta(hours=24)
        old_sent_messages = db_session.query(ScheduledMessage).filter(
            ScheduledMessage.is_sent == True,
            ScheduledMessage.schedule_time < one_day_ago,
            ScheduledMessage.is_repeat == False  # Only non-repeating messages
        ).all()
        
        deleted_sent_count = 0
        for msg in old_sent_messages:
            db_session.delete(msg)
            deleted_sent_count += 1
        
        if deleted_sent_count > 0:
            logger.info(f"Deleted {deleted_sent_count} old sent messages")
        
        # 2. Delete old rejected payments (older than 30 days)
        thirty_days_ago = now - timedelta(days=30)
        from ..models.database import Payment
        old_rejected_payments = db_session.query(Payment).filter(
            Payment.status == 'rejected',
            Payment.processed_at < thirty_days_ago
        ).all()
        
        deleted_payment_count = 0
        for payment in old_rejected_payments:
            db_session.delete(payment)
            deleted_payment_count += 1
        
        if deleted_payment_count > 0:
            logger.info(f"Deleted {deleted_payment_count} old rejected payments")
        
        # 3. Clean up old log files (keep only last 7 days)
        try:
            logs_dir = 'logs'
            if os.path.exists(logs_dir):
                seven_days_ago = now - timedelta(days=7)
                for filename in os.listdir(logs_dir):
                    if filename.endswith('.log'):
                        filepath = os.path.join(logs_dir, filename)
                        file_mtime = datetime.fromtimestamp(os.path.getmtime(filepath))
                        if file_mtime < seven_days_ago:
                            os.remove(filepath)
                            logger.info(f"Deleted old log file: {filename}")
        except Exception as log_error:
            logger.error(f"Error cleaning up log files: {log_error}")
        
        # 4. Clean up orphaned session files
        try:
            sessions_dir = 'sessions'
            if os.path.exists(sessions_dir):
                # Get all active phone numbers from database
                active_phones = set()
                users = db_session.query(User).filter(User.phone_number != None).all()
                for user in users:
                    active_phones.add(user.phone_number.replace('+', ''))
                
                # Remove session files for inactive users
                for filename in os.listdir(sessions_dir):
                    if filename.endswith('.session'):
                        phone_from_file = filename.replace('_session.session', '')
                        if phone_from_file not in active_phones:
                            filepath = os.path.join(sessions_dir, filename)
                            os.remove(filepath)
                            logger.info(f"Deleted orphaned session file: {filename}")
        except Exception as session_error:
            logger.error(f"Error cleaning up session files: {session_error}")
        
        db_session.commit()
        logger.info("Old data cleanup completed")
        
    except Exception as e:
        logger.error(f"Error during cleanup: {e}")
        try:
            db_session.rollback()
        except Exception as rollback_error:
            logger.error(f"Cleanup rollback error: {rollback_error}")
    finally:
        try:
            db_session.close()
        except Exception as close_error:
            logger.error(f"Cleanup session close error: {close_error}")

def start_scheduler():
    """Start the message scheduler in background thread"""
    try:
        import threading
        thread = threading.Thread(target=_run_scheduler_thread, daemon=True)
        thread.start()
        logger.info("Xabar rejalashtiruvchi fon jarayonida ishga tushdi")
    except Exception as e:
        logger.error(f"Xabar rejalashtiruvchini ishga tushirishda xato: {e}")

def _run_scheduler_thread():
    """Run scheduler in separate thread"""
    try:
        # Create event loop for this thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.create_task(run_scheduler())
        loop.run_forever()
    except Exception as e:
        logger.error(f"Rejalashtiruvchi jarayonida xato: {e}")

async def run_scheduler():
    """Main scheduler loop with periodic cleanup"""
    logger.info("Xabar rejalashtiruvchi ishga tushdi")
    
    cleanup_counter = 0
    
    while True:
        try:
            # Check for messages to send every minute
            await check_and_send_messages()
            
            # Run cleanup every 60 minutes (every 60 iterations)
            cleanup_counter += 1
            if cleanup_counter >= 60:
                await cleanup_old_data()
                cleanup_counter = 0
            
            await asyncio.sleep(60)  # Wait 1 minute
        except Exception as e:
            logger.error(f"Rejalashtiruvchida xato: {e}")
            await asyncio.sleep(60)

async def check_and_send_messages():
    """Check for scheduled messages and send them"""
    db_session = get_session()
    try:
        # Get pending messages that should be sent now
        now = datetime.utcnow()
        
        # Use atomic transaction for all database operations
        try:
            # 1. Handle repeating messages that were already sent once (ATOMIC)
            repeating_messages = db_session.query(ScheduledMessage).filter(
                ScheduledMessage.is_active == True,
                ScheduledMessage.is_repeat == True,
                ScheduledMessage.is_sent == True
            ).all()
            
            rescheduled_count = 0
            for message in repeating_messages:
                interval = message.repeat_interval or 5
                next_run_time = message.schedule_time + timedelta(minutes=interval)
                
                if now >= next_run_time:
                    while next_run_time <= now:
                        next_run_time += timedelta(minutes=interval)
                    
                    message.schedule_time = next_run_time - timedelta(minutes=interval)
                    message.is_sent = False
                    rescheduled_count += 1
                    uz_next_time = next_run_time + timedelta(hours=5)
                    logger.info(f"Repeating message {message.id} triggered. Next: {next_run_time} (UZ: {uz_next_time.strftime('%Y-%m-%d %H:%M')})")
            
            if rescheduled_count > 0:
                db_session.commit()
                logger.info(f"Rescheduled {rescheduled_count} repeating messages")
            
            # 2. Get pending messages (after commit for fresh data)
            pending_messages = db_session.query(ScheduledMessage).filter(
                ScheduledMessage.schedule_time <= now,
                ScheduledMessage.is_active == True,
                ScheduledMessage.is_sent == False
            ).all()
            
        except Exception as db_error:
            logger.error(f"Database error in atomic transaction: {db_error}")
            db_session.rollback()
            pending_messages = []
        
        # 3. Auto-delete messages older than 6 hours
        six_hours_go = now - timedelta(hours=6)
        old_messages = db_session.query(ScheduledMessage).filter(
            ScheduledMessage.is_active == True,
            ScheduledMessage.is_repeat == True,
            ScheduledMessage.created_at < six_hours_go
        ).all()
        
        deleted_count = 0
        for old_msg in old_messages:
            db_session.delete(old_msg)  # PERMANENTLY DELETE after 6 hours
            deleted_count += 1
            logger.info(f"Auto-deleted message {old_msg.id} (created at {old_msg.created_at}, older than 6 hours)")
        
        if deleted_count > 0:
            db_session.commit()
            logger.info(f"Auto-deleted {deleted_count} messages older than 6 hours")
        
        # 4. Send all pending messages using QUEUE system for rate limiting
        if pending_messages:
            logger.info(f"Processing {len(pending_messages)} pending messages with QUEUE system")
            
            # Start queue processor if not running
            global _queue_processor_started
            if not _queue_processor_started:
                asyncio.create_task(process_message_queue())
                await asyncio.sleep(0.5)  # Wait for processor to start
            
            # Commit any pending changes before adding to queue
            db_session.commit()
            
            # Add all message IDs to queue (not full objects)
            queued_count = 0
            for message in pending_messages:
                if not message.is_active:
                    logger.info(f"Message {message.id} was deactivated, skipping")
                    continue
                
                # Mark as sent immediately to prevent duplicate queuing
                message.is_sent = True
                await _message_queue.put(message.id)
                queued_count += 1
                logger.info(f"Message {message.id} added to queue (position: {_message_queue.qsize()})")
            
            # Commit the is_sent changes
            db_session.commit()
            logger.info(f"Queued {queued_count} messages for processing")
                
    except Exception as e:
        logger.error(f"Xabarlarni tekshirishda xato: {e}")
        try:
            db_session.rollback()
        except Exception as rollback_error:
            logger.error(f"Rollback xato: {rollback_error}")
    finally:
        try:
            db_session.close()
        except Exception as close_error:
            logger.error(f"Session yopishda xato: {close_error}")

async def send_scheduled_message_isolated(message_record):
    """Send scheduled message - COMPLETELY ISOLATED for true parallelism"""
    message_id = message_record.id
    user_id = message_record.user_id
    message_text = message_record.message_text
    target_groups = message_record.target_groups
    
    # Each call creates its OWN everything - no shared resources
    from ..models.database import get_session
    message_db_session = get_session()
    
    try:
        # Re-query message in isolated session
        current_message = message_db_session.query(ScheduledMessage).filter(
            ScheduledMessage.id == message_id
        ).first()
        
        if not current_message or not current_message.is_active:
            return False
        
        user = message_db_session.query(User).filter(User.id == user_id).first()
        if not user:
            return False
        
        # Get target groups
        group_ids = []
        if target_groups:
            try:
                clean_json = target_groups.replace("'", '"')
                group_ids = json.loads(clean_json)
            except Exception as json_error:
                logger.warning(f"Failed to parse target_groups JSON: {json_error}")
        
        if not group_ids:
            user_groups = message_db_session.query(UserGroup).filter(
                UserGroup.user_id == user.id,
                UserGroup.is_active == True
            ).all()
            group_ids = [g.group_id for g in user_groups]
        
        if not group_ids or not user.phone_number:
            return False

        # Create COMPLETELY INDEPENDENT Telegram client
        from .telegram_api import TelegramAPI
        import os
        
        # Get API credentials from environment - NO DEFAULTS
        api_id = os.getenv('TELEGRAM_API_ID')
        api_hash = os.getenv('TELEGRAM_API_HASH')
        
        # Validate API credentials
        if not api_id or not api_hash:
            logger.error("TELEGRAM_API_ID yoki TELEGRAM_API_HASH .env faylida sozlanmagan!")
            return False
        
        # New instance per message ensures no shared state
        user_api = TelegramAPI(api_id, api_hash, user.phone_number)
        
        # Send message
        success, result_msg = await user_api.send_message_to_groups(
            user.phone_number, message_text, group_ids
        )
        
        if success:
            # For repeating messages, is_sent is already True (set when queued)
            # For one-time messages, we keep it True
            # For repeating messages that need to repeat, scheduler will reset is_sent
            logger.info(f"Message {message_id} sent successfully: {result_msg}")
        else:
            # If failed, reset is_sent so it can be retried
            if not current_message.is_repeat:
                current_message.is_sent = False
                message_db_session.commit()
                logger.warning(f"Message {message_id} failed, will retry: {result_msg}")
            else:
                # For repeating messages, still mark as sent to avoid blocking
                # The scheduler will handle the next cycle
                logger.warning(f"Repeating message {message_id} failed but marked as sent: {result_msg}")
        
        return success
            
    except Exception as e:
        logger.error(f"Send error for message {message_id}: {e}")
        try:
            message_db_session.rollback()
        except Exception as rollback_error:
            logger.error(f"Rollback error: {rollback_error}")
        return False
    finally:
        try:
            message_db_session.close()
        except Exception as close_error:
            logger.error(f"Session close error: {close_error}")

# Keep old function for backward compatibility
async def send_scheduled_message(message_record, db_session):
    """Backward compatible wrapper"""
    return await send_scheduled_message_isolated(message_record)