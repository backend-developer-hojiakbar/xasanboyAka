from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from datetime import datetime, timedelta
from src.models.database import get_session, User, save_user_session, load_user_session, clear_user_session
from src.utils.helpers import is_admin, format_subscription_status, get_payment_card_details
from src.utils.helpers import setup_logging
from src.utils.telegram_api import verifier

logger = setup_logging()

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command with session persistence"""
    user = update.effective_user
    db_session = get_session()
    
    try:
        # Get or create user
        db_user = db_session.query(User).filter(User.telegram_id == str(user.id)).first()
        if not db_user:
            db_user = User(
                telegram_id=str(user.id),
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name
            )
            db_session.add(db_user)
            db_session.commit()
            logger.info(f"New user registered: {user.id}")
        else:
            # Load saved session data
            saved_session = load_user_session(user.id)
            if saved_session:
                context.user_data.update(saved_session)
                logger.info(f"Loaded session data for user {user.id}")
        
        # Check user status in correct order:
        # 1. New user (not active) → Show subscription/payment menu first
        # 2. Active subscription but not verified → Show verification menu
        # 3. Active subscription and verified → Show main menu
        
        if not db_user.is_active or (db_user.subscription_end and db_user.subscription_end < datetime.utcnow()):
            # User needs to subscribe first (show payment card)
            await show_subscription_menu(update, context, db_user)
        elif not db_user.is_verified or not db_user.phone_number:
            # User has active subscription but needs to link Telegram account
            await show_verification_menu(update, context, db_user)
        else:
            # User has active subscription and is verified
            await show_main_menu(update, context, db_user)
            
    except Exception as e:
        logger.error(f"start_commandda xato: {e}")
        await update.message.reply_text("❌ Xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring.")
    finally:
        db_session.close()

async def handle_phone_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle phone number input and send verification code"""
    if not context.user_data.get('awaiting_phone'):
        return
    
    phone_number = update.message.text.strip()
    
    # Basic phone number validation
    if not phone_number.startswith('+') or len(phone_number) < 10:
        await update.message.reply_text(
            "❌ Telefone raqam formati noto'g'ri.\n"
            "Iltimos, xalqaro formatda kiriting: <code>+1234567890</code>",
            parse_mode='HTML'
        )
        return
    
    # Save phone number temporarily
    context.user_data['phone_number'] = phone_number
    context.user_data['awaiting_phone'] = False
    
    # Save session data
    user = update.effective_user
    save_user_session(user.id, context.user_data)
    
    # Send verification code using real Telegram API - NO DEMO FALLBACK
    try:
        success, result = await verifier.send_verification_code(phone_number)
        
        if success:
            context.user_data['awaiting_code'] = True
            context.user_data['phone_code_hash'] = result  # Store the hash for verification
            context.user_data['using_demo'] = False  # Explicitly set that we're not using demo
            
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
            
            message = (
                f"📱 <b>Telefon Raqam Saqlandi</b>\n\n"
                f"Telefon: <code>{phone_number}</code>\n\n"
                f"Telegramga haqiqiy tekshiruv kodi yuborildi.\n\n"
                "Telegramda kelgan 5 xonali kodni quyidagi formatda kiriting: <code>123.45</code>\n\n"
                "Misol: Agar kod 12345 bo'lsa, shunday kiriting: 123.45"
            )
        else:
            # If real API fails, show error and don't fall back to demo
            message = f"❌ Xatolik yuz berdi: {result}\n\nIltimos, API kalitlaringizni tekshiring va qaytadan urinib ko'ring."
            context.user_data['awaiting_phone'] = True  # Allow user to try again
            
    except Exception as e:
        logger.error(f"Telefon raqamni qayta ishlashda xato: {e}")
        message = f"❌ Telefon raqamni qayta ishlashda xato yuz berdi: {str(e)}\n\nIltimos, API kalitlaringizni tekshiring va qaytadan urinib ko'ring."
        context.user_data['awaiting_phone'] = True  # Allow user to try again
    
    keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="back_to_main")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(message, reply_markup=reply_markup, parse_mode='HTML')

async def handle_verification_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle verification code input"""
    if not context.user_data.get('awaiting_code'):
        return
    
    code = update.message.text.strip()
    
    # Validate code format (should be in format 123.45)
    parts = code.split('.')
    if len(parts) != 2 or len(parts[0]) != 3 or len(parts[1]) != 2:
        await update.message.reply_text(
            "❌ Kod formati noto'g'ri.\n"
            "Iltimos, kodni quyidagicha kiriting: <code>123.45</code>",
            parse_mode='HTML'
        )
        return
    
    # Combine the parts (123.45 -> 12345)
    full_code = ''.join(parts)
    phone_number = context.user_data.get('phone_number')
    phone_code_hash = context.user_data.get('phone_code_hash')
    
    if not phone_number:
        await update.message.reply_text("❌ Telefon raqam topilmadi. Avval telefon raqamingizni kiriting.")
        return
    
    if not phone_code_hash:
        await update.message.reply_text("❌ Kod hash topilmadi. Avval telefon raqamingizni qayta kiriting.")
        return
    
    try:
        # Verify code using real Telegram API only, no demo fallback
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
        else:
            message = f"❌ Tasdiqlashda xato: {result}\n\nIltimos, kodni qayta tekshirib kiriting."
            
    except Exception as e:
        logger.error(f"Kodni tekshirishda xato: {e}")
        message = f"❌ Kodni tekshirishda xato yuz berdi: {str(e)}\n\nIltimos, qaytadan urinib ko'ring."
    
    reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Orqaga", callback_data="back_to_main")]])
    await update.message.reply_text(message, reply_markup=reply_markup, parse_mode='HTML')

async def show_subscription_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, user):
    """Show subscription menu for users without active subscription"""
    # Save session data before showing menu
    if context.user_data:
        save_user_session(user.telegram_id, context.user_data)
    
    keyboard = [
        [InlineKeyboardButton("💳 Karta Raqam", callback_data="view_card")],
        [InlineKeyboardButton("📱 Video Qo'llanma", callback_data="video_guide")],
        [InlineKeyboardButton("📞 Admin Bilan Bog'lanish", callback_data="contact_admin")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    message = (
        "👋 <b>Avto Xabar Botga Xush Kelibsiz!</b>\n\n"
        "Botdan foydalanish uchun avval obuna bo'lishingiz kerak.\n\n"
        "<b>Obuna Bo'lish Jarayoni:</b>\n"
        "1️⃣ Karta raqamini ko'rish\n"
        "2️⃣ To'lov qiling va chekni yuboring\n"
        "3️⃣ Admin tasdiqlashini kuting (maksimal 1 soat)\n"
        "4️⃣ Barcha funksiyalarga 1-oylik kirish huquqi oling\n\n"
        "⚠️ <b>Eslatma!</b> To'lov qilishdan oldin video qo'llanmani ko'rib chiqing.\n\n"
        "Boshlash uchun quyidagi variantlarni bosing:"
    )
    
    if update.message:
        await update.message.reply_text(message, reply_markup=reply_markup, parse_mode='HTML')
    else:
        await update.callback_query.message.reply_text(message, reply_markup=reply_markup, parse_mode='HTML')

async def show_verification_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, user):
    """Show main menu directly - account linking is optional"""
    # Skip verification, go directly to main menu
    await show_main_menu(update, context, user)

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, user):
    """Show main menu for users with active subscription"""
    # Save session data before showing menu
    if context.user_data:
        save_user_session(user.telegram_id, context.user_data)
    
    keyboard = [
        [InlineKeyboardButton("➕ Akkaunt Qo'shish", callback_data="add_account")],
        [InlineKeyboardButton("👤 Mening Akkauntim", callback_data="my_account")],
        [InlineKeyboardButton("📤 Xabar Yuborish", callback_data="send_message")],
        [InlineKeyboardButton("📅 Xabarlar Rejasi", callback_data="message_schedule")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    subscription_info = format_subscription_status(user)
    
    message = (
        f"👋 <b>Xush kelibsiz, {user.first_name}!</b>\n\n"
        f"<b>Obuna Holati:</b> {subscription_info}\n\n"
        "<b>Bosh Menyu:</b>\n"
        "• Telegram akkauntingizni qo'shing va boshqaring\n"
        "• Guruhlarga xabarlarni rejalashtiring\n"
        "• Xabarlarni darhol yuboring\n"
        "• Rejalashtirilgan xabarlarni ko'ring va boshqaring\n\n"
        "Quyidagi variantlardan birini tanlang:"
    )
    
    if update.message:
        await update.message.reply_text(message, reply_markup=reply_markup, parse_mode='HTML')
    else:
        await update.callback_query.message.reply_text(message, reply_markup=reply_markup, parse_mode='HTML')

# Qolgan funksiyalar...
async def read_instructions_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle read instructions callback - kept for backward compatibility"""
    # Redirect to video guide
    await video_guide_callback(update, context)

async def view_card_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle view card details callback"""
    card_number, card_holder = get_payment_card_details()
    
    card_info = (
        "💳 <b>Karta Ma'lumotlari</b>\n\n"
        f"<b>Karta Raqami:</b> <code>{card_number}</code>\n"
        f"<b>Karta Egasi:</b> {card_holder}\n"
        f"<b>Summa:</b> 50,000 so'm (1 oylik obuna)\n\n"
        "<b>To'lov qilish uchun:</b>\n"
        "1. Yuqoridagi karta raqamiga to'lov qiling\n"
        "2. To'lov chekining skrinshotini oling\n"
        "3. Quyidagi 'To'lov Qildim' tugmasini bosing\n"
        "4. Chek skrinshotini yuboring\n"
        "5. Admin tasdiqlashini kuting (maksimal 1 soat)"
    )
    
    keyboard = [
        [InlineKeyboardButton("✅ To'lov Qildim", callback_data="payment_made")],
        [InlineKeyboardButton("⬅️ Orqaga", callback_data="back_to_subscription")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.message.edit_text(card_info, reply_markup=reply_markup, parse_mode='HTML')

async def make_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle make payment callback - redirects to view_card"""
    await view_card_callback(update, context)

async def payment_made_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle payment made callback"""
    message = (
        "📸 <b>To'lov Chekini Yuboring</b>\n\n"
        "Iltimos, to'lov chekining skrinshotini yuboring.\n"
        "Admin tekshirib, obunangizni 1 soat ichida faollashtiradi.\n\n"
        "<b>Nima yuborish kerak:</b>\n"
        "• To'lov tasdiqlashining aniq skrinshoti\n"
        "• Summa va tranzaksiya tafsilotlarini qo'shing"
    )
    
    keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="back_to_subscription")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')
    context.user_data['awaiting_receipt'] = True

async def handle_photo_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle receipt photo submission"""
    if not context.user_data.get('awaiting_receipt'):
        return
    
    user = update.effective_user
    photo = update.message.photo[-1]  # Get the largest photo
    
    db_session = get_session()
    try:
        db_user = db_session.query(User).filter(User.telegram_id == str(user.id)).first()
        if db_user:
            from src.models.database import Payment
            payment = Payment(
                user_id=db_user.id,
                receipt_photo_id=photo.file_id,
                status='pending'
            )
            db_session.add(payment)
            db_session.commit()
            
            # Save session data
            save_user_session(user.id, context.user_data)
            
            # Notify admin immediately with inline buttons
            admin_id = context.application.bot_data.get('admin_id')
            if admin_id:
                admin_message = (
                    f"💳 <b>Yangi To'lov Cheki</b>\n\n"
                    f"<b>Foydalanuvchi:</b> {db_user.first_name} (@{db_user.username})\n"
                    f"<b>Foydalanuvchi ID:</b> {db_user.telegram_id}\n"
                    f"<b>Summa:</b> Aniqlanmoqda\n\n"
                    f"Iltimos, chekni ko'rib chiqing va tasdiqlang/bekor qilish."
                )
                
                # Create inline keyboard for admin
                keyboard = [
                    [InlineKeyboardButton("✅ Tasdiqlash", callback_data=f"admin_approve_{payment.id}")],
                    [InlineKeyboardButton("❌ Inkor Qilish", callback_data=f"admin_reject_{payment.id}")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await context.bot.send_photo(
                    chat_id=admin_id,
                    photo=photo.file_id,
                    caption=admin_message,
                    reply_markup=reply_markup,
                    parse_mode='HTML'
                )
            
            message = (
                "✅ <b>Chek Yuborildi!</b>\n\n"
                "To'lov chekingiz adminga yuborildi.\n"
                "Tasdiqlanganidan keyin 1 soat ichida kirish huquqini olasiz.\n\n"
                "/start tugmasini bosish orqali obuna holatingizni tekshirishingiz mumkin"
            )
            
            await update.message.reply_text(message, parse_mode='HTML')
            context.user_data['awaiting_receipt'] = False
            
    except Exception as e:
        logger.error(f"Chekni qayta ishlashda xato: {e}")
        await update.message.reply_text("❌ Chekni qayta ishlashda xato. Iltimos, qaytadan urinib ko'ring.")
    finally:
        db_session.close()

async def video_guide_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle video guide callback"""
    message = (
        "🎥 <b>Video Qo'llanma</b>\n\n"
        "Bu botdan foydalanish bo'yicha to'liq qo'llanmani ko'ring:\n\n"
        "[Video Linki Bu Yerda]\n\n"
        "<b>Video Tarkibi:</b>\n"
        "• Akkaunt sozlash jarayoni\n"
        "• Xabar rejalashtirish qo'llanmasi\n"
        "• Guruh boshqaruvi\n"
        "• Muammolarni hal qilish bo'yicha maslahatlar"
    )
    
    keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="back_to_subscription")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')

async def contact_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle contact admin callback"""
    admin_id = context.application.bot_data.get('admin_id')
    if admin_id:
        message = (
            "📞 <b>Admin Bilan Bog'lanish</b>\n\n"
            "Har qanday savollar yoki yordam uchun bevosita admin bilan bog'laning.\n\n"
            f"Admin ID: <code>{admin_id}</code>"
        )
    else:
        message = "📞 <b>Admin Bilan Bog'lanish</b>\n\nAdmin bilan bog'lanish ma'lumotlari mavjud emas."
    
    keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="back_to_subscription")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')

async def back_to_main_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle back to main menu callback"""
    user = update.effective_user
    db_session = get_session()
    
    try:
        # If user was in "schedule message" flow, clear its state
        # so later messages don't get misrouted.
        if context.user_data:
            context.user_data.pop('awaiting_message_text', None)
            context.user_data.pop('message_text', None)
            context.user_data.pop('message_text_parts', None)
            context.user_data.pop('pending_message_id', None)

        db_user = db_session.query(User).filter(User.telegram_id == str(user.id)).first()
        if db_user:
            # Save current session before switching
            if context.user_data:
                save_user_session(user.id, context.user_data)
            await show_main_menu(update, context, db_user)
    except Exception as e:
        logger.error(f"back_to_main: {e}")
    finally:
        db_session.close()

async def back_to_subscription_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle back to subscription menu callback"""
    user = update.effective_user
    db_session = get_session()
    
    try:
        db_user = db_session.query(User).filter(User.telegram_id == str(user.id)).first()
        if db_user:
            # Save current session before switching
            if context.user_data:
                save_user_session(user.id, context.user_data)
            await show_subscription_menu(update, context, db_user)
    except Exception as e:
        logger.error(f"back_to_subscription: {e}")
    finally:
        db_session.close()