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
_message_queue = asyncio.Queue()
_queue_processor_started = False

async def process_message_queue():
    """Process messages from queue with global rate limiting"""
    global _queue_processor_started
    _queue_processor_started = True
    logger.info("Global message queue processor started")
    
    while True:
        try:
            # Get message from queue
            message_task = await _message_queue.get()
            
            # Process with rate limiting (max 30 messages per minute globally)
            await send_scheduled_message_isolated(message_task)
            
            # Small delay to prevent too many requests
            await asyncio.sleep(2)  # 2 second delay between any two messages
            
            _message_queue.task_done()
        except Exception as e:
            logger.error(f"Queue processing error: {e}")
            await asyncio.sleep(5)

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
    """Main scheduler loop"""
    logger.info("Xabar rejalashtiruvchi ishga tushdi")
    
    while True:
        try:
            # Check for messages to send every minute
            await check_and_send_messages()
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
        
        # 1. Get one-time or newly created repeating messages that are due
        pending_messages = db_session.query(ScheduledMessage).filter(
            ScheduledMessage.schedule_time <= now,
            ScheduledMessage.is_active == True,
            ScheduledMessage.is_sent == False
        ).all()
        
        # 2. Handle repeating messages that were already sent once
        repeating_messages = db_session.query(ScheduledMessage).filter(
            ScheduledMessage.is_active == True,
            ScheduledMessage.is_repeat == True,
            ScheduledMessage.is_sent == True
        ).all()
        
        rescheduled_count = 0
        for message in repeating_messages:
            # For repeating messages, schedule_time is the time it was LAST scheduled to run
            # We check if now is past (last schedule time + interval)
            interval = message.repeat_interval or 5
            next_run_time = message.schedule_time + timedelta(minutes=interval)
            
            if now >= next_run_time:
                # Update schedule time to the next interval
                # If we are way behind, catch up to current time
                while next_run_time <= now:
                    next_run_time += timedelta(minutes=interval)
                
                message.schedule_time = next_run_time - timedelta(minutes=interval) # Current run time
                message.is_sent = False  # Mark as ready to send again
                pending_messages.append(message)
                rescheduled_count += 1
                # Convert to Uzbekistan time for logging
                uz_next_time = next_run_time + timedelta(hours=5)
                logger.info(f"Repeating message {message.id} triggered. Next will be at {next_run_time} (UZ: {uz_next_time.strftime('%Y-%m-%d %H:%M')})")
        
        if rescheduled_count > 0:
            db_session.commit()
        
        # 3. Auto-delete messages older than 6 hours and cleanup session files
        six_hours_go = now - timedelta(hours=6)
        old_messages = db_session.query(ScheduledMessage).filter(
            ScheduledMessage.is_active == True,
            ScheduledMessage.is_repeat == True,
            ScheduledMessage.created_at < six_hours_go
        ).all()
        
        deleted_count = 0
        for old_msg in old_messages:
            # Get user phone number before deleting
            old_user = db_session.query(User).filter(User.id == old_msg.user_id).first()
            old_phone = old_user.phone_number if old_user else None
            
            db_session.delete(old_msg)  # PERMANENTLY DELETE after 6 hours
            deleted_count += 1
            logger.info(f"Auto-deleted message {old_msg.id} (created at {old_msg.created_at}, older than 6 hours)")
            
            # Cleanup session file
            if old_phone:
                cleanup_session_file(old_phone)
        
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
            
            # Add all messages to queue
            queued_count = 0
            for message in pending_messages:
                db_session.refresh(message)
                if not message.is_active:
                    logger.info(f"Message {message.id} was deactivated, skipping")
                    continue
                
                await _message_queue.put(message)
                queued_count += 1
                logger.info(f"Message {message.id} added to queue (position: {_message_queue.qsize()})")
            
            logger.info(f"Queued {queued_count} messages for processing")
                
    except Exception as e:
        logger.error(f"Xabarlarni tekshirishda xato: {e}")
        try:
            db_session.rollback()
        except:
            pass
    finally:
        try:
            db_session.close()
        except:
            pass

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
            except:
                pass
        
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
        success, _ = await user_api.send_message_to_groups(
            user.phone_number, message_text, group_ids
        )
        
        if success:
            current_message.is_sent = True
            message_db_session.commit()
        
        return success
            
    except Exception as e:
        logger.error(f"Send error for message {message_id}: {e}")
        try:
            message_db_session.rollback()
        except:
            pass
        return False
    finally:
        try:
            message_db_session.close()
        except:
            pass

# Keep old function for backward compatibility
async def send_scheduled_message(message_record, db_session):
    """Backward compatible wrapper"""
    return await send_scheduled_message_isolated(message_record)