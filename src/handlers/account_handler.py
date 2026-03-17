from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ContextTypes
from src.models.database import get_session, User, save_user_session, load_user_session, clear_user_session
from src.utils.helpers import setup_logging
from src.utils.telegram_api import verifier

logger = setup_logging()

async def add_account_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle add account callback - with contact sharing button"""
    # Save session data
    user = update.effective_user
    if context.user_data:
        save_user_session(user.id, context.user_data)
    
    message = (
        "📱 <b>Akkaunt Qo'shish</b>\n\n"
        "Telefon raqamingizni yuboring:\n\n"
        "<b>Variant 1:</b> Quyidagi tugma orqali kontaktingizni ulashing\n"
        "<b>Variant 2:</b> Qo'lda xalqaro formatda kiriting: <code>+998901234567</code>"
    )
    
    # Create keyboard with contact sharing button
    contact_button = KeyboardButton("📱 Kontaktni Ulashish", request_contact=True)
    keyboard = [[contact_button]]
    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    
    await update.callback_query.message.reply_text(message, reply_markup=reply_markup, parse_mode='HTML')
    context.user_data['awaiting_phone'] = True

async def handle_contact_share(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle contact sharing - extract phone number from contact"""
    # Initialize user_data if empty
    if not context.user_data:
        context.user_data = {}
    
    if not context.user_data.get('awaiting_phone'):
        # Set flag if not set (for contact sharing flow)
        context.user_data['awaiting_phone'] = True
    
    contact = update.message.contact
    if not contact:
        await update.message.reply_text(
            "❌ Kontakt ma'lumotlari topilmadi.",
            reply_markup=ReplyKeyboardRemove()
        )
        return
    
    # Get phone number from contact
    phone_number = contact.phone_number
    
    # Ensure it has + prefix for international format
    if not phone_number.startswith('+'):
        # Add + if it's missing (assuming it's a local number)
        phone_number = '+' + phone_number
    
    # Remove reply keyboard and confirm
    await update.message.reply_text(
        f"✅ Kontakt qabul qilindi!\nTelefon: <code>{phone_number}</code>",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode='HTML'
    )
    
    # Process the phone number
    await process_phone_number(update, context, phone_number)

async def handle_phone_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle phone number input and send verification code"""
    if not context.user_data.get('awaiting_phone'):
        return
    
    phone_number = update.message.text.strip()
    await process_phone_number(update, context, phone_number)

async def process_phone_number(update: Update, context: ContextTypes.DEFAULT_TYPE, phone_number: str):
    """Process phone number and send verification code"""
    logger.info(f"[DEBUG] process_phone_number called with: {phone_number}")
    
    # Basic phone number validation
    if not phone_number.startswith('+') or len(phone_number) < 10:
        logger.warning(f"[DEBUG] Invalid phone format: {phone_number}")
        await update.message.reply_text(
            "❌ Telefon raqam formati noto'g'ri.\n"
            "Iltimos, xalqaro formatda kiriting: <code>+1234567890</code>",
            parse_mode='HTML'
        )
        return
    
    # Save phone number temporarily
    context.user_data['phone_number'] = phone_number
    context.user_data['awaiting_phone'] = False
    logger.info(f"[DEBUG] Saved phone_number to user_data")
    
    # Save session data
    user = update.effective_user
    save_user_session(user.id, context.user_data)
    logger.info(f"[DEBUG] Saved user session")
    
    # Send verification code using real Telegram API
    logger.info(f"[DEBUG] Calling verifier.send_verification_code for {phone_number}")
    try:
        success, result = await verifier.send_verification_code(phone_number)
        logger.info(f"[DEBUG] send_verification_code returned: success={success}, result={result}")
        
        if not success:
            message = f"❌ Xatolik yuz berdi: {result}\n\nIltimos, API kalitlaringizni tekshiring va qaytadan urinib ko'ring."
            context.user_data['awaiting_phone'] = True
            
            keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="back_to_main")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(message, reply_markup=reply_markup, parse_mode='HTML')
            return
        
        context.user_data['using_demo'] = False
        
        if success:
            context.user_data['awaiting_code'] = True
            context.user_data['phone_code_hash'] = result  # Store the hash for verification
            
            # Update user in database
            db_session = get_session()
            try:
                db_user = db_session.query(User).filter(User.telegram_id == str(user.id)).first()
                if db_user:
                    db_user.phone_number = phone_number
                    db_session.commit()
            except Exception as e:
                logger.error(f"Telefon raqam saqlashda xato: {e}")
            finally:
                db_session.close()
            
            # Save session data AFTER setting awaiting_code
            save_user_session(user.id, context.user_data)
            
            # Provide instructions based on delivery method
            message = (
                f"📱 <b>Telefon Raqam Saqlandi</b>\n\n"
                f"Telefon: <code>{phone_number}</code>\n\n"
                f"✅ Kod yuborildi!\n\n"
                "<b>KODNI QAYERDAN TOPISH:</b>\n\n"
                "<b>1-usul (SMS):</b>\n"
                "• Telefoningizga SMS xabar kelishini kuting\n"
                "• 5 xonali kodni ko'ring\n\n"
                "<b>2-usul (Telegram App):</b>\n"
                "• Telegram ilovasini oching\n"
                "• Yuqori o'ng burchakda <b>3 chiziq (≡)</b> ni bosing\n"
                "• <b>Sozlamalar</b> ni tanlang\n"
                "• <b>Qurilmalar</b> ga kiring\n"
                "• <b>'Yangi qurilma'</b> xabarini bosing\n"
                "• 5 xonali kodni ko'rasiz\n\n"
                "<b>3-usul (Telegram Desktop):</b>\n"
                "• Kompyuterdagi Telegram Desktop ni oching\n"
                "• Xabarlar ichida kodni qidiring\n\n"
                "⚠️ Kod 1-2 daqiqa ichida keladi. Agar kelmasa:\n"
                "• /start ni bosing va qayta urinib ko'ring\n"
                "• Telefon raqamingizni to'g'ri kiritganingizni tekshiring\n\n"
                "<b>Kodni kiriting:</b> (misol: <code>12345</code>)"
            )
        else:
            message = f"❌ {result}"
            
    except Exception as e:
        logger.error(f"Telefon raqamni qayta ishlashda xato: {e}")
        message = "❌ Telefon raqamni qayta ishlashda xato yuz berdi. Iltimos, qaytadan urinib ko'ring."
    
    keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="back_to_main")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(message, reply_markup=reply_markup, parse_mode='HTML')

async def handle_verification_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle verification code input - with 2FA support"""
    # Check if awaiting 2FA password
    if context.user_data.get('awaiting_2fa'):
        await handle_2fa_password(update, context)
        return
    
    if not context.user_data.get('awaiting_code'):
        return
    
    code = update.message.text.strip()
    
    # Accept code in any format (12345 or 123.45 or 12.345)
    # Remove any non-digit characters
    full_code = ''.join(filter(str.isdigit, code))
    
    # Validate code length (should be 5 digits)
    if len(full_code) != 5:
        await update.message.reply_text(
            "❌ Kod formati noto'g'ri.\n"
            "Iltimos, 5 xonali kodni kiriting.\n"
            "Misol: <code>12345</code> yoki <code>123.45</code>",
            parse_mode='HTML'
        )
        return
    phone_number = context.user_data.get('phone_number')
    phone_code_hash = context.user_data.get('phone_code_hash')
    
    if not phone_number:
        await update.message.reply_text("❌ Telefon raqam topilmadi. Avval telefon raqamingizni kiriting.")
        return
    
    if not phone_code_hash and not context.user_data.get('using_demo'):
        await update.message.reply_text("❌ Kod hash topilmadi. Avval telefon raqamingizni qayta kiriting.")
        return
    
    try:
        # Verify code using Telegram API (with 2FA support)
        success, result = await verifier.verify_code(phone_number, full_code, phone_code_hash)
        
        if success:
            context.user_data['awaiting_code'] = False
            context.user_data['account_verified'] = True
            context.user_data['user_info'] = result
            
            # Save session data
            user = update.effective_user
            save_user_session(user.id, context.user_data)
            
            # Update user verification status and info
            db_session = get_session()
            try:
                db_user = db_session.query(User).filter(User.telegram_id == str(user.id)).first()
                if db_user:
                    db_user.is_verified = True
                    db_user.phone_number = phone_number
                    db_session.commit()
            except Exception as e:
                logger.error(f"Tasdiqlash holatini yangilashda xato: {e}")
            finally:
                db_session.close()
            
            message = (
                f"✅ <b>Akkaunt Muvaffaqiyatli Tasdiqlandi!</b>\n\n"
                f"<b>Ism:</b> {result['first_name']} {result['last_name'] or ''}\n"
                f"<b>Foydalanuvchi nomi:</b> @{result['username']}\n"
                f"<b>Telefon:</b> {result['phone']}\n\n"
                "Akkauntingiz qo'shildi va tasdiqlandi.\n"
                "Endi barcha bot funksiyalaridan foydalanishingiz mumkin.\n\n"
                "<b>Keyingi Qadamlar:</b>\n"
                "• 'Xabarni Rejalashtirish'ga o'ting xabarlar yaratish uchun\n"
                "• Darhol efirga chiqarish uchun 'Xabar Yuborish'dan foydalaning\n"
                "• Guruhlaringizni tanlash uchun 'Guruhlarni Yangilash' tugmasini bosing"
            )
            
            keyboard = [
                [InlineKeyboardButton("🔄 Guruhlarni Yangilash", callback_data="refresh_groups")],
                [InlineKeyboardButton("⬅️ Bosh Menyuga Qaytish", callback_data="back_to_main")]
            ]
        elif result == "2FA_REQUIRED":
            # 2FA is required - ask for password
            context.user_data['awaiting_2fa'] = True
            context.user_data['verification_code'] = full_code
            
            message = (
                "🔐 <b>Ikki Bosqichli Parol Talab Qilinadi</b>\n\n"
                "Sizning akkauntingizda ikki bosqichli parol (2FA) yoqilgan.\n"
                "Iltimos, Telegram parolingizni kiriting:\n\n"
                "<i>Eslatma: Parol xavfsizlik maqsadlarida saqlanmaydi.</i>"
            )
            
            keyboard = [[InlineKeyboardButton("⬅️ Bekor Qilish", callback_data="cancel_2fa")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(message, reply_markup=reply_markup, parse_mode='HTML')
            return
        else:
            message = f"❌ {result}"
            keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="back_to_main")]]
            
    except Exception as e:
        logger.error(f"Kodni tekshirishda xato: {e}")
        message = "❌ Kodni tekshirishda xato yuz berdi. Iltimos, qaytadan urinib ko'ring."
        keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="back_to_main")]]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(message, reply_markup=reply_markup, parse_mode='HTML')

async def handle_2fa_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle 2FA password input"""
    password = update.message.text.strip()
    phone_number = context.user_data.get('phone_number')
    phone_code_hash = context.user_data.get('phone_code_hash')
    verification_code = context.user_data.get('verification_code')
    
    if not phone_number or not verification_code:
        await update.message.reply_text("❌ Sessiya ma'lumotlari topilmadi. Iltimos, qaytadan boshlang.")
        return
    
    try:
        # Verify with 2FA password
        success, result = await verifier.verify_code(
            phone_number, 
            verification_code, 
            phone_code_hash,
            two_fa_password=password
        )
        
        if success:
            context.user_data['awaiting_2fa'] = False
            context.user_data['awaiting_code'] = False
            context.user_data['account_verified'] = True
            context.user_data['user_info'] = result
            
            # Save session data
            user = update.effective_user
            save_user_session(user.id, context.user_data)
            
            # Update user verification status
            db_session = get_session()
            try:
                db_user = db_session.query(User).filter(User.telegram_id == str(user.id)).first()
                if db_user:
                    db_user.is_verified = True
                    db_user.phone_number = phone_number
                    db_session.commit()
            except Exception as e:
                logger.error(f"Tasdiqlash holatini yangilashda xato: {e}")
            finally:
                db_session.close()
            
            message = (
                f"✅ <b>Akkaunt Muvaffaqiyatli Tasdiqlandi!</b>\n\n"
                f"<b>Ism:</b> {result['first_name']} {result['last_name'] or ''}\n"
                f"<b>Foydalanuvchi nomi:</b> @{result['username']}\n"
                f"<b>Telefon:</b> {result['phone']}\n\n"
                "Ikki bosqichli parol bilan akkauntingiz tasdiqlandi.\n"
                "Endi barcha bot funksiyalaridan foydalanishingiz mumkin."
            )
            
            keyboard = [
                [InlineKeyboardButton("🔄 Guruhlarni Yangilash", callback_data="refresh_groups")],
                [InlineKeyboardButton("⬅️ Bosh Menyuga Qaytish", callback_data="back_to_main")]
            ]
        else:
            message = f"❌ Parol noto'g'ri: {result}\n\nIltimos, to'g'ri parolni kiriting yoki 'Bekor Qilish' tugmasini bosing."
            keyboard = [[InlineKeyboardButton("⬅️ Bekor Qilish", callback_data="cancel_2fa")]]
            
    except Exception as e:
        logger.error(f"2FA parolni tekshirishda xato: {e}")
        message = "❌ Parolni tekshirishda xato yuz berdi. Iltimos, qaytadan urinib ko'ring."
        keyboard = [[InlineKeyboardButton("⬅️ Bekor Qilish", callback_data="cancel_2fa")]]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(message, reply_markup=reply_markup, parse_mode='HTML')

async def cancel_2fa_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel 2FA process"""
    context.user_data['awaiting_2fa'] = False
    context.user_data['awaiting_code'] = False
    context.user_data['verification_code'] = None
    
    message = "❌ Ikki bosqichli parol jarayoni bekor qilindi."
    
    keyboard = [
        [InlineKeyboardButton("🔄 Qayta Urinish", callback_data="add_account")],
        [InlineKeyboardButton("⬅️ Bosh Menyu", callback_data="back_to_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.answer()
    await update.callback_query.message.edit_text(message, reply_markup=reply_markup)

async def refresh_groups_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle refresh groups callback"""
    # Save session data
    user = update.effective_user
    
    # Try to load session if context is empty
    if not context.user_data or 'phone_number' not in context.user_data:
        saved_session = load_user_session(user.id)
        if saved_session:
            context.user_data.update(saved_session)
            logger.info(f"Restored session data for user {user.id} during refresh groups")

    phone_number = context.user_data.get('phone_number')
    
    # If still not found, check database directly
    if not phone_number:
        db_session = get_session()
        try:
            db_user = db_session.query(User).filter(User.telegram_id == str(user.id)).first()
            if db_user and db_user.phone_number:
                phone_number = db_user.phone_number
                context.user_data['phone_number'] = phone_number
                logger.info(f"Retrieved phone number from DB for user {user.id}: {phone_number}")
        finally:
            db_session.close()

    if not phone_number:
        await update.callback_query.message.reply_text("❌ Telefon raqam topilmadi. Iltimos, akkauntingizni qayta qo'shing.")
        return
    
    try:
        # Show loading message
        await update.callback_query.message.edit_text("🔄 Guruhlar yuklanmoqda, iltimos kuting...")
        
        # Get user's groups using Telegram API
        groups = await verifier.get_user_groups(phone_number)
        
        # Limit message length to avoid Telegram API limits
        max_message_length = 4000  # Leave buffer for Telegram's 4096 limit
        
        if not groups:
            message = "❌ Sizning guruhlaringiz topilmadi yoki ulangan guruhlar yo'q."
        else:
            # Save groups to database
            db_session = get_session()
            try:
                db_user = db_session.query(User).filter(User.telegram_id == str(user.id)).first()
                if db_user:
                    from src.models.database import UserGroup
                    # Clear existing groups
                    db_session.query(UserGroup).filter(UserGroup.user_id == db_user.id).delete()
                    
                    # Add new groups
                    saved_groups = 0
                    for group in groups[:50]:  # Limit to 50 groups to prevent overflow
                        try:
                            user_group = UserGroup(
                                user_id=db_user.id,
                                group_id=group['id'],
                                group_title=group['title'][:100]  # Limit title length
                            )
                            db_session.add(user_group)
                            saved_groups += 1
                        except Exception as e:
                            logger.error(f"Error saving group {group['id']}: {e}")
                            continue
                    
                    db_session.commit()
                    
                    # Create concise message
                    message = f"✅ {saved_groups} ta guruh topildi:\n\n"
                    
                    # Add groups with length checking
                    for i, group in enumerate(groups[:10]):  # Show only first 10 groups
                        group_line = f"{i+1}. {group['title'][:30]} ({group['type']})\n"
                        if len(message) + len(group_line) < max_message_length - 200:
                            message += group_line
                        else:
                            message += f"\n...va yana {len(groups) - i} ta guruh"
                            break
                    
                    if len(groups) > 10:
                        message += f"\n\nJami: {len(groups)} guruh"
                        
            except Exception as e:
                logger.error(f"Guruhlarni saqlashda xato: {e}")
                message = "❌ Guruhlarni saqlashda xato yuz berdi."
            finally:
                db_session.close()
                
    except Exception as e:
        logger.error(f"Guruhlarni olishda xato: {e}")
        message = "❌ Guruhlarni olishda xato yuz berdi. Iltimos, qaytadan urinib ko'ring."
    
    # Ensure message is not too long
    if len(message) > max_message_length:
        message = message[:max_message_length-100] + "\n\n...xabar uzunligi chegaralandi"
    
    keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="back_to_main")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')
    except Exception as e:
        logger.error(f"Xabarni yuborishda xato: {e}")
        # Fallback to shorter message
        fallback_message = "✅ Guruhlar yangilandi. Bosh menyuga qayting."
        await update.callback_query.message.edit_text(fallback_message, reply_markup=reply_markup)

async def my_account_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle my account callback - shows account info and management options"""
    user = update.effective_user
    
    # Try to load session if context is empty
    if not context.user_data:
        saved_session = load_user_session(user.id)
        if saved_session:
            context.user_data.update(saved_session)
    
    db_session = get_session()
    
    try:
        db_user = db_session.query(User).filter(User.telegram_id == str(user.id)).first()
        if db_user:
            status = "✅ Tasdiqlangan" if db_user.is_verified else "❌ Tasdiqlanmagan"
            phone = db_user.phone_number or "Berilmagan"
            
            # Get user's groups
            from src.models.database import UserGroup
            user_groups = db_session.query(UserGroup).filter(UserGroup.user_id == db_user.id).all()
            
            message = (
                "👤 <b>Mening Akkauntim</b>\n\n"
                f"<b>Telefon Raqami:</b> <code>{phone}</code>\n"
                f"<b>Tasdiqlash Holati:</b> {status}\n"
                f"<b>Obuna:</b> Faol\n"
                f"<b>Akkaunt Qo'shilgan:</b> {db_user.created_at.strftime('%Y-%m-%d')}\n"
                f"<b>Guruhlar Soni:</b> {len(user_groups)} ta\n\n"
                "<b>Amallar:</b> Quyidagi tugmalardan birini tanlang"
            )
            
            keyboard = [
                [InlineKeyboardButton("🔄 Guruhlarni Yangilash", callback_data="refresh_groups")],
                [InlineKeyboardButton("🔄 Akkauntni Almashtirish", callback_data="switch_account")],
                [InlineKeyboardButton("🗑 Akkauntni O'chirish", callback_data="delete_account")],
                [InlineKeyboardButton("⬅️ Orqaga", callback_data="back_to_main")]
            ]
        else:
            message = "❌ Akkaunt topilmadi."
            keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="back_to_main")]]
            
    except Exception as e:
        logger.error(f"Akkaunt ma'lumotlarini olishda xato: {e}")
        message = "❌ Akkaunt ma'lumotlarini olishda xato yuz berdi."
        keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="back_to_main")]]
    finally:
        db_session.close()
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')

async def delete_account_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle delete account callback - shows confirmation"""
    user = update.effective_user
    
    message = (
        "🗑 <b>Akkauntni O'chirish</b>\n\n"
        "Haqiqatan ham akkauntingizni o'chirmoqchimisiz?\n\n"
        "<b>Diqqat!</b> Bu amal quyidagilarni o'chiradi:\n"
        "• Barcha saqlangan guruhlar\n"
        "• Barcha rejalashtirilgan xabarlar\n"
        "• Akkaunt ma'lumotlari\n\n"
        "Bu amalni qaytarib bo'lmaydi!"
    )
    
    keyboard = [
        [InlineKeyboardButton("✅ Ha, O'chirish", callback_data="confirm_delete_account")],
        [InlineKeyboardButton("❌ Yo'q, Bekor Qilish", callback_data="my_account")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')

async def confirm_delete_account_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle confirm delete account callback - permanently deletes account"""
    user = update.effective_user
    
    db_session = get_session()
    
    try:
        db_user = db_session.query(User).filter(User.telegram_id == str(user.id)).first()
        if db_user:
            # Get phone number for session cleanup
            phone_number = db_user.phone_number
            
            # Delete user's groups
            from src.models.database import UserGroup, ScheduledMessage, Payment
            db_session.query(UserGroup).filter(UserGroup.user_id == db_user.id).delete()
            
            # Delete user's scheduled messages
            db_session.query(ScheduledMessage).filter(ScheduledMessage.user_id == db_user.id).delete()
            
            # Delete user's payments
            db_session.query(Payment).filter(Payment.user_id == db_user.id).delete()
            
            # Clean up Telegram session if exists
            if phone_number:
                try:
                    await verifier.cleanup_client(phone_number)
                except:
                    pass
                
                # Delete session file
                try:
                    import os
                    sessions_dir = 'sessions'
                    phone_clean = phone_number.replace('+', '')
                    session_file = f"{sessions_dir}/{phone_clean}_session.session"
                    if os.path.exists(session_file):
                        os.remove(session_file)
                        logger.info(f"Deleted session file: {session_file}")
                except Exception as e:
                    logger.error(f"Error deleting session file: {e}")
            
            # Delete user from database
            db_session.delete(db_user)
            db_session.commit()
            
            # Clear session data
            clear_user_session(user.id)
            if context.user_data:
                context.user_data.clear()
            
            message = (
                "✅ <b>Akkaunt Muvaffaqiyatli O'chirildi!</b>\n\n"
                "Barcha ma'lumotlaringiz o'chirildi:\n"
                "• Telegram akkaunt ulanishi\n"
                "• Saqlangan guruhlar\n"
                "• Rejalashtirilgan xabarlar\n"
                "• To'lov tarixi\n\n"
                "Yangi akkaunt qo'shish uchun /start buyrug'ini bosing."
            )
        else:
            message = "❌ Akkaunt topilmadi."
            
    except Exception as e:
        logger.error(f"Akkauntni o'chirishda xato: {e}")
        message = "❌ Akkauntni o'chirishda xato yuz berdi."
    finally:
        db_session.close()
    
    keyboard = [[InlineKeyboardButton("🏠 Bosh Menyu", callback_data="back_to_main")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')

# Qolgan funksiyalar ham session saqlash bilan yangilanadi...
async def account_status_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle account status callback"""
    # Save session data
    user = update.effective_user
    if context.user_data:
        save_user_session(user.id, context.user_data)
    
    db_session = get_session()
    
    try:
        db_user = db_session.query(User).filter(User.telegram_id == str(user.id)).first()
        if db_user:
            status = "✅ Tasdiqlangan" if db_user.is_verified else "❌ Tasdiqlanmagan"
            phone = db_user.phone_number or "Berilmagan"
            
            # Get user's groups
            from src.models.database import UserGroup
            user_groups = db_session.query(UserGroup).filter(UserGroup.user_id == db_user.id).all()
            
            message = (
                "📊 <b>Akkaunt Holati</b>\n\n"
                f"<b>Telefon Raqami:</b> <code>{phone}</code>\n"
                f"<b>Tasdiqlash Holati:</b> {status}\n"
                f"<b>Obuna:</b> Faol\n"
                f"<b>Akkaunt Qo'shilgan:</b> {db_user.created_at.strftime('%Y-%m-%d')}\n"
                f"<b>Guruhlar Soni:</b> {len(user_groups)} ta\n\n"
                "<b>Eslatma:</b> Obuna uchun faqat bitta akkaunt qo'shishingiz mumkin."
            )
        else:
            message = "❌ Akkaunt topilmadi."
            
    except Exception as e:
        logger.error(f"Akkaunt holatini olishda xato: {e}")
        message = "❌ Akkaunt holatini olishda xato yuz berdi."
    finally:
        db_session.close()
    
    keyboard = [
        [InlineKeyboardButton("🔄 Guruhlarni Yangilash", callback_data="refresh_groups")],
        [InlineKeyboardButton("⬅️ Orqaga", callback_data="back_to_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')

async def switch_account_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle switch account callback"""
    # Save session data
    user = update.effective_user
    if context.user_data:
        save_user_session(user.id, context.user_data)
    
    message = (
        "🔄 <b>Akkauntni Almashtirish</b>\n\n"
        "Haqiqatan ham akkauntingizni almashtirmoqchimisiz?\n"
        "Bu hozirgi akkauntingizni uzib, yangisini qo'shish imkonini beradi.\n\n"
        "<b>Ogohlantirish:</b> Bir vaqtda faqat bitta faol akkauntga ega bo'lishingiz mumkin."
    )
    
    keyboard = [
        [InlineKeyboardButton("✅ Ha, Almashtirish", callback_data="confirm_switch")],
        [InlineKeyboardButton("❌ Yo'q, Bekor Qilish", callback_data="back_to_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')

async def confirm_switch_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle confirm switch callback"""
    # Save session data
    user = update.effective_user
    if context.user_data:
        save_user_session(user.id, context.user_data)
    
    db_session = get_session()
    
    try:
        db_user = db_session.query(User).filter(User.telegram_id == str(user.id)).first()
        if db_user:
            # Reset verification status
            db_user.is_verified = False
            db_user.phone_number = None
            db_session.commit()
            
            # Clean up Telegram session if exists
            phone_number = context.user_data.get('phone_number')
            if phone_number:
                try:
                    await verifier.cleanup_client(phone_number)
                except:
                    pass
            
            # Clear session data
            clear_user_session(user.id)
            context.user_data.clear()
            
            message = (
                "🔄 <b>Akkaunt Almashtirildi</b>\n\n"
                "Akkauntingiz uzildi.\n"
                "Endi yangi akkaunt qo'shishingiz mumkin.\n\n"
                "'Akkaunt Qo'shish' tugmasini bosing."
            )
        else:
            message = "❌ Akkaunt topilmadi."
            
    except Exception as e:
        logger.error(f"Akkaunt almashtirishda xato: {e}")
        message = "❌ Akkaunt almashtirishda xato yuz berdi."
    finally:
        db_session.close()
    
    keyboard = [[InlineKeyboardButton("⬅️ Bosh Menyuga Qaytish", callback_data="back_to_main")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')

# Helper function to check if user can add account
def can_add_account(user_data, db_user):
    """Check if user can add an account"""
    if not db_user:
        return False, "Akkaunt topilmadi"
    
    if db_user.is_verified:
        return False, "Sizda allaqachon qo'shilgan akkaunt bor. O'zgartirish uchun 'Akkauntni Almashtirish'dan foydalaning."
    
    return True, "OK"