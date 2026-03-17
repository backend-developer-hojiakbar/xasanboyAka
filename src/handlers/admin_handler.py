from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from datetime import datetime, timedelta
from src.models.database import get_session, User, Payment
from src.utils.helpers import is_admin, format_datetime, format_subscription_status
from src.utils.helpers import setup_logging

logger = setup_logging()

# Handler for payment approve button from admin panel
async def admin_payment_approve_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle admin approve payment from inline button"""
    if not is_admin(update.effective_user.id):
        await update.callback_query.answer("❌ Kirish rad etildi", show_alert=True)
        return
    
    # Extract payment ID from callback data
    callback_data = update.callback_query.data
    payment_id = int(callback_data.split('_')[-1])
    
    session = get_session()
    try:
        payment = session.query(Payment).filter(Payment.id == payment_id).first()
        if payment:
            # Update payment status
            payment.status = 'approved'
            payment.processed_at = datetime.utcnow()
            payment.admin_notes = "Admin tomonidan tasdiqlangan (inline button)"
            
            # Activate user subscription for 1 month
            user = session.query(User).filter(User.id == payment.user_id).first()
            if user:
                user.is_active = True
                user.subscription_end = datetime.utcnow() + timedelta(days=30)
            
            session.commit()
            
            # Notify user
            try:
                await context.bot.send_message(
                    chat_id=user.telegram_id,
                    text=(
                        "✅ <b>To'lov Tasdiqlangan!</b>\n\n"
                        "Sizning to'lovingiz tasdiqlandi.\n"
                        "Obunangiz 1 oylik faollashtirildi.\n"
                        "Endi barcha bot funksiyalaridan foydalanishingiz mumkin.\n\n"
                        "Bosh menyuni ochish uchun /start tugmasini bosing."
                    ),
                    parse_mode='HTML'
                )
            except Exception as e:
                logger.error(f"Foydalanuvchini xabardor qilishda xato: {e}")
            
            # Update admin message
            message = (
                "✅ <b>To'lov Tasdiqlangan!</b>\n\n"
                f"<b>Foydalanuvchi:</b> {user.first_name} (@{user.username})\n"
                f"<b>Foydalanuvchi ID:</b> {user.telegram_id}\n"
                f"<b>Summa:</b> Aniqlanmoqda\n\n"
                "✅ To'lov muvaffaqiyatli tasdiqlandi!\n"
                "Foydalanuvchiga xabar yuborildi: 'To'lov tasdiqlandi. /start buyrug'ini bosing.'"
            )
            
            await update.callback_query.message.edit_caption(caption=message, parse_mode='HTML')
        else:
            await update.callback_query.answer("❌ To'lov topilmadi", show_alert=True)
            
    except Exception as e:
        logger.error(f"To'lovni tasdiqlashda xato: {e}")
        await update.callback_query.answer("❌ Xato yuz berdi", show_alert=True)
    finally:
        session.close()

# Handler for payment reject button from admin panel
async def admin_payment_reject_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle admin reject payment from inline button"""
    if not is_admin(update.effective_user.id):
        await update.callback_query.answer("❌ Kirish rad etildi", show_alert=True)
        return
    
    # Extract payment ID from callback data
    callback_data = update.callback_query.data
    payment_id = int(callback_data.split('_')[-1])
    
    # Store payment ID for rejection reason
    context.user_data['current_payment_id'] = payment_id
    
    # Ask for rejection reason
    message = (
        "❌ <b>To'lovni Rad Etish</b>\n\n"
        "Iltimos, rad etish sababini kiriting:\n\n"
        "1. Noto'g'ri chek\n"
        "2. Noto'g'ri miqdor\n"
        "3. Muddati o'tgan chek\n"
        "4. Boshqa sabab\n\n"
        "Raqam yoki batafsil sabab yozing:"
    )
    
    await update.callback_query.message.reply_text(message, parse_mode='HTML')
    context.user_data['awaiting_rejection_reason'] = True

async def handle_rejection_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle rejection reason input"""
    if not context.user_data.get('awaiting_rejection_reason'):
        return
    
    reason = update.message.text.strip()
    payment_id = context.user_data.get('current_payment_id')
    
    if not payment_id:
        await update.message.reply_text("❌ Rad etish uchun hech qanday to'lov yo'q.")
        return
    
    session = get_session()
    try:
        payment = session.query(Payment).filter(Payment.id == payment_id).first()
        if payment:
            # Update payment status
            payment.status = 'rejected'
            payment.processed_at = datetime.utcnow()
            payment.admin_notes = f"Rad etildi: {reason}"
            session.commit()
            
            # Notify user
            user = session.query(User).filter(User.id == payment.user_id).first()
            if user:
                try:
                    await context.bot.send_message(
                        chat_id=user.telegram_id,
                        text=(
                            f"❌ <b>To'lov Rad Etildi</b>\n\n"
                            f"Sabab: {reason}\n\n"
                            "Iltimos, to'lov tafsilotlaringizni tekshiring va qaytadan urinib ko'ring.\n"
                            "To'lovni qayta yuborish uchun /start buyrug'ini bosing."
                        ),
                        parse_mode='HTML'
                    )
                except Exception as e:
                    logger.error(f"Foydalanuvchini xabardor qilishda xato: {e}")
            
            # Update admin message
            admin_message = (
                "❌ <b>To'lov Rad Etildi!</b>\n\n"
                f"<b>Foydalanuvchi:</b> {user.first_name} (@{user.username})\n"
                f"<b>Foydalanuvchi ID:</b> {user.telegram_id}\n"
                f"<b>Summa:</b> Aniqlanmoqda\n\n"
                f"❌ Rad etish sababi: {reason}\n"
                "To'lov muvaffaqiyatli rad etildi!"
            )
            
            # Find the original message to update
            # In practice, you'd need to store the message ID, but for now we'll just send a new message
            await update.message.reply_text(
                f"❌ To'lov rad etildi! Sabab: {reason}\n"
                f"Foydalanuvchiga xabar yuborildi."
            )
        else:
            await update.message.reply_text("❌ To'lov topilmadi.")
            
    except Exception as e:
        logger.error(f"To'lovni rad etishda xato: {e}")
        await update.message.reply_text("❌ To'lovni rad etishda xato yuz berdi.")
    finally:
        session.close()
        context.user_data['awaiting_rejection_reason'] = False
        context.user_data.pop('current_payment_id', None)

# Other admin handlers...
async def admin_panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle admin panel callback"""
    if not is_admin(update.effective_user.id):
        await update.callback_query.answer("❌ Kirish rad etildi", show_alert=True)
        return
    
    message = (
        "👑 <b>Admin Panel</b>\n\n"
        "Quyidagi variantlardan birini tanlang:"
    )
    
    keyboard = [
        [InlineKeyboardButton("👥 Foydalanuvchilarni Boshqarish", callback_data="admin_manage_users")],
        [InlineKeyboardButton("💳 To'lovlarni Ko'rib Chiqish", callback_data="admin_review_payments")],
        [InlineKeyboardButton("📊 Statistika", callback_data="admin_statistics")],
        [InlineKeyboardButton("⬅️ Orqaga", callback_data="back_to_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')

# Add more handlers as needed...
async def admin_manage_users_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle admin manage users callback"""
    if not is_admin(update.effective_user.id):
        return
    
    session = get_session()
    try:
        users = session.query(User).order_by(User.created_at.desc()).limit(10).all()
        
        message = "👥 <b>Foydalanuvchi Boshqaruvi</b>\n\n"
        if not users:
            message += "Hech qanday foydalanuvchi topilmadi."
        else:
            for i, user in enumerate(users[:5], 1):
                status = format_subscription_status(user)
                name = user.first_name or "Nomi Yo'q"
                username = user.username or "Noma'lum"
                message += (
                    f"{i}. {name} (@{username})\n"
                    f"   ID: {user.telegram_id}\n"
                    f"   Holat: {status}\n"
                    f"   Qo'shilgan: {format_datetime(user.created_at)}\n\n"
                )
        
        message += "Foydalanuvchilarni boshqarish uchun inline buyruqlardan foydalaning."
        
    except Exception as e:
        logger.error(f"admin_manage_usersda xato: {e}")
        message = "❌ Foydalanuvchilarni olishda xato yuz berdi."
    finally:
        session.close()
    
    keyboard = [
        [InlineKeyboardButton("🔍 Foydalanuvchini Qidirish", callback_data="admin_search_user")],
        [InlineKeyboardButton("⬅️ Orqaga", callback_data="admin_panel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')

async def admin_review_payments_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle admin review payments callback"""
    if not is_admin(update.effective_user.id):
        return
    
    session = get_session()
    try:
        pending_payments = session.query(Payment).filter(Payment.status == 'pending').all()
        
        if not pending_payments:
            message = "💳 <b>To'lovni Ko'rib Chiqish</b>\n\nKo'rib chiqish uchun hech qanday to'lovlar yo'q."
        else:
            # Show first pending payment
            payment = pending_payments[0]
            user = session.query(User).filter(User.id == payment.user_id).first()
            
            first_name = user.first_name if user else "Noma'lum"
            username = user.username if user else "Noma'lum"
            telegram_id = user.telegram_id if user else "Noma'lum"

            message = (
                "💳 <b>To'lovni Ko'rib Chiqish</b>\n\n"
                f"<b>Foydalanuvchi:</b> {first_name}\n"
                f"<b>Foydalanuvchi nomi:</b> @{username}\n"
                f"<b>Foydalanuvchi ID:</b> {telegram_id}\n"
                f"<b>Yuborilgan:</b> {format_datetime(payment.created_at)}\n\n"
                "<b>Ko'rsatmalar:</b>\n"
                "1. Foydalanuvchidan to'lov qilinganligini tekshiring\n"
                "2. Chekni tekshiring (summa, vaqt, karta raqami)\n"
                "3. Agar to'lov to'g'ri bo'lsa 'Tasdiqlash' tugmasini bosing\n"
                "4. Agar to'lov noto'g'ri bo'lsa 'Inkor qilish' tugmasini bosing\n\n"
                "Chek quyida ko'rsatilgan."
            )
            
            # Store current payment ID for later use
            context.bot_data['current_payment_id'] = payment.id
            
    except Exception as e:
        logger.error(f"admin_review_paymentsda xato: {e}")
        message = "❌ To'lovlarni olishda xato yuz berdi."
    finally:
        session.close()
    
    keyboard = [
        [InlineKeyboardButton("✅ Tasdiqlash", callback_data="admin_approve_payment")],
        [InlineKeyboardButton("❌ Inkor Qilish", callback_data="admin_reject_payment")],
        [InlineKeyboardButton("➡️ Keyingi To'lov", callback_data="admin_next_payment")],
        [InlineKeyboardButton("⬅️ Orqaga", callback_data="admin_panel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')

async def admin_approve_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle admin approve payment callback"""
    if not is_admin(update.effective_user.id):
        return
    
    payment_id = context.bot_data.get('current_payment_id')
    if not payment_id:
        await update.callback_query.message.reply_text("❌ Tasdiqlash uchun hech qanday to'lov yo'q.")
        return
    
    session = get_session()
    try:
        payment = session.query(Payment).filter(Payment.id == payment_id).first()
        if payment:
            # Update payment status
            payment.status = 'approved'
            payment.processed_at = datetime.utcnow()
            payment.admin_notes = "Admin tomonidan tasdiqlangan"
            
            # Activate user subscription for 1 month
            user = session.query(User).filter(User.id == payment.user_id).first()
            if user:
                user.is_active = True
                user.subscription_end = datetime.utcnow() + timedelta(days=30)
            
            session.commit()
            
            # Notify user
            try:
                await context.bot.send_message(
                    chat_id=user.telegram_id,
                    text=(
                        "✅ <b>To'lov Tasdiqlangan!</b>\n\n"
                        "Sizning to'lovingiz tasdiqlandi.\n"
                        "Obunangiz 1 oylik faollashtirildi.\n"
                        "Endi barcha bot funksiyalaridan foydalanishingiz mumkin.\n\n"
                        "Bosh menyuni ochish uchun /start tugmasini bosing."
                    ),
                    parse_mode='HTML'
                )
            except Exception as e:
                logger.error(f"Foydalanuvchini xabardor qilishda xato: {e}")
            
            username = user.username or "Noma'lum"
            message = (
                "✅ To'lov muvaffaqiyatli tasdiqlandi!\n\n"
                f"Foydalanuvchi: {user.first_name} (@{username})\n"
                f"Foydalanuvchi ID: {user.telegram_id}\n\n"
                "Foydalanuvchiga xabar yuborildi: 'To'lov tasdiqlandi. /start buyrug'ini bosing.'"
            )
        else:
            message = "❌ To'lov topilmadi."
            
    except Exception as e:
        logger.error(f"To'lovni tasdiqlashda xato: {e}")
        message = "❌ To'lovni tasdiqlashda xato yuz berdi."
    finally:
        session.close()
    
    keyboard = [
        [InlineKeyboardButton("➡️ Keyingi To'lov", callback_data="admin_next_payment")],
        [InlineKeyboardButton("⬅️ Orqaga", callback_data="admin_review_payments")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')

async def admin_reject_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle admin reject payment callback"""
    if not is_admin(update.effective_user.id):
        return
    
    payment_id = context.bot_data.get('current_payment_id')
    if not payment_id:
        await update.callback_query.message.reply_text("❌ Rad etish uchun hech qanday to'lov yo'q.")
        return
    
    # Ask for rejection reason
    message = (
        "❌ <b>To'lovni Rad Etish</b>\n\n"
        "Iltimos, rad etish sababini kiriting:\n\n"
        "1. Noto'g'ri chek\n"
        "2. Noto'g'ri miqdor\n"
        "3. Muddati o'tgan chek\n"
        "4. Boshqa sabab\n\n"
        "Raqam yoki batafsil sabab yozing:"
    )
    
    keyboard = [
        [InlineKeyboardButton("⬅️ Orqaga", callback_data="admin_review_payments")],
        [InlineKeyboardButton("🔄 Bekor Qilish", callback_data="admin_review_payments")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')
    context.user_data['awaiting_rejection_reason'] = True

async def admin_next_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle next payment callback"""
    if not is_admin(update.effective_user.id):
        return
    
    session = get_session()
    try:
        # Get next pending payment
        current_payment_id = context.bot_data.get('current_payment_id')
        pending_payments = session.query(Payment).filter(Payment.status == 'pending').all()
        
        if not pending_payments:
            message = "💳 <b>To'lovni Ko'rib Chiqish</b>\n\nKo'rib chiqish uchun hech qanday to'lovlar yo'q."
            keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="admin_panel")]]
        else:
            # Find next payment after current one
            current_index = -1
            for i, payment in enumerate(pending_payments):
                if payment.id == current_payment_id:
                    current_index = i
                    break
            
            next_index = current_index + 1 if current_index >= 0 else 0
            if next_index >= len(pending_payments):
                next_index = 0  # Loop back to first
            
            payment = pending_payments[next_index]
            user = session.query(User).filter(User.id == payment.user_id).first()
            
            first_name = user.first_name if user else "Noma'lum"
            username = user.username if user else "Noma'lum"
            telegram_id = user.telegram_id if user else "Noma'lum"

            message = (
                "💳 <b>To'lovni Ko'rib Chiqish</b>\n\n"
                f"<b>Foydalanuvchi:</b> {first_name}\n"
                f"<b>Foydalanuvchi nomi:</b> @{username}\n"
                f"<b>Foydalanuvchi ID:</b> {telegram_id}\n"
                f"<b>Yuborilgan:</b> {format_datetime(payment.created_at)}\n\n"
                "<b>Ko'rsatmalar:</b>\n"
                "1. Foydalanuvchidan to'lov qilinganligini tekshiring\n"
                "2. Chekni tekshiring (summa, vaqt, karta raqami)\n"
                "3. Agar to'lov to'g'ri bo'lsa 'Tasdiqlash' tugmasini bosing\n"
                "4. Agar to'lov noto'g'ri bo'lsa 'Inkor qilish' tugmasini bosing\n\n"
                "Chek quyida ko'rsatilgan."
            )
            
            # Update current payment ID
            context.bot_data['current_payment_id'] = payment.id
            
            keyboard = [
                [InlineKeyboardButton("✅ Tasdiqlash", callback_data="admin_approve_payment")],
                [InlineKeyboardButton("❌ Inkor Qilish", callback_data="admin_reject_payment")],
                [InlineKeyboardButton("➡️ Keyingi To'lov", callback_data="admin_next_payment")],
                [InlineKeyboardButton("⬅️ Orqaga", callback_data="admin_panel")]
            ]
        
    except Exception as e:
        logger.error(f"admin_next_paymentda xato: {e}")
        message = "❌ Xato yuz berdi."
        keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="admin_review_payments")]]
    finally:
        session.close()
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')

async def admin_statistics_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle admin statistics callback"""
    if not is_admin(update.effective_user.id):
        return
    
    session = get_session()
    try:
        total_users = session.query(User).count()
        active_users = session.query(User).filter(User.is_active == True).count()
        pending_payments = session.query(Payment).filter(Payment.status == 'pending').count()
        approved_payments = session.query(Payment).filter(Payment.status == 'approved').count()
        rejected_payments = session.query(Payment).filter(Payment.status == 'rejected').count()
        
        message = (
            "📊 <b>Bot Statistikasi</b>\n\n"
            f"<b>Jami Foydalanuvchilar:</b> {total_users}\n"
            f"<b>Faol Foydalanuvchilar:</b> {active_users}\n"
            f"<b>Kutilayotgan To'lovlar:</b> {pending_payments}\n"
            f"<b>Tasdiqlangan To'lovlar:</b> {approved_payments}\n"
            f"<b>Rad Etlangan To'lovlar:</b> {rejected_payments}\n\n"
            f"<b>So'nggi Yangilangan:</b> {format_datetime(datetime.utcnow())}"
        )
        
    except Exception as e:
        logger.error(f"Statistikani olishda xato: {e}")
        message = "❌ Statistikani olishda xato yuz berdi."
    finally:
        session.close()
    
    keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="admin_panel")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')

async def admin_search_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle admin search user callback"""
    if not is_admin(update.effective_user.id):
        return
    
    message = (
        "🔍 <b>Foydalanuvchini Qidirish</b>\n\n"
        "Qidirish uchun foydalanuvchi ID yoki foydalanuvchi nomini kiriting:"
    )
    
    keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="admin_manage_users")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')
    context.user_data['awaiting_user_search'] = True

async def handle_user_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle user search input"""
    if not context.user_data.get('awaiting_user_search'):
        return
    
    search_term = update.message.text.strip()
    session = get_session()
    
    try:
        # Search by ID or username
        if search_term.isdigit():
            user = session.query(User).filter(User.telegram_id == search_term).first()
        else:
            user = session.query(User).filter(User.username == search_term.lstrip('@')).first()
        
        if user:
            status = format_subscription_status(user)
            first_name = user.first_name or "Yo'q"
            last_name = user.last_name or ""
            username = user.username or "Yo'q"

            message = (
                f"👤 <b>Foydalanuvchi Tafsilotlari</b>\n\n"
                f"<b>Ism:</b> {first_name} {last_name}\n"
                f"<b>Foydalanuvchi Nomi:</b> @{username}\n"
                f"<b>Foydalanuvchi ID:</b> {user.telegram_id}\n"
                f"<b>Telefon:</b> {user.phone_number or 'Berilmagan'}\n"
                f"<b>Holat:</b> {status}\n"
                f"<b>Qo'shilgan:</b> {format_datetime(user.created_at)}\n"
                f"<b>So'nggi Faoliyat:</b> {format_datetime(user.updated_at)}"
            )
            
            # Add management buttons
            keyboard = [
                [InlineKeyboardButton("🔄 Obunani Qayta O'rnatish", callback_data=f"admin_reset_sub_{user.id}")],
                [InlineKeyboardButton("❌ Deaktivatsiya Qilish", callback_data=f"admin_deactivate_{user.id}")],
                [InlineKeyboardButton("⬅️ Orqaga", callback_data="admin_manage_users")]
            ]
        else:
            message = "❌ Foydalanuvchi topilmadi."
            keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="admin_manage_users")]]
            
    except Exception as e:
        logger.error(f"Foydalanuvchini qidirishda xato: {e}")
        message = "❌ Foydalanuvchini qidirishda xato yuz berdi."
        keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="admin_manage_users")]]
    finally:
        session.close()
    
    context.user_data['awaiting_user_search'] = False
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(message, reply_markup=reply_markup, parse_mode='HTML')

# Helper functions for admin actions
async def admin_reset_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id):
    """Reset user subscription"""
    if not is_admin(update.effective_user.id):
        return
    
    session = get_session()
    try:
        user = session.query(User).filter(User.id == user_id).first()
        if user:
            user.is_active = True
            user.subscription_end = datetime.utcnow() + timedelta(days=30)
            session.commit()
            
            # Notify user
            try:
                await context.bot.send_message(
                    chat_id=user.telegram_id,
                    text="🔄 <b>Obuna Qayta O'rnatildi</b>\n\nObunangiz 30 kunlik muddatga qayta o'rnatildi.",
                    parse_mode='HTML'
                )
            except Exception as e:
                logger.error(f"Foydalanuvchini xabardor qilishda xato: {e}")
            
            message = "✅ Obuna muvaffaqiyatli qayta o'rnatildi!"
        else:
            message = "❌ Foydalanuvchi topilmadi."
    except Exception as e:
        logger.error(f"Obunani qayta o'rnatishda xato: {e}")
        message = "❌ Obunani qayta o'rnatishda xato yuz berdi."
    finally:
        session.close()
    
    keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="admin_manage_users")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')

async def admin_deactivate_user(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id):
    """Deactivate user"""
    if not is_admin(update.effective_user.id):
        return
    
    session = get_session()
    try:
        user = session.query(User).filter(User.id == user_id).first()
        if user:
            user.is_active = False
            user.subscription_end = None
            session.commit()
            
            message = "✅ Foydalanuvchi deaktivatsiya qilindi!"
        else:
            message = "❌ Foydalanuvchi topilmadi."
    except Exception as e:
        logger.error(f"Foydalanuvchini deaktivatsiya qilishda xato: {e}")
        message = "❌ Foydalanuvchini deaktivatsiya qilishda xato yuz berdi."
    finally:
        session.close()
    
    keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="admin_manage_users")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')