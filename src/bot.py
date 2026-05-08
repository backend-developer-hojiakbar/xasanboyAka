import asyncio
import logging
import sys
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from telegram import Update
from src.handlers.auth_handler import (
    start_command, read_instructions_callback, view_card_callback, make_payment_callback,
    payment_made_callback, handle_photo_receipt, video_guide_callback, contact_admin_callback,
    back_to_main_callback, back_to_subscription_callback
)
from src.handlers.account_handler import (
    add_account_callback, handle_phone_number, handle_verification_code, refresh_groups_callback,
    account_status_callback, switch_account_callback, confirm_switch_callback,
    my_account_callback, delete_account_callback, confirm_delete_account_callback,
    handle_contact_share, cancel_2fa_callback
)
from src.handlers.message_handler import (
    schedule_message_callback, send_message_callback, scheduled_messages_callback,
    handle_scheduled_message_text, handle_schedule_time, handle_target_selection,
    handle_media_upload, send_all_groups_callback, send_selected_groups_callback,
    select_group_callback, finish_group_selection_callback, handle_message_text_input,
    use_scheduled_message_callback, new_message_callback, message_schedule_callback,
    use_folder_callback, new_group_selection_callback, save_as_folder_callback, handle_folder_name_input,
    clear_all_messages_callback, manage_message_callback, confirm_clear_all_callback,
    finish_scheduled_message_text_callback,
    handle_interval_selection, set_interval_target_groups, handle_interval_group_selection,
    finish_interval_group_selection, confirm_delete_message_callback,
    show_telegram_folders, handle_folder_selection,
    handle_folder_toggle, clear_folder_selection, send_multi_folders,
    configure_send_folders_callback, config_folder_toggle_callback,
    config_folder_save_callback, config_folder_clear_callback, config_folder_sync_callback
)
from src.handlers.admin_handler import (
    admin_panel_callback, admin_manage_users_callback, admin_review_payments_callback,
    admin_approve_payment_callback, admin_reject_payment_callback, admin_next_payment_callback,
    admin_statistics_callback, admin_search_user_callback, handle_rejection_reason,
    handle_user_search, admin_payment_approve_callback, admin_payment_reject_callback
)
from src.utils.scheduler import start_scheduler
from src.utils.helpers import setup_logging, is_admin
from src.models.database import init_database

logger = setup_logging()

def setup_handlers(application: Application):
    """Setup all bot handlers"""
    
    # Command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("admin", lambda update, context: 
        admin_panel_callback(update, context) if is_admin(update.effective_user.id) else 
        update.message.reply_text("❌ Kirish rad etildi")))
    
    # Callback query handlers
    application.add_handler(CallbackQueryHandler(read_instructions_callback, pattern="^read_instructions$"))
    application.add_handler(CallbackQueryHandler(view_card_callback, pattern="^view_card$"))
    application.add_handler(CallbackQueryHandler(make_payment_callback, pattern="^make_payment$"))
    application.add_handler(CallbackQueryHandler(payment_made_callback, pattern="^payment_made$"))
    application.add_handler(CallbackQueryHandler(video_guide_callback, pattern="^video_guide$"))
    application.add_handler(CallbackQueryHandler(contact_admin_callback, pattern="^contact_admin$"))
    application.add_handler(CallbackQueryHandler(back_to_main_callback, pattern="^back_to_main$"))
    application.add_handler(CallbackQueryHandler(back_to_subscription_callback, pattern="^back_to_subscription$"))
    
    # Account handlers
    application.add_handler(CallbackQueryHandler(add_account_callback, pattern="^add_account$"))
    application.add_handler(CallbackQueryHandler(my_account_callback, pattern="^my_account$"))
    application.add_handler(CallbackQueryHandler(account_status_callback, pattern="^account_status$"))
    application.add_handler(CallbackQueryHandler(switch_account_callback, pattern="^switch_account$"))
    application.add_handler(CallbackQueryHandler(confirm_switch_callback, pattern="^confirm_switch$"))
    application.add_handler(CallbackQueryHandler(refresh_groups_callback, pattern="^refresh_groups$"))
    application.add_handler(CallbackQueryHandler(delete_account_callback, pattern="^delete_account$"))
    application.add_handler(CallbackQueryHandler(confirm_delete_account_callback, pattern="^confirm_delete_account$"))
    application.add_handler(CallbackQueryHandler(cancel_2fa_callback, pattern="^cancel_2fa$"))
    
    # Message handlers
    application.add_handler(CallbackQueryHandler(message_schedule_callback, pattern="^message_schedule$"))
    application.add_handler(CallbackQueryHandler(schedule_message_callback, pattern="^schedule_message$"))
    application.add_handler(
        CallbackQueryHandler(
            finish_scheduled_message_text_callback,
            pattern="^finish_scheduled_message_text$"
        )
    )
    application.add_handler(CallbackQueryHandler(send_message_callback, pattern="^send_message$"))
    application.add_handler(CallbackQueryHandler(scheduled_messages_callback, pattern="^scheduled_messages$"))
    
    # New send message handlers
    application.add_handler(CallbackQueryHandler(send_all_groups_callback, pattern="^send_all_groups$"))
    application.add_handler(CallbackQueryHandler(send_selected_groups_callback, pattern="^send_selected_groups$"))
    application.add_handler(CallbackQueryHandler(configure_send_folders_callback, pattern="^configure_send_folders$"))
    application.add_handler(CallbackQueryHandler(config_folder_toggle_callback, pattern=r"^config_folder_toggle_.*$"))
    application.add_handler(CallbackQueryHandler(config_folder_save_callback, pattern="^config_folder_save$"))
    application.add_handler(CallbackQueryHandler(config_folder_clear_callback, pattern="^config_folder_clear$"))
    application.add_handler(CallbackQueryHandler(config_folder_sync_callback, pattern="^config_folder_sync$"))
    application.add_handler(CallbackQueryHandler(select_group_callback, pattern="^select_group_.*$"))
    application.add_handler(CallbackQueryHandler(finish_group_selection_callback, pattern="^finish_group_selection$"))
    application.add_handler(CallbackQueryHandler(use_scheduled_message_callback, pattern="^use_scheduled_\\d+$"))
    application.add_handler(CallbackQueryHandler(new_message_callback, pattern="^new_message$"))
    
    # Folder handlers
    application.add_handler(CallbackQueryHandler(use_folder_callback, pattern="^use_folder_\\d+$"))
    application.add_handler(CallbackQueryHandler(new_group_selection_callback, pattern="^new_group_selection$"))
    application.add_handler(CallbackQueryHandler(save_as_folder_callback, pattern="^save_as_folder$"))
    
    # Message management handlers
    application.add_handler(CallbackQueryHandler(clear_all_messages_callback, pattern="^clear_all_messages$"))
    application.add_handler(CallbackQueryHandler(confirm_clear_all_callback, pattern="^confirm_clear_all$"))
    application.add_handler(CallbackQueryHandler(manage_message_callback, pattern="^manage_message_\\d+$"))
    application.add_handler(CallbackQueryHandler(confirm_delete_message_callback, pattern="^confirm_delete_\\d+$"))
    
    # Interval message handlers
    application.add_handler(CallbackQueryHandler(handle_interval_selection, pattern="^interval_15min$"))
    application.add_handler(CallbackQueryHandler(handle_interval_selection, pattern="^interval_30min$"))
    application.add_handler(CallbackQueryHandler(handle_interval_selection, pattern="^interval_1hour$"))
    application.add_handler(CallbackQueryHandler(set_interval_target_groups, pattern="^set_interval_all_groups$"))
    application.add_handler(CallbackQueryHandler(set_interval_target_groups, pattern="^set_interval_selected_groups$"))
    application.add_handler(CallbackQueryHandler(handle_interval_group_selection, pattern="^interval_select_group_.*$"))
    application.add_handler(CallbackQueryHandler(finish_interval_group_selection, pattern="^finish_interval_group_selection$"))
    
    # Telegram folder handlers
    application.add_handler(CallbackQueryHandler(set_interval_target_groups, pattern="^select_telegram_folder$"))
    application.add_handler(CallbackQueryHandler(handle_folder_selection, pattern=r"^folder_\d+$"))
    application.add_handler(CallbackQueryHandler(handle_folder_toggle, pattern=r"^toggle_folder_\d+$"))
    application.add_handler(CallbackQueryHandler(clear_folder_selection, pattern="^clear_folder_selection$"))
    application.add_handler(CallbackQueryHandler(send_multi_folders, pattern="^send_multi_folders$"))
    
    # Admin handlers (old ones)
    application.add_handler(CallbackQueryHandler(admin_panel_callback, pattern="^admin_panel$"))
    application.add_handler(CallbackQueryHandler(admin_manage_users_callback, pattern="^admin_manage_users$"))
    application.add_handler(CallbackQueryHandler(admin_review_payments_callback, pattern="^admin_review_payments$"))
    application.add_handler(CallbackQueryHandler(admin_approve_payment_callback, pattern="^admin_approve_payment$"))
    application.add_handler(CallbackQueryHandler(admin_reject_payment_callback, pattern="^admin_reject_payment$"))
    application.add_handler(CallbackQueryHandler(admin_next_payment_callback, pattern="^admin_next_payment$"))
    application.add_handler(CallbackQueryHandler(admin_statistics_callback, pattern="^admin_statistics$"))
    application.add_handler(CallbackQueryHandler(admin_search_user_callback, pattern="^admin_search_user$"))
    
    # New admin handlers for inline buttons
    application.add_handler(CallbackQueryHandler(admin_payment_approve_callback, pattern="^admin_approve_\\d+$"))
    application.add_handler(CallbackQueryHandler(admin_payment_reject_callback, pattern="^admin_reject_\\d+$"))
    
    # Message handlers for text input
    application.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, handle_text_input))
    # Photo handler for receipt submission
    application.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, handle_photo_receipt))

async def handle_text_input(update: Update, context):
    """Handle text input based on current state"""
    user_data = context.user_data
    
    # Handle phone number input
    if user_data.get('awaiting_phone'):
        await handle_phone_number(update, context)
        return
    
    # Handle verification code input
    if user_data.get('awaiting_code'):
        await handle_verification_code(update, context)
        return
    
    # Handle scheduled message text input
    if user_data.get('awaiting_message_text') and not user_data.get('send_to_all_groups') and not user_data.get('send_to_selected_groups'):
        await handle_scheduled_message_text(update, context)
        return
    
    # Handle schedule time input
    if user_data.get('awaiting_schedule_time'):
        await handle_schedule_time(update, context)
        return
    
    # Handle message text input for sending
    if user_data.get('awaiting_message_text') and (user_data.get('send_to_all_groups') or user_data.get('send_to_selected_groups')):
        await handle_message_text_input(update, context)
        return
    
    # Handle rejection reason input
    if user_data.get('awaiting_rejection_reason'):
        await handle_rejection_reason(update, context)
        return
    
    # Handle user search input
    if user_data.get('awaiting_user_search'):
        await handle_user_search(update, context)
        return
    
    # Handle folder name input
    if user_data.get('awaiting_folder_name'):
        await handle_folder_name_input(update, context)
        return
    
    # Handle contact sharing for phone number
    if update.message.contact and user_data.get('awaiting_phone'):
        await handle_contact_share(update, context)
        return

def main():
    """Main function to start the bot"""
    try:
        # Initialize database
        init_database()
        logger.info("Ma'lumotlar bazasi ishga tushirildi")
        
        # Start message scheduler
        start_scheduler()
        logger.info("Xabar rejalashtiruvchi ishga tushirildi")
        
        # Load environment variables
        from dotenv import load_dotenv
        load_dotenv()
        import os
        
        # Get bot token and admin ID
        BOT_TOKEN = os.getenv('BOT_TOKEN')
        ADMIN_ID = os.getenv('ADMIN_ID')
        
        if not BOT_TOKEN:
            raise ValueError("BOT_TOKEN topilmadi. Iltimos, .env fayliga BOT_TOKEN qo'shing")
        
        if not ADMIN_ID:
            raise ValueError("ADMIN_ID topilmadi. Iltimos, .env fayliga ADMIN_ID qo'shing")
        
        # Set admin ID in bot data
        application = Application.builder().token(BOT_TOKEN).build()
        application.bot_data['admin_id'] = ADMIN_ID
        logger.info(f"Admin ID o'rnatildi: {ADMIN_ID}")
        
        # Setup handlers
        setup_handlers(application)
        
        logger.info("Bot ishga tushirish yakunlandi")
        
        # Fix for Python 3.14: Set event loop policy
        if sys.version_info >= (3, 14):
            asyncio.set_event_loop(asyncio.new_event_loop())
        
        # Start the bot
        application.run_polling(allowed_updates=Update.ALL_TYPES)
        
    except Exception as e:
        logger.error(f"Botni ishga tushirishda xato: {e}")
        raise

if __name__ == "__main__":
    main()