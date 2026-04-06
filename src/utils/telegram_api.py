import asyncio
import logging
import threading
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, ApiIdInvalidError
from telethon.tl.functions.messages import ImportChatInviteRequest
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.types import InputPeerChannel, InputPeerChat, InputPeerUser
from datetime import datetime
from src.utils.helpers import setup_logging

logger = setup_logging()

# Global session locks to prevent concurrent access to same session file
_session_locks = {}
_session_locks_lock = threading.Lock()

def get_session_lock(phone_number):
    """Get or create a lock for a specific phone number's session"""
    if not phone_number:
        return None
    phone_clean = phone_number.replace('+', '')
    with _session_locks_lock:
        if phone_clean not in _session_locks:
            _session_locks[phone_clean] = asyncio.Lock()
        return _session_locks[phone_clean]

class TelegramAPI:
    def __init__(self, api_id, api_hash, phone_number=None):
        self.api_id = api_id
        self.api_hash = api_hash
        self.phone_number = phone_number
        self.client = None
        self.session_file = None  # Will use session files for verification
        self.is_valid_api = True  # Track if API credentials are valid
        self.phone_code_hash = None  # Store phone code hash
        
    async def connect(self):
        """Connect to Telegram without session files"""
        if not self.is_valid_api:
            logger.warning("API credentials are invalid, skipping real connection")
            return False
            
        try:
            # Always create new client without session file
            logger.info(f"Creating new Telegram client for {self.phone_number}")
            self.client = TelegramClient(None, self.api_id, self.api_hash)  # No session file
            
            # Start with phone number to avoid interactive input
            if self.phone_number:
                await self.client.start(phone=self.phone_number)
            else:
                await self.client.start()
                
            logger.info(f"Telegram client connected for {self.phone_number}")
            return True
            
        except ApiIdInvalidError:
            logger.error("API ID/Hash is invalid")
            self.is_valid_api = False
            return False
        except Exception as e:
            logger.error(f"Telegram client connection failed: {e}")
            return False
            
    async def disconnect(self):
        """Disconnect from Telegram"""
        if self.client:
            await self.client.disconnect()
            logger.info(f"Telegram client disconnected for {self.phone_number}")
            
    async def send_verification_code(self, phone_number):
        """Send verification code to phone number - NO DEMO FALLBACK"""
        logger.info(f"[DEBUG] Starting send_verification_code for {phone_number}")
        
        if not self.is_valid_api:
            logger.error("[DEBUG] API credentials are invalid")
            return False, "API credentials are invalid. Please update your TELEGRAM_API_ID and TELEGRAM_API_HASH in .env file."
            
        try:
            # Create session file name for this phone number
            import os
            sessions_dir = 'sessions'
            if not os.path.exists(sessions_dir):
                os.makedirs(sessions_dir)
            
            session_file = f"{sessions_dir}/{phone_number.replace('+', '')}_session"
            logger.info(f"[DEBUG] Session file: {session_file}")
            
            # Create new client with session file
            self.phone_number = phone_number
            logger.info(f"[DEBUG] Creating TelegramClient with api_id={self.api_id}, api_hash={self.api_hash[:5]}...")
            
            self.client = TelegramClient(session_file, self.api_id, self.api_hash)
            logger.info("[DEBUG] TelegramClient created successfully")
            
            # Connect first before sending code
            logger.info("[DEBUG] Connecting to Telegram...")
            await self.client.connect()
            logger.info("[DEBUG] Connected to Telegram successfully")
            
            # Check if already authorized (session exists)
            logger.info("[DEBUG] Checking if user is already authorized...")
            is_authorized = await self.client.is_user_authorized()
            logger.info(f"[DEBUG] is_user_authorized: {is_authorized}")
            
            if is_authorized:
                logger.info(f"[DEBUG] User {phone_number} already authorized")
                return True, "already_authorized"
            
            # Send code request using raw API to ensure SMS delivery
            logger.info(f"[DEBUG] Sending code request to {phone_number} via raw API...")
            try:
                from telethon.tl.functions.auth import SendCodeRequest
                from telethon.tl.types import CodeSettings
                
                # Force SMS delivery by disabling app-based delivery
                # This ensures code comes via SMS, not just Telegram app
                code_settings = CodeSettings(
                    allow_flashcall=False,
                    current_number=False,
                    allow_app_hash=False,  # Disable app hash to force SMS
                    allow_missed_call=False
                )
                
                # Send code request directly via API
                # api_id must be integer, not string
                api_id_int = int(self.api_id) if isinstance(self.api_id, str) else self.api_id
                
                result = await self.client(SendCodeRequest(
                    phone_number=phone_number,
                    api_id=api_id_int,
                    api_hash=self.api_hash,
                    settings=code_settings
                ))
                
                logger.info(f"[DEBUG] SendCodeRequest result type: {type(result)}")
                logger.info(f"[DEBUG] SendCodeRequest result: {result}")
                
                if hasattr(result, 'phone_code_hash'):
                    self.phone_code_hash = result.phone_code_hash
                    logger.info(f"[DEBUG] phone_code_hash received: {result.phone_code_hash}")
                else:
                    logger.error(f"[DEBUG] No phone_code_hash in result! Result attributes: {dir(result)}")
                    return False, "Failed to get phone_code_hash from Telegram"
                
                # Check delivery type
                delivery_type = "Unknown"
                if hasattr(result, 'type'):
                    type_str = str(type(result.type))
                    logger.info(f"[DEBUG] Code delivery type: {result.type}")
                    if 'SentCodeTypeApp' in type_str:
                        delivery_type = "Telegram App"
                        logger.warning(f"[DEBUG] Code sent via APP only. User must check Telegram app.")
                    elif 'SentCodeTypeSms' in type_str:
                        delivery_type = "SMS"
                        logger.info(f"[DEBUG] Code sent via SMS to {phone_number}")
                    elif 'SentCodeTypeCall' in type_str:
                        delivery_type = "Phone Call"
                        logger.info(f"[DEBUG] Code will be delivered via phone call")
                
                # Check for next_type (SMS fallback)
                if hasattr(result, 'next_type'):
                    logger.info(f"[DEBUG] Next type (fallback): {result.next_type}")
                
                # Check for timeout
                if hasattr(result, 'timeout'):
                    logger.info(f"[DEBUG] Timeout: {result.timeout}")
                
                # Check for terms of service
                if hasattr(result, 'terms_of_service'):
                    logger.info(f"[DEBUG] Terms of service: {result.terms_of_service}")
                
                logger.info(f"[DEBUG] Verification code sent successfully to {phone_number} via {delivery_type}")
                
            except Exception as send_error:
                logger.error(f"[DEBUG] send_code_request failed: {type(send_error).__name__}: {send_error}")
                import traceback
                logger.error(f"[DEBUG] Traceback: {traceback.format_exc()}")
                return False, f"send_code_request failed: {send_error}"
            
            # Keep connection alive for verification step
            logger.info("[DEBUG] Keeping connection alive for verify_code")
            
            return True, self.phone_code_hash
            
        except ApiIdInvalidError as e:
            logger.error(f"[DEBUG] API ID/Hash is invalid: {e}")
            self.is_valid_api = False
            return False, "API credentials are invalid. Please update your TELEGRAM_API_ID and TELEGRAM_API_HASH in .env file."
        except Exception as e:
            logger.error(f"[DEBUG] Failed to send verification code: {type(e).__name__}: {e}")
            import traceback
            logger.error(f"[DEBUG] Traceback: {traceback.format_exc()}")
            # Clean up on error
            if self.client:
                try:
                    await self.client.disconnect()
                except Exception as disconnect_error:
                    logger.debug(f"Client disconnect error during cleanup: {disconnect_error}")
                self.client = None
            return False, f"Verification code sending failed: {str(e)}. Please check your API credentials."
            
    async def verify_code(self, phone_number, code, phone_code_hash, two_fa_password=None):
        """Verify the received code - with 2FA support - uses existing client connection"""
        if not self.is_valid_api:
            return False, "API credentials are invalid. Please update your TELEGRAM_API_ID and TELEGRAM_API_HASH in .env file."
        
        # Use existing client if available, otherwise create new one
        client = self.client
        created_new_client = False
        
        try:
            # Use the provided phone_code_hash or stored one
            if not phone_code_hash and self.phone_code_hash:
                phone_code_hash = self.phone_code_hash
            
            # If no existing client, create new one
            if not client:
                import os
                sessions_dir = 'sessions'
                if not os.path.exists(sessions_dir):
                    os.makedirs(sessions_dir)
                
                session_file = f"{sessions_dir}/{phone_number.replace('+', '')}_session"
                client = TelegramClient(session_file, self.api_id, self.api_hash)
                await client.connect()
                created_new_client = True
            
            # Ensure we're connected
            if not client.is_connected():
                await client.connect()
            
            try:
                # Sign in with code
                result = await client.sign_in(phone_number, code, phone_code_hash=phone_code_hash)
            except SessionPasswordNeededError:
                # 2FA is enabled, need password
                if two_fa_password:
                    # Try to sign in with 2FA password
                    result = await client.sign_in(password=two_fa_password)
                else:
                    # Return special status indicating 2FA is needed
                    return False, "2FA_REQUIRED"
            
            # Get user info
            me = await client.get_me()
            user_info = {
                'first_name': me.first_name,
                'last_name': me.last_name,
                'username': me.username,
                'phone': me.phone
            }
            
            logger.info(f"User verified: {user_info}")
            
            # Save client for future use
            self.client = client
            
            return True, user_info
            
        except ApiIdInvalidError:
            logger.error("API ID/Hash is invalid")
            self.is_valid_api = False
            return False, "API credentials are invalid. Please update your TELEGRAM_API_ID and TELEGRAM_API_HASH in .env file."
        except Exception as e:
            logger.error(f"Code verification failed: {e}")
            return False, f"Code verification failed: {str(e)}"
        finally:
            # Only disconnect if we created a new client
            # Keep existing client connection alive
            if created_new_client and client:
                try:
                    await client.disconnect()
                except Exception as disconnect_error:
                    logger.debug(f"Client disconnect error: {disconnect_error}")
            
    async def get_user_groups(self, phone_number):
        """Get user's groups and channels - NO DEMO FALLBACK"""
        if not self.is_valid_api:
            logger.error("get_user_groups: API credentials are invalid")
            return []
            
        client = None
        try:
            # Create session file name for this phone number
            import os
            sessions_dir = 'sessions'
            if not os.path.exists(sessions_dir):
                os.makedirs(sessions_dir)
            
            phone_clean = phone_number.replace('+', '')
            session_file = f"{sessions_dir}/{phone_clean}_session"
            
            logger.info(f"Connecting to Telegram for groups fetching: {phone_number} (session: {session_file})")
            
            # Create new client with session file
            client = TelegramClient(session_file, self.api_id, self.api_hash)
            
            # Connect and start
            await client.connect()
            
            is_authorized = await client.is_user_authorized()
            if not is_authorized:
                logger.warning(f"User not authorized for {phone_number}, session may be invalid or expired")
                return []
            
            logger.info(f"User {phone_number} is authorized, fetching dialogs...")
            
            # Get dialogs (chats, groups, channels)
            dialogs = await client.get_dialogs()
            logger.info(f"Total dialogs found for {phone_number}: {len(dialogs)}")
            
            groups = []
            for dialog in dialogs:
                # Check for groups and channels
                if dialog.is_group or dialog.is_channel:
                    group_type = 'channel' if dialog.is_channel else 'group'
                    groups.append({
                        'id': str(dialog.entity.id),
                        'title': dialog.entity.title,
                        'type': group_type
                    })
            
            logger.info(f"Filtered {len(groups)} groups/channels for {phone_number}")
            return groups
        except ApiIdInvalidError:
            logger.error("API ID/Hash is invalid")
            self.is_valid_api = False
            return []
        except Exception as e:
            logger.error(f"Failed to get user groups for {phone_number}: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return []
        finally:
            if client:
                try:
                    await client.disconnect()
                    logger.info(f"Disconnected Telegram client for {phone_number}")
                except Exception as disconnect_error:
                    logger.error(f"Error disconnecting client: {disconnect_error}")
            
    async def get_user_folders(self, phone_number):
        """Get user's chat folders from Telegram"""
        if not self.is_valid_api:
            logger.error("get_user_folders: API credentials are invalid")
            return []
            
        client = None
        try:
            import os
            sessions_dir = 'sessions'
            if not os.path.exists(sessions_dir):
                os.makedirs(sessions_dir)
            
            phone_clean = phone_number.replace('+', '')
            session_file = f"{sessions_dir}/{phone_clean}_session"
            
            logger.info(f"Connecting to Telegram for folders fetching: {phone_number}")
            
            client = TelegramClient(session_file, self.api_id, self.api_hash)
            await client.connect()
            
            is_authorized = await client.is_user_authorized()
            if not is_authorized:
                logger.warning(f"User not authorized for {phone_number}")
                return []
            
            # Get dialog filters (folders)
            from telethon.tl.functions.messages import GetDialogFiltersRequest
            result = await client(GetDialogFiltersRequest())
            
            logger.info(f"GetDialogFilters result type: {type(result)}")
            
            # Detailed debug logging
            if hasattr(result, 'filters'):
                logger.info(f"Number of filters: {len(result.filters)}")
                for i, f in enumerate(result.filters):
                    logger.info(f"Filter {i}: id={getattr(f, 'id', 'N/A')}, title={getattr(f, 'title', 'N/A')}")
                    logger.info(f"Filter {i} attributes: {dir(f)}")
                    # Check for different peer attributes
                    for attr in ['include_peers', 'pinned_peers', 'include', 'exclude_peers', 'emoticon', 'color']:
                        if hasattr(f, attr):
                            val = getattr(f, attr)
                            logger.info(f"Filter {i}.{attr}: {len(val) if hasattr(val, '__len__') else val}")
            else:
                logger.info(f"Result attributes: {dir(result)}")
            
            folders = []
            
            # Handle different response formats
            filters_list = None
            if hasattr(result, 'filters'):
                filters_list = result.filters
            elif isinstance(result, list):
                filters_list = result
            elif hasattr(result, '__iter__'):
                filters_list = list(result)
            
            if filters_list:
                for folder in filters_list:
                    try:
                        # Skip if not a dialog filter
                        if not hasattr(folder, 'id'):
                            continue
                            
                        # Skip default folder (id=0) unless it's the only one
                        if folder.id == 0 and len(filters_list) > 1:
                            continue
                        
                        # Get folder title - handle TextWithEntities
                        raw_title = getattr(folder, 'title', f"Folder {folder.id}")
                        # Extract text from TextWithEntities if needed
                        if hasattr(raw_title, 'text'):
                            title = raw_title.text
                        else:
                            title = str(raw_title)
                        
                        folder_data = {
                            'id': folder.id,
                            'title': title,
                            'groups': []
                        }
                        
                        # Get included chats/peers - try different attribute names
                        include_peers = []
                        
                        # Check all possible peer attributes
                        peer_attrs = ['include_peers', 'pinned_peers', 'include', 'chats', 'channels']
                        for attr in peer_attrs:
                            if hasattr(folder, attr):
                                val = getattr(folder, attr)
                                if val and len(val) > 0:
                                    include_peers = val
                                    logger.info(f"Using {attr} for folder {folder.id}: {len(val)} items")
                                    break
                        
                        # Also check if folder has groups/channels flags
                        has_groups = getattr(folder, 'groups', False)
                        has_broadcasts = getattr(folder, 'broadcasts', False)
                        
                        logger.info(f"Folder {folder.id} - groups={has_groups}, broadcasts={has_broadcasts}, peers={len(include_peers)}")
                        
                        # If folder has groups/broadcasts flags but no explicit peers,
                        # it means "include all groups/channels" - fetch them from dialogs
                        if (has_groups or has_broadcasts) and len(include_peers) == 0:
                            logger.info(f"Folder {folder.id} has groups/broadcasts flags - fetching all dialogs")
                            try:
                                all_dialogs = await client.get_dialogs()
                                for dialog in all_dialogs:
                                    if dialog.is_group and has_groups:
                                        include_peers.append(dialog.input_entity)
                                    elif dialog.is_channel and has_broadcasts:
                                        include_peers.append(dialog.input_entity)
                                logger.info(f"Found {len(include_peers)} dialogs for folder {folder.id}")
                            except Exception as dialog_error:
                                logger.error(f"Error fetching dialogs for folder {folder.id}: {dialog_error}")
                        
                        for peer in include_peers:
                            try:
                                # Get chat info
                                chat = await client.get_entity(peer)
                                if chat:
                                    chat_title = getattr(chat, 'title', str(chat.id))
                                    folder_data['groups'].append({
                                        'id': str(chat.id),
                                        'title': chat_title
                                    })
                            except Exception as e:
                                logger.debug(f"Could not get entity for peer {peer}: {e}")
                                continue
                        
                        folders.append(folder_data)
                        # Safe logging for Unicode characters - ASCII only
                        safe_title = title.encode('ascii', 'replace').decode('ascii') if title else '[No title]'
                        logger.info(f"Found folder: {safe_title} with {len(folder_data['groups'])} groups")
                    except Exception as folder_error:
                        logger.warning(f"Error processing folder: {folder_error}")
                        continue
            
            logger.info(f"Total folders found: {len(folders)}")
            return folders
            
        except Exception as e:
            logger.error(f"Failed to get user folders: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return []
        finally:
            if client:
                try:
                    await client.disconnect()
                except Exception as disconnect_error:
                    logger.debug(f"Client disconnect error in get_user_groups: {disconnect_error}")
    
    async def send_message_to_groups(self, phone_number, message_text, group_ids, delay_seconds=1):
        """Send message to specified groups with rate limiting and session locking - NO DEMO FALLBACK"""
        if not self.is_valid_api:
            return False, "API credentials are invalid. Please update your TELEGRAM_API_ID and TELEGRAM_API_HASH in .env file."
        
        # Get session lock for this phone number to prevent concurrent access
        session_lock = get_session_lock(phone_number)
        
        # Wait for lock with timeout to prevent indefinite blocking
        try:
            await asyncio.wait_for(session_lock.acquire(), timeout=30.0)
            logger.info(f"Session lock acquired for {phone_number}")
        except asyncio.TimeoutError:
            logger.error(f"Could not acquire session lock for {phone_number} within 30 seconds")
            return False, "Session busy - another message is being sent from this account"
        
        client = None
        try:
            # Create session file name for this phone number
            import os
            sessions_dir = 'sessions'
            if not os.path.exists(sessions_dir):
                os.makedirs(sessions_dir)
            
            session_file = f"{sessions_dir}/{phone_number.replace('+', '')}_session"
            
            # Create new client with session file
            client = TelegramClient(session_file, self.api_id, self.api_hash)
            
            # Connect and start
            await client.connect()
            if not await client.is_user_authorized():
                logger.warning(f"User not authorized for {phone_number}, session may be invalid")
                return False, "User not authorized. Please re-authenticate your account."
            
            success_count = 0
            failed_groups = []
            
            # Send messages in batches: 20 messages, then 1-2 second pause
            batch_size = 20
            
            for i, group_id in enumerate(group_ids):
                try:
                    # Convert group_id to int
                    group_id_int = int(group_id)
                    
                    # Send message to group
                    await client.send_message(group_id_int, message_text)
                    success_count += 1
                    logger.info(f"Message sent to group {group_id} ({i+1}/{len(group_ids)})")
                    
                    # Check if we need a pause after this message
                    if i < len(group_ids) - 1:  # Not the last message
                        # After every 20 messages, pause 1-2 seconds
                        if (i + 1) % batch_size == 0:
                            logger.info(f"Batch complete ({batch_size} messages). Pausing 1.5s...")
                            await asyncio.sleep(1.5)  # 1.5 seconds pause between batches
                        else:
                            # Small delay between messages in same batch (0.3s)
                            await asyncio.sleep(0.3)
                        
                except Exception as e:
                    failed_groups.append(group_id)
                    logger.error(f"Failed to send message to group {group_id}: {e}")
                    # Add extra delay after error to avoid further rate limiting
                    await asyncio.sleep(2)
            
            if success_count > 0:
                result = (True, f"{success_count} guruhga muvaffaqiyatli yuborildi")
            else:
                result = (False, "Hech qanday guruhga xabar yuborilmadi")
            
            return result
            
        except ApiIdInvalidError:
            logger.error("API ID/Hash is invalid")
            self.is_valid_api = False
            return False, "API credentials are invalid. Please update your TELEGRAM_API_ID and TELEGRAM_API_HASH in .env file."
        except Exception as e:
            logger.error(f"Failed to send messages to groups: {e}")
            return False, f"Message sending failed: {str(e)}"
        finally:
            # Always release the lock first
            if session_lock and session_lock.locked():
                session_lock.release()
                logger.info(f"Session lock released for {phone_number}")
            # Then disconnect client
            if client:
                try:
                    await client.disconnect()
                except Exception as disconnect_error:
                    logger.debug(f"Client disconnect error in send_message_to_groups: {disconnect_error}")

    async def cleanup_client(self, phone_number):
        """Clean up client resources"""
        try:
            if self.client:
                await self.disconnect()
        except Exception as e:
            logger.error(f"Failed to cleanup client: {e}")

# Global instances - use real API
import os
from dotenv import load_dotenv
load_dotenv()

# Get real API credentials from environment - NO DEFAULTS
TELEGRAM_API_ID = os.getenv('TELEGRAM_API_ID')
TELEGRAM_API_HASH = os.getenv('TELEGRAM_API_HASH')

# Validate that API credentials are set
if not TELEGRAM_API_ID or not TELEGRAM_API_HASH:
    raise ValueError(
        "TELEGRAM_API_ID va TELEGRAM_API_HASH .env faylida sozlanmagan!\n"
        "Iltimos, .env fayliga quyidagi qatorlarni qo'shing:\n"
        "TELEGRAM_API_ID=30631048\n"
        "TELEGRAM_API_HASH=a4f7bf9bdeea19173830278c28ffdfd2"
    )

verifier = TelegramAPI(TELEGRAM_API_ID, TELEGRAM_API_HASH)