from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from datetime import datetime, timedelta
import asyncio
import json
from src.models.database import get_session, User, ScheduledMessage, UserGroup, GroupFolder
from src.utils.helpers import setup_logging
from src.utils.telegram_api import verifier

logger = setup_logging()

SCHEDULE_FOLDER_CONFIG_NAME = "__schedule_folder_targets__"
FOLDER_CACHE_NAME = "__telegram_folder_cache__"
_folder_sync_tasks = {}


def _load_schedule_folder_config(db_session, user_id):
    config = db_session.query(GroupFolder).filter(
        GroupFolder.user_id == user_id,
        GroupFolder.folder_name == SCHEDULE_FOLDER_CONFIG_NAME
    ).first()
    if not config or not config.group_ids:
        return {}
    try:
        return json.loads(config.group_ids)
    except Exception:
        return {}


def _save_schedule_folder_config(db_session, user_id, payload):
    config = db_session.query(GroupFolder).filter(
        GroupFolder.user_id == user_id,
        GroupFolder.folder_name == SCHEDULE_FOLDER_CONFIG_NAME
    ).first()
    serialized = json.dumps(payload)
    if config:
        config.group_ids = serialized
    else:
        config = GroupFolder(
            user_id=user_id,
            folder_name=SCHEDULE_FOLDER_CONFIG_NAME,
            group_ids=serialized
        )
        db_session.add(config)
    db_session.commit()


async def _resolve_target_groups_from_saved_folders(db_user, db_session):
    """Resolve target groups from local DB cache without Telegram API calls."""
    config = _load_schedule_folder_config(db_session, db_user.id)
    selected_folder_ids = [str(fid) for fid in config.get("selected_folder_ids", [])]
    if not selected_folder_ids:
        return [], []

    cache_row = db_session.query(GroupFolder).filter(
        GroupFolder.user_id == db_user.id,
        GroupFolder.folder_name == FOLDER_CACHE_NAME
    ).first()

    folder_cache = {}
    if cache_row and cache_row.group_ids:
        try:
            cache_payload = json.loads(cache_row.group_ids)
            for folder in cache_payload.get("folders", []):
                folder_cache[str(folder.get("id"))] = folder
        except Exception:
            folder_cache = {}

    all_group_ids = []
    folder_names = config.get("selected_folder_titles", [])
    for folder_id in selected_folder_ids:
        folder = folder_cache.get(folder_id)
        if not folder:
            continue
        for gid in folder.get("group_ids", []):
            gid = str(gid)
            if gid:
                all_group_ids.append(gid)

    # Legacy/fallback cache kept in config payload
    if not all_group_ids:
        cached_group_ids = config.get("cached_group_ids", [])
        all_group_ids = [str(g) for g in cached_group_ids if str(g)]
        folder_names = config.get("selected_folder_titles", [])

    unique_group_ids = list(dict.fromkeys(all_group_ids))
    return unique_group_ids, folder_names


def _fallback_active_user_groups(db_session, db_user):
    """Hard fallback so scheduling never blocks: use active groups from DB."""
    groups = db_session.query(UserGroup).filter(
        UserGroup.user_id == db_user.id,
        UserGroup.is_active == True
    ).all()
    return [str(g.group_id) for g in groups if g.group_id]


def _load_folder_name_cache(db_session, user_id):
    cache_row = db_session.query(GroupFolder).filter(
        GroupFolder.user_id == user_id,
        GroupFolder.folder_name == FOLDER_CACHE_NAME
    ).first()
    if not cache_row or not cache_row.group_ids:
        return []
    try:
        payload = json.loads(cache_row.group_ids)
        folders = payload.get("folders", [])
        return [{"id": str(f.get("id")), "title": str(f.get("title", f"Folder {f.get('id')}"))} for f in folders]
    except Exception:
        return []


def _save_folder_cache(db_session, user_id, folders):
    cache_row = db_session.query(GroupFolder).filter(
        GroupFolder.user_id == user_id,
        GroupFolder.folder_name == FOLDER_CACHE_NAME
    ).first()
    payload = {
        "updated_at": datetime.utcnow().isoformat(),
        "folders": folders
    }
    serialized = json.dumps(payload)
    if cache_row:
        cache_row.group_ids = serialized
    else:
        db_session.add(GroupFolder(
            user_id=user_id,
            folder_name=FOLDER_CACHE_NAME,
            group_ids=serialized
        ))
    db_session.commit()


async def _refresh_folder_group_cache(user_id, phone_number):
    """Background sync: fetch full folder->groups mapping and cache it in DB."""
    if not phone_number:
        return
    folders = await verifier.get_user_folders(phone_number)
    mapped = []
    for folder in folders:
        fid = str(folder.get("id"))
        title = folder.get("title", f"Folder {fid}")
        if not isinstance(title, str):
            title = str(title)
        group_ids = [str(g.get("id")) for g in folder.get("groups", []) if g.get("id")]
        mapped.append({"id": fid, "title": title, "group_ids": list(dict.fromkeys(group_ids))})

    db_session = get_session()
    try:
        _save_folder_cache(db_session, user_id, mapped)
        logger.info(f"Folder cache synced for user={user_id}, folders={len(mapped)}")
        return True
    except Exception as e:
        logger.error(f"Folder cache sync error for user={user_id}: {e}")
        return False
    finally:
        db_session.close()


def _ensure_folder_cache_sync(user_id, phone_number, force=False):
    """Start at most one background sync task per user."""
    existing_task = _folder_sync_tasks.get(user_id)
    if existing_task and not existing_task.done() and not force:
        return False

    task = asyncio.create_task(_refresh_folder_group_cache(user_id, phone_number))

    def _cleanup(done_task):
        current = _folder_sync_tasks.get(user_id)
        if current is done_task:
            _folder_sync_tasks.pop(user_id, None)

    task.add_done_callback(_cleanup)
    _folder_sync_tasks[user_id] = task
    return True


async def _hydrate_scheduled_message_targets(message_id, user_id, phone_number):
    """Populate scheduled message targets in background without blocking user."""
    try:
        await _refresh_folder_group_cache(user_id, phone_number)
    except Exception as e:
        logger.warning(f"Background cache refresh failed for user={user_id}: {e}")

    db_session = get_session()
    try:
        db_user = db_session.query(User).filter(User.id == user_id).first()
        scheduled_msg = db_session.query(ScheduledMessage).filter(
            ScheduledMessage.id == message_id,
            ScheduledMessage.user_id == user_id,
            ScheduledMessage.is_active == True
        ).first()
        if not db_user or not scheduled_msg:
            return

        target_groups, _ = await _resolve_target_groups_from_saved_folders(db_user, db_session)
        if not target_groups:
            target_groups = _fallback_active_user_groups(db_session, db_user)
        if target_groups:
            scheduled_msg.target_groups = json.dumps(target_groups)
            db_session.commit()
            logger.info(f"Hydrated targets for message={message_id}, groups={len(target_groups)}")
    except Exception as e:
        logger.error(f"Hydrate scheduled targets error message={message_id}: {e}")
    finally:
        db_session.close()

async def schedule_message_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle schedule message callback"""
    message = (
        "📝 <b>Xabarni Rejalashtirish</b>\n\n"
        "Iltimos, yubormoqchi bo'lgan xabar matnini kiriting.\n\n"
        "Agar matn juda katta bo'lsa, bo'lib yuboring. Oxirida:"
    )
    
    keyboard = [
        [InlineKeyboardButton("✅ Tugatish", callback_data="finish_scheduled_message_text")],
        [InlineKeyboardButton("⬅️ Orqaga", callback_data="back_to_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')
    # Accumulate multiple message parts when user sends long texts.
    context.user_data['awaiting_message_text'] = True
    context.user_data['message_text_parts'] = []

async def handle_scheduled_message_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle scheduled message text input - now with interval selection"""
    if not context.user_data.get('awaiting_message_text'):
        return
    
    new_part = update.message.text or ""
    # Store parts to support long messages (can be pasted in multiple Telegram messages).
    parts = context.user_data.get('message_text_parts')
    if parts is None:
        parts = []
    parts.append(new_part)
    context.user_data['message_text_parts'] = parts

    full_text = "\n\n".join(parts).strip()
    context.user_data['message_text'] = full_text

    # If the text is small enough AND it's the first part, keep old UX:
    # directly show interval selection.
    # If it's large or multi-part, ask user to press "Tugatish" first.
    # Telegram text limit is ~4096 chars. We keep a slightly smaller threshold to be safe.
    AUTO_THRESHOLD = 4000
    if len(parts) == 1 and len(full_text) <= AUTO_THRESHOLD:
        context.user_data['awaiting_message_text'] = False

        # Show interval selection options - ALL messages are now repeating by default
        message = (
            "📝 <b>Xabar Saqlandi!</b>\n\n"
            f"<b>Xabar:</b> {full_text[:1000]}{'...' if len(full_text) > 1000 else ''}\n\n"
            "<b>Qaysi intervalda doimiy yuborilsin?</b>\n\n"
            "⚠️ <i>Xabar foydalanuvchi to'xtatmagunicha davom etadi!</i>"
        )
        
        keyboard = [
            [InlineKeyboardButton("🕐 Har 15 daqiqada", callback_data="interval_15min")],
            [InlineKeyboardButton("🕐 Har 30 daqiqada", callback_data="interval_30min")],
            [InlineKeyboardButton("🕐 Har 1 soatda", callback_data="interval_1hour")],
            [InlineKeyboardButton("⬅️ Orqaga", callback_data="back_to_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(message, reply_markup=reply_markup, parse_mode='HTML')
        return

    # Multi-part mode: keep awaiting input until user presses "Tugatish"
    context.user_data['awaiting_message_text'] = True
    preview = full_text[:300]
    more = "..." if len(full_text) > 300 else ""

    keyboard = [
        [InlineKeyboardButton("✅ Tugatish", callback_data="finish_scheduled_message_text")],
        [InlineKeyboardButton("⬅️ Orqaga", callback_data="back_to_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"✅ Qabul qilindi! Kiritishni davom ettirishingiz mumkin.\n"
        f"Jami uzunlik: {len(full_text)} belgidan iborat.\n\n"
        f"Preview:\n{preview}{more}",
        reply_markup=reply_markup
    )

async def finish_scheduled_message_text_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User pressed 'Tugatish' after sending big/multi-part message."""
    parts = context.user_data.get('message_text_parts', [])
    if not parts:
        await update.callback_query.answer("Xabar kiritilmadi", show_alert=True)
        return

    full_text = "\n\n".join(parts).strip()
    if not full_text:
        await update.callback_query.answer("Xabar bo'sh", show_alert=True)
        return

    context.user_data['message_text'] = full_text
    context.user_data['awaiting_message_text'] = False
    context.user_data['message_text_parts'] = []

    message = (
        "📝 <b>Xabar Saqlandi!</b>\n\n"
        f"<b>Xabar:</b> {full_text[:1000]}{'...' if len(full_text) > 1000 else ''}\n\n"
        "<b>Qaysi intervalda doimiy yuborilsin?</b>\n\n"
        "⚠️ <i>Xabar foydalanuvchi to'xtatmagunicha davom etadi!</i>"
    )

    keyboard = [
        [InlineKeyboardButton("🕐 Har 15 daqiqada", callback_data="interval_15min")],
        [InlineKeyboardButton("🕐 Har 30 daqiqada", callback_data="interval_30min")],
        [InlineKeyboardButton("🕐 Har 1 soatda", callback_data="interval_1hour")],
        [InlineKeyboardButton("⬅️ Orqaga", callback_data="back_to_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.callback_query.message.reply_text(message, reply_markup=reply_markup, parse_mode='HTML')

async def handle_interval_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle interval selection for repeating messages"""
    callback_data = update.callback_query.data
    interval_map = {
        "interval_15min": (15, "daqiqa"),
        "interval_30min": (30, "daqiqa"),
        "interval_1hour": (60, "soat")
    }
    
    if callback_data not in interval_map:
        return
    
    interval_minutes, interval_name = interval_map[callback_data]
    message_text = context.user_data.get('message_text')
    
    if not message_text:
        await update.callback_query.answer("❌ Xabar matni topilmadi", show_alert=True)
        return
    
    user = update.effective_user
    db_session = get_session()
    
    try:
        try:
            await update.callback_query.answer("⏳ Faollashtirilmoqda...")
        except Exception:
            pass

        db_user = db_session.query(User).filter(User.telegram_id == str(user.id)).first()
        if db_user:
            # Create repeating message
            schedule_time = datetime.utcnow() + timedelta(minutes=interval_minutes)
            scheduled_msg = ScheduledMessage(
                user_id=db_user.id,
                message_text=message_text,
                schedule_time=schedule_time,
                is_active=True,
                is_repeat=True,  # Mark as repeating message
                repeat_interval=interval_minutes  # Repeat interval
            )
            db_session.add(scheduled_msg)
            db_session.commit()

            # Non-blocking UX: use whatever is available quickly.
            target_groups, folder_names = await _resolve_target_groups_from_saved_folders(db_user, db_session)
            if target_groups:
                scheduled_msg.target_groups = json.dumps(target_groups)
                db_session.commit()
                groups_count = len(target_groups)
                folder_preview = ", ".join(folder_names[:3]) if folder_names else "Sozlangan folderlar"
                if len(folder_names) > 3:
                    folder_preview += "..."
                source_line = f"<b>Folderlar:</b> {len(folder_names)} ta ({folder_preview})"
            else:
                # Immediate activation; hydrate targets in background.
                groups_count = len(_fallback_active_user_groups(db_session, db_user))
                source_line = "<b>Holat:</b> Guruhlar fon rejimida tayyorlanmoqda"
                _ensure_folder_cache_sync(db_user.id, db_user.phone_number, force=False)
                asyncio.create_task(
                    _hydrate_scheduled_message_targets(
                        scheduled_msg.id,
                        db_user.id,
                        db_user.phone_number
                    )
                )

            message = (
                f"✅ <b>Xabar Faollashtirildi!</b>\n\n"
                f"<b>Xabar:</b> {message_text[:50]}...\n"
                f"<b>Interval:</b> Har {interval_minutes} {interval_name}\n"
                f"{source_line}\n"
                f"<b>Jami guruhlar:</b> {groups_count} ta\n\n"
                f"⏰ Birinchi xabar {(scheduled_msg.schedule_time + timedelta(hours=5)).strftime('%H:%M')} da yuboriladi.\n"
                "Keyingi yuborishlar avtomatik davom etadi."
            )
            keyboard = [
                [InlineKeyboardButton("📅 Xabarlar Rejasiga O'tish", callback_data="message_schedule")],
                [InlineKeyboardButton("🏠 Bosh Menyu", callback_data="back_to_main")]
            ]
            await update.callback_query.message.edit_text(message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
            logger.info(f"Repeating message {scheduled_msg.id} auto-configured for user {user.id} with {len(target_groups)} groups")
        else:
            await update.callback_query.answer("❌ Foydalanuvchi topilmadi", show_alert=True)
    except Exception as e:
        logger.error(f"Xabarni saqlashda xato: {e}")
        await update.callback_query.answer("❌ Xabarni saqlashda xato", show_alert=True)
    finally:
        db_session.close()

async def set_interval_target_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set target groups for interval message"""
    callback_data = update.callback_query.data
    user = update.effective_user
    db_session = get_session()
    
    try:
        db_user = db_session.query(User).filter(User.telegram_id == str(user.id)).first()
        if not db_user:
            await update.callback_query.answer("❌ Foydalanuvchi topilmadi", show_alert=True)
            return
        
        # Get the pending message
        pending_id = context.user_data.get('pending_message_id')
        if not pending_id:
            await update.callback_query.answer("❌ Xabar topilmadi", show_alert=True)
            return
        
        scheduled_msg = db_session.query(ScheduledMessage).filter(
            ScheduledMessage.id == pending_id,
            ScheduledMessage.user_id == db_user.id
        ).first()
        
        if not scheduled_msg:
            await update.callback_query.answer("❌ Xabar topilmadi", show_alert=True)
            return
        
        if callback_data == "set_interval_all_groups":
            # Set to all groups
            user_groups = db_session.query(UserGroup).filter(
                UserGroup.user_id == db_user.id,
                UserGroup.is_active == True
            ).all()
            
            if not user_groups:
                message = "❌ Sizda faol guruhlar yo'q. Avval guruhlarni yangilang."
                keyboard = [[InlineKeyboardButton("🔄 Guruhlarni Yangilash", callback_data="refresh_groups")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')
                return
            
            import json
            scheduled_msg.target_groups = json.dumps([g.group_id for g in user_groups])
            db_session.commit()
            
            message = (
                f"✅ <b>Xabar Faollashtirildi!</b>\n\n"
                f"<b>Xabar:</b> {scheduled_msg.message_text[:50]}...\n"
                f"<b>Interval:</b> Har {scheduled_msg.repeat_interval} daqiqada\n"
                f"<b>Guruhlar:</b> Barcha guruhlar ({len(user_groups)} ta)\n\n"
                f"⏰ Birinchi xabar {(scheduled_msg.schedule_time + timedelta(hours=5)).strftime('%H:%M')} da yuboriladi.\n"
                f"Keyin har {scheduled_msg.repeat_interval} daqiqada avtomatik yuboriladi.\n\n"
                f"⚠️ <b>6 soatdan keyin xabar avtomatik o'chiriladi!</b>\n\n"
                f"<b>To'xtatish uchun:</b> 📅 Xabarlar Rejasi → 📋 Doimiy Xabarlar → Xabarni o'chirish"
            )
            
        elif callback_data == "set_interval_selected_groups":
            # Show group selection
            await show_group_selection_for_interval(update, context, db_user, db_session)
            return
        
        elif callback_data == "select_telegram_folder":
            # Show Telegram folders
            await show_telegram_folders(update, context, db_user)
            return
        
        keyboard = [
            [InlineKeyboardButton("📅 Xabarlar Rejasiga O'tish", callback_data="message_schedule")],
            [InlineKeyboardButton("🏠 Bosh Menyu", callback_data="back_to_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')
        
        # Clear pending message ID
        context.user_data.pop('pending_message_id', None)
        
    except Exception as e:
        logger.error(f"Guruhlarni sozlashda xato: {e}")
        await update.callback_query.answer("❌ Xatolik yuz berdi", show_alert=True)
    finally:
        db_session.close()

async def show_group_selection_for_interval(update: Update, context: ContextTypes.DEFAULT_TYPE, db_user=None, db_session=None):
    """Show group selection for interval message"""
    user = update.effective_user
    should_close_session = False
    
    if db_session is None:
        db_session = get_session()
        should_close_session = True
    
    try:
        if db_user is None:
            db_user = db_session.query(User).filter(User.telegram_id == str(user.id)).first()
        
        if not db_user:
            message = "❌ Foydalanuvchi topilmadi."
            keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="message_schedule")]]
            await update.callback_query.message.edit_text(message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
            return
        
        user_groups = db_session.query(UserGroup).filter(UserGroup.user_id == db_user.id).all()
        
        if not user_groups:
            message = (
                "❌ Sizning guruhlaringiz topilmadi.\n"
                "Iltimos, avval akkaunt qo'shing va guruhlarni yangilang."
            )
            keyboard = [[InlineKeyboardButton("🔄 Guruhlarni Yangilash", callback_data="refresh_groups")]]
            await update.callback_query.message.edit_text(message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
            return
        
        # Reset selected groups
        context.user_data['selected_groups'] = []
        context.user_data['selecting_for_interval'] = True
        
        message = (
            f"🎯 <b>Guruhlarni Tanlang</b>\n\n"
            f"<b>Jami guruhlar:</b> {len(user_groups)} ta\n"
            f"<b>Tanlangan guruhlar:</b> 0 ta\n\n"
            "Quyidagi guruhlardan kerakli bo'lganlarini tanlang:\n"
            "✅ - tanlangan | 🔘 - tanlanmagan\n\n"
            "<i>Tanlangan guruhlarga har intervalda xabar yuboriladi.</i>"
        )
        
        keyboard = []
        for i, group in enumerate(user_groups[:10]):
            group_title = group.group_title[:28] if group.group_title else f"Guruh {i+1}"
            callback_data = f"interval_select_group_{group.group_id}"
            keyboard.append([InlineKeyboardButton(f"🔘 {group_title}", callback_data=callback_data)])
        
        keyboard.append([InlineKeyboardButton("✅ Tanlovni Yakunlash", callback_data="finish_interval_group_selection")])
        keyboard.append([InlineKeyboardButton("⬅️ Orqaga", callback_data="message_schedule")])
        
        context.user_data['available_groups'] = {g.group_id: g.group_title for g in user_groups}
        
        await update.callback_query.message.edit_text(message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
        
    except Exception as e:
        logger.error(f"Guruh tanlash interfeysida xato: {e}")
        message = "❌ Xatolik yuz berdi."
        keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="message_schedule")]]
        await update.callback_query.message.edit_text(message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    finally:
        if should_close_session:
            db_session.close()

async def handle_interval_group_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle group selection for interval message"""
    callback_data = update.callback_query.data
    group_id = callback_data.split('_')[-1]
    
    selected_groups = context.user_data.get('selected_groups', [])
    available_groups = context.user_data.get('available_groups', {})
    
    # Toggle group selection
    if group_id in selected_groups:
        selected_groups.remove(group_id)
    else:
        selected_groups.append(group_id)
    
    context.user_data['selected_groups'] = selected_groups
    
    # Build updated message
    selected_count = len(selected_groups)
    message = (
        f"🎯 <b>Guruhlarni Tanlang</b>\n\n"
        f"<b>Jami guruhlar:</b> {len(available_groups)} ta\n"
        f"<b>Tanlangan guruhlar:</b> {selected_count} ta\n\n"
        "Quyidagi guruhlardan kerakli bo'lganlarini tanlang:\n"
        "✅ - tanlangan | 🔘 - tanlanmagan"
    )
    
    # Rebuild keyboard
    keyboard = []
    for gid, group_title in available_groups.items():
        if gid in selected_groups:
            button_text = f"✅ {group_title[:28]}"
        else:
            button_text = f"🔘 {group_title[:28]}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"interval_select_group_{gid}")])
    
    keyboard.append([InlineKeyboardButton("✅ Tanlovni Yakunlash", callback_data="finish_interval_group_selection")])
    keyboard.append([InlineKeyboardButton("⬅️ Orqaga", callback_data="message_schedule")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')

async def finish_interval_group_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Finish group selection for interval message"""
    selected_groups = context.user_data.get('selected_groups', [])
    pending_id = context.user_data.get('pending_message_id')
    
    if not selected_groups:
        await update.callback_query.answer("❌ Kamida bitta guruh tanlang!", show_alert=True)
        return
    
    if not pending_id:
        await update.callback_query.answer("❌ Xabar topilmadi", show_alert=True)
        return
    
    user = update.effective_user
    db_session = get_session()
    
    try:
        db_user = db_session.query(User).filter(User.telegram_id == str(user.id)).first()
        if not db_user:
            await update.callback_query.answer("❌ Foydalanuvchi topilmadi", show_alert=True)
            return
        
        scheduled_msg = db_session.query(ScheduledMessage).filter(
            ScheduledMessage.id == pending_id,
            ScheduledMessage.user_id == db_user.id
        ).first()
        
        if not scheduled_msg:
            await update.callback_query.answer("❌ Xabar topilmadi", show_alert=True)
            return
        
        # Save selected groups
        import json
        scheduled_msg.target_groups = json.dumps(selected_groups)
        db_session.commit()
        
        message = (
            f"✅ <b>Xabar Faollashtirildi!</b>\n\n"
            f"<b>Xabar:</b> {scheduled_msg.message_text[:50]}...\n"
            f"<b>Interval:</b> Har {scheduled_msg.repeat_interval} daqiqada\n"
            f"<b>Guruhlar:</b> {len(selected_groups)} ta tanlangan\n\n"
            f"⏰ Birinchi xabar {(scheduled_msg.schedule_time + timedelta(hours=5)).strftime('%H:%M')} da yuboriladi.\n"
            f"Keyin har {scheduled_msg.repeat_interval} daqiqada avtomatik yuboriladi.\n\n"
            f"⚠️ <b>6 soatdan keyin xabar avtomatik o'chiriladi!</b>\n\n"
            f"<b>To'xtatish uchun:</b> 📅 Xabarlar Rejasi → 📋 Doimiy Xabarlar → Xabarni o'chirish"
        )
        
        keyboard = [
            [InlineKeyboardButton("📅 Xabarlar Rejasiga O'tish", callback_data="message_schedule")],
            [InlineKeyboardButton("🏠 Bosh Menyu", callback_data="back_to_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')
        
        # Clear context data
        context.user_data.pop('pending_message_id', None)
        context.user_data.pop('selected_groups', None)
        context.user_data.pop('available_groups', None)
        context.user_data.pop('selecting_for_interval', None)
        
        logger.info(f"Interval message {pending_id} configured with {len(selected_groups)} groups")
        
    except Exception as e:
        logger.error(f"Guruhlarni saqlashda xato: {e}")
        await update.callback_query.answer("❌ Xatolik yuz berdi", show_alert=True)
    finally:
        db_session.close()

async def show_telegram_folders(update: Update, context: ContextTypes.DEFAULT_TYPE, db_user):
    """Show Telegram folders for user to select"""
    user = update.effective_user
    
    # Get phone number
    phone_number = db_user.phone_number
    if not phone_number:
        message = (
            "❌ <b>Telefon raqam topilmadi</b>\n\n"
            "Telegram folderlarini ko'rish uchun avval akkauntingizni ulang."
        )
        keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="message_schedule")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')
        return
    
    # Show loading message
    await update.callback_query.message.edit_text("🔄 Telegram folderlari yuklanmoqda...")
    
    try:
        # Get folders from Telegram
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"Fetching folders for phone: {phone_number}")
        
        folders = await verifier.get_user_folders(phone_number)
        logger.info(f"Folders result: {folders}")
        
        if not folders:
            message = (
                "📁 <b>Telegram Folderlari</b>\n\n"
                "Sizda Telegram folderlari topilmadi.\n\n"
                "Folder yaratish uchun:\n"
                "1. Telegram oching\n"
                "2. Sozlamalar → Papkalar\n"
                "3. Yangi folder yarating\n\n"
                "Yoki quyidagi variantlardan foydalaning:"
            )
            keyboard = [
                [InlineKeyboardButton("📢 Barcha Guruhlarga", callback_data="set_interval_all_groups")],
                [InlineKeyboardButton("🎯 Guruhlarni Tanlash", callback_data="set_interval_selected_groups")],
                [InlineKeyboardButton("⬅️ Orqaga", callback_data="message_schedule")]
            ]
        else:
            message = (
                f"📁 <b>Telegram Folderlari</b>\n\n"
                f"{len(folders)} ta folder topildi:\n\n"
                f"<b>Ko'p tanlash:</b> Bir nechta folder tanlang (3-4 ta)\n"
                f"Tanlangan folderlardagi barcha guruhlarga yuboriladi.\n\n"
                f"Folderlarni tanlang:"
            )
            
            # Initialize selected folders list
            if 'selected_folders' not in context.user_data:
                context.user_data['selected_folders'] = []
            
            import re
            keyboard = []
            for folder in folders:
                # Ensure title is a string
                folder_title = folder.get('title', f"Folder {folder['id']}")
                if not isinstance(folder_title, str):
                    folder_title = str(folder_title)
                folder_name = folder_title[:25]
                # Some sources may append counts like "Shaxsiy (10)" to the title.
                # We strip a trailing "(number)" so UI shows only the real folder name.
                folder_name = re.sub(r"\s*\(\d+\)\s*$", "", folder_name)
                group_count = len(folder.get('groups', []))
                folder_id = str(folder['id'])
                
                # Check if folder is selected
                is_selected = folder_id in context.user_data['selected_folders']
                checkmark = "✅" if is_selected else "⬜"
                
                # Do not show group_count in the folder button title.
                button_text = f"{checkmark} {folder_name}"
                callback_data = f"toggle_folder_{folder_id}"
                keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
            
            # Store folders in context for later use
            context.user_data['telegram_folders'] = {str(f['id']): f for f in folders}
            
            # Add action buttons
            selected_count = len(context.user_data.get('selected_folders', []))
            keyboard.append([InlineKeyboardButton(f"🚀 Yuborish ({selected_count} ta)", callback_data="send_multi_folders")])
            keyboard.append([InlineKeyboardButton("🔄 Tanlashni Tozalash", callback_data="clear_folder_selection")])
            keyboard.append([InlineKeyboardButton("📢 Barcha Guruhlarga", callback_data="set_interval_all_groups")])
            keyboard.append([InlineKeyboardButton("⬅️ Orqaga", callback_data="message_schedule")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')
        
    except Exception as e:
        logger.error(f"Folderlarni olishda xato: {e}")
        message = (
            "❌ <b>Folderlarni olishda xato</b>\n\n"
            "Iltimos, keyinroq qayta urinib ko'ring."
        )
        keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="message_schedule")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')

async def handle_folder_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle folder toggle (select/deselect)"""
    callback_data = update.callback_query.data
    folder_id = callback_data.split('_')[-1]
    
    # Initialize selected folders if not exists
    if 'selected_folders' not in context.user_data:
        context.user_data['selected_folders'] = []
    
    selected_folders = context.user_data['selected_folders']
    
    # Toggle folder selection
    if folder_id in selected_folders:
        selected_folders.remove(folder_id)
        await update.callback_query.answer("❌ Folder olib tashlandi")
    else:
        # Limit to 4 folders max
        if len(selected_folders) >= 4:
            await update.callback_query.answer("⚠️ Maksimum 4 ta folder tanlash mumkin!", show_alert=True)
            return
        selected_folders.append(folder_id)
        await update.callback_query.answer("✅ Folder tanlandi")
    
    # Refresh folder list
    db_session = get_session()
    try:
        db_user = db_session.query(User).filter(User.telegram_id == str(update.effective_user.id)).first()
        if db_user:
            await show_telegram_folders(update, context, db_user)
    finally:
        db_session.close()

async def clear_folder_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear all selected folders"""
    context.user_data['selected_folders'] = []
    await update.callback_query.answer("🔄 Tanlash tozalandi")
    
    # Refresh folder list
    db_session = get_session()
    try:
        db_user = db_session.query(User).filter(User.telegram_id == str(update.effective_user.id)).first()
        if db_user:
            await show_telegram_folders(update, context, db_user)
    finally:
        db_session.close()

async def send_multi_folders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send message to multiple selected folders"""
    selected_folder_ids = context.user_data.get('selected_folders', [])
    
    if not selected_folder_ids:
        await update.callback_query.answer("❌ Hech qanday folder tanlanmagan!", show_alert=True)
        return
    
    pending_id = context.user_data.get('pending_message_id')
    if not pending_id:
        await update.callback_query.answer("❌ Xabar topilmadi", show_alert=True)
        return
    
    # Get folder data
    folders = context.user_data.get('telegram_folders', {})
    
    # Collect all group IDs from selected folders
    all_group_ids = []
    folder_names = []
    
    for folder_id in selected_folder_ids:
        folder = folders.get(folder_id)
        if folder:
            group_ids = [g['id'] for g in folder.get('groups', [])]
            all_group_ids.extend(group_ids)
            
            folder_title = folder.get('title', f"Folder {folder_id}")
            if not isinstance(folder_title, str):
                folder_title = str(folder_title)
            folder_names.append(folder_title)
    
    # Remove duplicates while preserving order
    seen = set()
    unique_group_ids = []
    for gid in all_group_ids:
        if gid not in seen:
            seen.add(gid)
            unique_group_ids.append(gid)
    
    if not unique_group_ids:
        await update.callback_query.answer("❌ Tanlangan folderlarda guruhlar yo'q!", show_alert=True)
        return
    
    user = update.effective_user
    db_session = get_session()
    
    try:
        db_user = db_session.query(User).filter(User.telegram_id == str(user.id)).first()
        if not db_user:
            await update.callback_query.answer("❌ Foydalanuvchi topilmadi", show_alert=True)
            return
        
        scheduled_msg = db_session.query(ScheduledMessage).filter(
            ScheduledMessage.id == pending_id,
            ScheduledMessage.user_id == db_user.id
        ).first()
        
        if not scheduled_msg:
            await update.callback_query.answer("❌ Xabar topilmadi", show_alert=True)
            return
        
        # Save target groups
        import json
        scheduled_msg.target_groups = json.dumps(unique_group_ids)
        db_session.commit()
        
        # Create folder list text
        folders_text = "\n".join([f"  • {name[:20]}" for name in folder_names[:4]])
        
        message = (
            f"✅ <b>Xabar Faollashtirildi!</b>\n\n"
            f"<b>Xabar:</b> {scheduled_msg.message_text[:50]}...\n"
            f"<b>Interval:</b> Har {scheduled_msg.repeat_interval} daqiqada\n"
            f"<b>Folderlar ({len(folder_names)} ta):</b>\n{folders_text}\n"
            f"<b>Jami guruhlar:</b> {len(unique_group_ids)} ta\n\n"
            f"⏰ Birinchi xabar {(scheduled_msg.schedule_time + timedelta(hours=5)).strftime('%H:%M')} da yuboriladi.\n"
            f"Keyin har {scheduled_msg.repeat_interval} daqiqada avtomatik yuboriladi.\n\n"
            f"⚠️ <b>6 soatdan keyin xabar avtomatik o'chiriladi!</b>\n\n"
            f"<b>To'xtatish uchun:</b> 📅 Xabarlar Rejasi → 📋 Doimiy Xabarlar → Xabarni o'chirish"
        )
        
        keyboard = [
            [InlineKeyboardButton("📅 Xabarlar Rejasiga O'tish", callback_data="message_schedule")],
            [InlineKeyboardButton("🏠 Bosh Menyu", callback_data="back_to_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')
        
        # NOTE: We keep selected_folders for reuse on next visit
        # Only clear when user explicitly clicks "Tanlashni Tozalash"
        
        try:
            logger.info(f"Message {pending_id} configured with {len(folder_names)} folders ({len(unique_group_ids)} groups)")
        except UnicodeEncodeError:
            logger.info(f"Message {pending_id} configured with multiple folders")
        
    except Exception as e:
        logger.error(f"Multi-folder tanlashda xato: {e}")
        await update.callback_query.answer("❌ Xatolik yuz berdi", show_alert=True)
    finally:
        db_session.close()

async def handle_folder_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle Telegram folder selection (single folder - for backward compatibility)"""
    callback_data = update.callback_query.data
    folder_id = callback_data.split('_')[-1]
    
    pending_id = context.user_data.get('pending_message_id')
    if not pending_id:
        await update.callback_query.answer("❌ Xabar topilmadi", show_alert=True)
        return
    
    # Get folder data
    folders = context.user_data.get('telegram_folders', {})
    folder = folders.get(folder_id)
    
    if not folder:
        await update.callback_query.answer("❌ Folder topilmadi", show_alert=True)
        return
    
    user = update.effective_user
    db_session = get_session()
    
    try:
        db_user = db_session.query(User).filter(User.telegram_id == str(user.id)).first()
        if not db_user:
            await update.callback_query.answer("❌ Foydalanuvchi topilmadi", show_alert=True)
            return
        
        scheduled_msg = db_session.query(ScheduledMessage).filter(
            ScheduledMessage.id == pending_id,
            ScheduledMessage.user_id == db_user.id
        ).first()
        
        if not scheduled_msg:
            await update.callback_query.answer("❌ Xabar topilmadi", show_alert=True)
            return
        
        # Get group IDs from folder
        group_ids = [g['id'] for g in folder['groups']]
        
        if not group_ids:
            message = "❌ Tanlangan folderda guruhlar yo'q."
            keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="select_telegram_folder")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')
            return
        
        # Save target groups
        import json
        scheduled_msg.target_groups = json.dumps(group_ids)
        db_session.commit()
        
        # Ensure folder title is string
        folder_title = folder.get('title', 'Noma\'lum')
        if not isinstance(folder_title, str):
            folder_title = str(folder_title)
        
        message = (
            f"✅ <b>Xabar Faollashtirildi!</b>\n\n"
            f"<b>Xabar:</b> {scheduled_msg.message_text[:50]}...\n"
            f"<b>Interval:</b> Har {scheduled_msg.repeat_interval} daqiqada\n"
            f"<b>Folder:</b> {folder_title}\n"
            f"<b>Guruhlar:</b> {len(group_ids)} ta\n\n"
            f"⏰ Birinchi xabar {(scheduled_msg.schedule_time + timedelta(hours=5)).strftime('%H:%M')} da yuboriladi.\n"
            f"Keyin har {scheduled_msg.repeat_interval} daqiqada avtomatik yuboriladi.\n\n"
            f"⚠️ <b>6 soatdan keyin xabar avtomatik o'chiriladi!</b>\n\n"
            f"<b>To'xtatish uchun:</b> 📅 Xabarlar Rejasi → 📋 Doimiy Xabarlar → Xabarni o'chirish"
        )
        
        keyboard = [
            [InlineKeyboardButton("📅 Xabarlar Rejasiga O'tish", callback_data="message_schedule")],
            [InlineKeyboardButton("🏠 Bosh Menyu", callback_data="back_to_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')
        
        # Clear context data
        context.user_data.pop('pending_message_id', None)
        context.user_data.pop('telegram_folders', None)
        
        try:
            logger.info(f"Message {pending_id} configured with folder {folder_title} ({len(group_ids)} groups)")
        except UnicodeEncodeError:
            logger.info(f"Message {pending_id} configured with folder [Unicode] ({len(group_ids)} groups)")
        
    except Exception as e:
        logger.error(f"Folder tanlashda xato: {e}")
        await update.callback_query.answer("❌ Xatolik yuz berdi", show_alert=True)
    finally:
        db_session.close()

async def handle_schedule_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle schedule time input - DEPRECATED: All messages are now repeating by default"""
    # This function is kept for backward compatibility but should not be used
    # All new messages are created as repeating messages via handle_interval_selection
    await update.message.reply_text(
        "⚠️ <b>Bu funksiya eski</b>\n\n"
        "Yangi xabarlar avtomatik ravishda doimiy (repeating) rejimda yaratiladi.\n"
        "Iltimos, 'Xabarlar Rejasi' bo'limidan foydalaning.",
        parse_mode='HTML'
    )

async def send_message_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle send message callback"""
    message = (
        "📤 <b>Xabar Yuborish</b>\n\n"
        "Quyidagi variantlardan birini tanlang:"
    )
    
    keyboard = [
        [InlineKeyboardButton("📁 Folderni Sozlash", callback_data="configure_send_folders")],
        [InlineKeyboardButton("📢 Barcha Guruhlarga", callback_data="send_all_groups")],
        [InlineKeyboardButton("🎯 Tanlangan Guruhlarga", callback_data="send_selected_groups")],
        [InlineKeyboardButton("⬅️ Orqaga", callback_data="back_to_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')


async def configure_send_folders_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Open folder config UI for scheduled messages."""
    user = update.effective_user
    db_session = get_session()
    try:
        db_user = db_session.query(User).filter(User.telegram_id == str(user.id)).first()
        if not db_user or not db_user.phone_number:
            message = (
                "❌ <b>Telefon raqam topilmadi</b>\n\n"
                "Avval akkauntingizni ulang, keyin folderlarni sozlang."
            )
            keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="send_message")]]
            await update.callback_query.message.edit_text(message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
            return

        await update.callback_query.message.edit_text("🔄 Folder nomlari yuklanmoqda...")
        folders = _load_folder_name_cache(db_session, db_user.id)
        if not folders:
            folders = await verifier.get_user_folder_names(db_user.phone_number)

        if not folders:
            message = "❌ Telegram folderlari topilmadi."
            keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="send_message")]]
            await update.callback_query.message.edit_text(message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
            return

        config = _load_schedule_folder_config(db_session, db_user.id)
        selected = set(config.get("selected_folder_ids", []))
        context.user_data["config_telegram_folders"] = {str(f["id"]): {"id": str(f["id"]), "title": f.get("title", f"Folder {f['id']}")} for f in folders}
        context.user_data["config_selected_folder_ids"] = [fid for fid in selected if fid in context.user_data["config_telegram_folders"]]
        await _render_config_folder_picker(update, context, edit_message=True)
    except Exception as e:
        logger.error(f"Folder sozlashni ochishda xato: {e}")
        await update.callback_query.message.edit_text(
            "❌ Folder sozlashni ochib bo'lmadi.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Orqaga", callback_data="send_message")]]),
            parse_mode='HTML'
        )
    finally:
        db_session.close()


async def _render_config_folder_picker(update: Update, context: ContextTypes.DEFAULT_TYPE, edit_message=False):
    folders = context.user_data.get("config_telegram_folders", {})
    selected_ids = set(context.user_data.get("config_selected_folder_ids", []))

    if not folders:
        text = "❌ Folderlar topilmadi."
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Orqaga", callback_data="send_message")]])
    else:
        text = (
            "📁 <b>Folderni Sozlash</b>\n\n"
            "Xabar yuboriladigan folderlarni tanlang:\n"
            "✅ tanlangan | ⬜ tanlanmagan\n\n"
            f"<b>Tanlangan:</b> {len(selected_ids)} ta"
        )
        keyboard = []
        for folder_id, folder in list(folders.items())[:40]:
            title = folder.get("title", f"Folder {folder_id}")
            if not isinstance(title, str):
                title = str(title)
            prefix = "✅" if folder_id in selected_ids else "⬜"
            keyboard.append([InlineKeyboardButton(f"{prefix} {title[:30]}", callback_data=f"config_folder_toggle_{folder_id}")])
        keyboard.append([InlineKeyboardButton("💾 Saqlash", callback_data="config_folder_save")])
        keyboard.append([InlineKeyboardButton("🔄 Tarkibni Yangilash", callback_data="config_folder_sync")])
        keyboard.append([InlineKeyboardButton("🔄 Tozalash", callback_data="config_folder_clear")])
        keyboard.append([InlineKeyboardButton("⬅️ Orqaga", callback_data="send_message")])
        markup = InlineKeyboardMarkup(keyboard)

    if edit_message:
        await update.callback_query.message.edit_text(text, reply_markup=markup, parse_mode='HTML')
    else:
        await update.callback_query.edit_message_text(text, reply_markup=markup, parse_mode='HTML')


async def config_folder_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    folder_id = update.callback_query.data.replace("config_folder_toggle_", "")
    selected = context.user_data.get("config_selected_folder_ids", [])
    if folder_id in selected:
        selected.remove(folder_id)
    else:
        selected.append(folder_id)
    context.user_data["config_selected_folder_ids"] = selected
    await _render_config_folder_picker(update, context)


async def config_folder_clear_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["config_selected_folder_ids"] = []
    await _render_config_folder_picker(update, context)


async def config_folder_save_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    selected_ids = context.user_data.get("config_selected_folder_ids", [])
    folder_map = context.user_data.get("config_telegram_folders", {})
    if not selected_ids:
        await update.callback_query.answer("Kamida bitta folder tanlang", show_alert=True)
        return

    user = update.effective_user
    db_session = get_session()
    try:
        db_user = db_session.query(User).filter(User.telegram_id == str(user.id)).first()
        if not db_user:
            await update.callback_query.answer("Foydalanuvchi topilmadi", show_alert=True)
            return

        selected_titles = []
        for folder_id in selected_ids:
            folder = folder_map.get(folder_id)
            if not folder:
                continue
            title = folder.get("title", f"Folder {folder_id}")
            if not isinstance(title, str):
                title = str(title)
            selected_titles.append(title)

        # Derive group IDs from cache snapshot if available.
        cache_row = db_session.query(GroupFolder).filter(
            GroupFolder.user_id == db_user.id,
            GroupFolder.folder_name == FOLDER_CACHE_NAME
        ).first()
        cached_group_ids = []
        if cache_row and cache_row.group_ids:
            try:
                cache_payload = json.loads(cache_row.group_ids)
                cache_map = {str(f.get("id")): f for f in cache_payload.get("folders", [])}
                for folder_id in selected_ids:
                    folder = cache_map.get(str(folder_id), {})
                    cached_group_ids.extend([str(gid) for gid in folder.get("group_ids", []) if str(gid)])
            except Exception:
                cached_group_ids = []
        unique_group_ids = list(dict.fromkeys(cached_group_ids))

        payload = {
            "selected_folder_ids": [str(fid) for fid in selected_ids],
            "selected_folder_titles": selected_titles,
            "cached_group_ids": unique_group_ids,
            "saved_at": datetime.utcnow().isoformat()
        }
        _save_schedule_folder_config(db_session, db_user.id, payload)

        message = (
            "✅ <b>Folderlar saqlandi!</b>\n\n"
            f"<b>Folderlar:</b> {len(selected_titles)} ta\n"
            f"<b>Cache guruhlar:</b> {len(unique_group_ids)} ta\n\n"
            "Folder nomlari saqlandi. Kerak bo'lsa `Tarkibni Yangilash` bilan cache yangilang.\n"
            "`Xabar Rejalashtirish` cache bo'sh bo'lsa ham to'xtab qolmaydi."
        )
        keyboard = [
            [InlineKeyboardButton("📁 Folder Sozlash", callback_data="send_message")],
            [InlineKeyboardButton("🏠 Bosh Menyu", callback_data="back_to_main")]
        ]
        await update.callback_query.message.edit_text(message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    except Exception as e:
        logger.error(f"Folder sozlamasini saqlashda xato: {e}")
        await update.callback_query.answer("Saqlashda xatolik", show_alert=True)
    finally:
        db_session.close()


async def config_folder_sync_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manual trigger for full folder-group cache refresh."""
    user = update.effective_user
    db_session = get_session()
    try:
        db_user = db_session.query(User).filter(User.telegram_id == str(user.id)).first()
        if not db_user or not db_user.phone_number:
            await update.callback_query.answer("Akkaunt topilmadi", show_alert=True)
            return
        started = _ensure_folder_cache_sync(db_user.id, db_user.phone_number, force=False)
        if started:
            await update.callback_query.answer("Yangilash boshlandi", show_alert=False)
        else:
            await update.callback_query.answer("Yangilash allaqachon ketmoqda", show_alert=False)
    except Exception as e:
        logger.error(f"Manual folder sync error: {e}")
        await update.callback_query.answer("Yangilashda xatolik", show_alert=True)
    finally:
        db_session.close()

async def send_all_groups_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle send to all groups callback"""
    user = update.effective_user
    db_session = get_session()
    
    try:
        # Get user's groups
        db_user = db_session.query(User).filter(User.telegram_id == str(user.id)).first()
        if db_user:
            user_groups = db_session.query(UserGroup).filter(UserGroup.user_id == db_user.id).all()
            
            if not user_groups:
                message = (
                    "❌ Sizning guruhlaringiz topilmadi.\n"
                    "Iltimos, avval akkaunt qo'shing va guruhlarni yangilang."
                )
                keyboard = [[InlineKeyboardButton("🔄 Guruhlarni Yangilash", callback_data="refresh_groups")]]
            else:
                message = (
                    f"📢 <b>Barcha Guruhlarga Xabar Yuborish</b>\n\n"
                    f"Guruhlar soni: {len(user_groups)}\n\n"
                    "Iltimos, yubormoqchi bo'lgan xabar matnini kiriting:"
                )
                keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="send_message")]]
                
                # Store that user wants to send to all groups
                context.user_data['send_to_all_groups'] = True
                context.user_data['target_groups'] = [g.group_id for g in user_groups]
            
        else:
            message = "❌ Foydalanuvchi topilmadi."
            keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="send_message")]]
            
    except Exception as e:
        logger.error(f"Barcha guruhlarga xabar yuborishda xato: {e}")
        message = "❌ Xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring."
        keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="send_message")]]
    finally:
        db_session.close()
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')
    context.user_data['awaiting_message_text'] = True

async def send_selected_groups_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle send to selected groups callback - shows folders first if any exist"""
    user = update.effective_user
    db_session = get_session()
    
    try:
        # Get user's groups
        db_user = db_session.query(User).filter(User.telegram_id == str(user.id)).first()
        if db_user:
            # Check if user has any saved folders
            from src.models.database import GroupFolder
            saved_folders = db_session.query(GroupFolder).filter(GroupFolder.user_id == db_user.id).all()
            
            if saved_folders:
                # Show folders first
                message = (
                    "📁 <b>Saqlangan Guruh Jildlari</b>\n\n"
                    "Avval saqlangan guruh tanlovlaringiz:\n"
                    "Yoki yangi tanlov qilish uchun 'Yangi Tanlov' tugmasini bosing."
                )
                
                keyboard = []
                for folder in saved_folders:
                    folder_name = folder.folder_name[:30]
                    keyboard.append([InlineKeyboardButton(f"📁 {folder_name}", callback_data=f"use_folder_{folder.id}")])
                
                keyboard.append([InlineKeyboardButton("➕ Yangi Tanlov", callback_data="new_group_selection")])
                keyboard.append([InlineKeyboardButton("⬅️ Orqaga", callback_data="send_message")])
                
                await update.callback_query.message.edit_text(message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
                return
            
            # No folders, proceed to group selection
            await show_group_selection(update, context, db_user, db_session)
            return
            
        else:
            message = "❌ Foydalanuvchi topilmadi."
            keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="send_message")]]
            
    except Exception as e:
        logger.error(f"Tanlangan guruhlarga xabar yuborishda xato: {e}")
        message = "❌ Xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring."
        keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="send_message")]]
    finally:
        db_session.close()
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')

async def show_group_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, db_user=None, db_session=None):
    """Show group selection interface"""
    user = update.effective_user
    should_close_session = False
    
    if db_session is None:
        db_session = get_session()
        should_close_session = True
    
    try:
        if db_user is None:
            db_user = db_session.query(User).filter(User.telegram_id == str(user.id)).first()
        
        if not db_user:
            message = "❌ Foydalanuvchi topilmadi."
            keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="send_message")]]
            await update.callback_query.message.edit_text(message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
            return
        
        user_groups = db_session.query(UserGroup).filter(UserGroup.user_id == db_user.id).all()
        
        if not user_groups:
            message = (
                "❌ Sizning guruhlaringiz topilmadi.\n"
                "Iltimos, avval akkaunt qo'shing va guruhlarni yangilang."
            )
            keyboard = [[InlineKeyboardButton("🔄 Guruhlarni Yangilash", callback_data="refresh_groups")]]
            await update.callback_query.message.edit_text(message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
            return
        
        # Reset selected groups when showing fresh selection
        context.user_data['selected_groups'] = []
        
        # Create group selection interface
        message = (
            f"🎯 <b>Tanlangan Guruhlarga Xabar Yuborish</b>\n\n"
            f"<b>Jami guruhlar:</b> {len(user_groups)} ta\n"
            f"<b>Tanlangan guruhlar:</b> 0 ta\n\n"
            "Quyidagi guruhlardan kerakli bo'lganlarini tanlang:\n"
            "✅ - tanlangan | 🔘 - tanlanmagan\n\n"
            "<i>Eslatma: Faqat tanlagan guruhlaringizga xabar yuboriladi!</i>"
        )
        
        # Create keyboard with group selection
        keyboard = []
        for i, group in enumerate(user_groups[:10]):  # Show first 10 groups
            group_title = group.group_title[:28] if group.group_title else f"Guruh {i+1}"
            callback_data = f"select_group_{group.group_id}"
            keyboard.append([InlineKeyboardButton(f"🔘 {group_title}", callback_data=callback_data)])
        
        keyboard.append([InlineKeyboardButton("💾 Jild sifatida saqlash", callback_data="save_as_folder")])
        keyboard.append([InlineKeyboardButton("✅ Tanlovni Yakunlash", callback_data="finish_group_selection")])
        keyboard.append([InlineKeyboardButton("⬅️ Orqaga", callback_data="send_message")])
        
        # Store available groups
        context.user_data['available_groups'] = {g.group_id: g.group_title for g in user_groups}
        
        logger.info(f"Group selection shown. Available groups: {len(user_groups)}, Selected: 0")
        
        await update.callback_query.message.edit_text(message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
        
    except Exception as e:
        logger.error(f"Guruh tanlash interfeysida xato: {e}")
        message = "❌ Xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring."
        keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="send_message")]]
        await update.callback_query.message.edit_text(message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    finally:
        if should_close_session:
            db_session.close()

async def select_group_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle group selection callback - properly tracks selected groups"""
    callback_data = update.callback_query.data
    group_id = callback_data.split('_')[-1]
    
    # Get current selection from context
    selected_groups = context.user_data.get('selected_groups', [])
    available_groups = context.user_data.get('available_groups', {})
    
    # Toggle group selection
    if group_id in selected_groups:
        selected_groups.remove(group_id)
        logger.info(f"Group {group_id} deselected. Current selection: {selected_groups}")
    else:
        selected_groups.append(group_id)
        logger.info(f"Group {group_id} selected. Current selection: {selected_groups}")
    
    # Update context
    context.user_data['selected_groups'] = selected_groups
    
    # Build updated message
    selected_count = len(selected_groups)
    message = (
        f"🎯 <b>Tanlangan Guruhlarga Xabar Yuborish</b>\n\n"
        f"<b>Tanlangan guruhlar:</b> {selected_count} ta\n\n"
        "Quyidagi guruhlardan kerakli bo'lganlarini tanlang:\n"
        "✅ - tanlangan | 🔘 - tanlanmagan"
    )
    
    # Rebuild keyboard with updated selection status
    keyboard = []
    for gid, group_title in available_groups.items():
        if gid in selected_groups:
            button_text = f"✅ {group_title[:28]}"
        else:
            button_text = f"🔘 {group_title[:28]}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"select_group_{gid}")])
    
    keyboard.append([InlineKeyboardButton("💾 Jild sifatida saqlash", callback_data="save_as_folder")])
    keyboard.append([InlineKeyboardButton("✅ Tanlovni Yakunlash", callback_data="finish_group_selection")])
    keyboard.append([InlineKeyboardButton("⬅️ Orqaga", callback_data="send_message")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')

async def finish_group_selection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle finish group selection callback"""
    selected_groups = context.user_data.get('selected_groups', [])
    
    if not selected_groups:
        message = (
            "❌ Hech qanday guruh tanlanmadi.\n"
            "Iltimos, avval guruhlarni tanlang."
        )
        keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="send_message")]]
    else:
        # Show list of scheduled messages to choose from
        user = update.effective_user
        db_session = get_session()
        try:
            db_user = db_session.query(User).filter(User.telegram_id == str(user.id)).first()
            if db_user:
                scheduled_msgs = db_session.query(ScheduledMessage).filter(
                    ScheduledMessage.user_id == db_user.id,
                    ScheduledMessage.is_active == True
                ).all()
                
                if not scheduled_msgs:
                    # No scheduled messages - show warning and redirect to message schedule
                    message = (
                        "⚠️ <b>Xabarlar Rejasi Bo'sh!</b>\n\n"
                        "Sizda rejalashtirilgan xabarlar yo'q.\n\n"
                        "Xabar yuborish uchun avval 'Xabarlar Rejasi' bo'limida xabar tayyorlab oling."
                    )
                    keyboard = [
                        [InlineKeyboardButton("📅 Xabarlar Rejasiga O'tish", callback_data="message_schedule")],
                        [InlineKeyboardButton("🏠 Bosh Menyu", callback_data="back_to_main")]
                    ]
                else:
                    message = (
                        f"🎯 <b>Tanlangan Guruhlarga Xabar Yuborish</b>\n\n"
                        f"Tanlangan guruhlar: {len(selected_groups)}\n\n"
                        "Quyidagi rejalashtirilgan xabarlardan birini tanlang:"
                    )
                    
                    # Create keyboard with scheduled messages
                    keyboard = []
                    for i, msg in enumerate(scheduled_msgs[:5]):  # Show first 5 messages
                        button_text = f"📝 {msg.message_text[:20]}..."
                        callback_data = f"use_scheduled_{msg.id}"
                        keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
                    
                    keyboard.append([InlineKeyboardButton("🆕 Yangi Xabar", callback_data="new_message")])
                    keyboard.append([InlineKeyboardButton("⬅️ Orqaga", callback_data="send_message")])
                    
                    # Store selected groups
                    context.user_data['selected_groups'] = selected_groups
                    
            else:
                message = "❌ Foydalanuvchi topilmadi."
                keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="send_message")]]
                
        except Exception as e:
            logger.error(f"Rejalashtirilgan xabarlarni olishda xato: {e}")
            message = "❌ Xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring."
            keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="send_message")]]
        finally:
            db_session.close()
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')

async def use_scheduled_message_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle use scheduled message callback"""
    callback_data = update.callback_query.data
    message_id = int(callback_data.split('_')[-1])
    
    user = update.effective_user
    db_session = get_session()
    
    try:
        # Get scheduled message
        scheduled_msg = db_session.query(ScheduledMessage).filter(ScheduledMessage.id == message_id).first()
        if scheduled_msg:
            # Get selected groups
            selected_groups = context.user_data.get('selected_groups', [])
            if not selected_groups:
                await update.callback_query.answer("❌ Guruhlar tanlanmadi", show_alert=True)
                return
            
            # Try to get phone number from session data first
            phone_number = context.user_data.get('phone_number')
            
            # If not in session, try to get from database
            if not phone_number:
                db_user = db_session.query(User).filter(User.telegram_id == str(user.id)).first()
                if db_user and db_user.phone_number:
                    phone_number = db_user.phone_number
                    # Save to session for future use
                    context.user_data['phone_number'] = phone_number
                    from src.models.database import save_user_session
                    save_user_session(user.id, context.user_data)
                else:
                    # Try to get from user session data in database
                    from src.models.database import load_user_session
                    saved_session = load_user_session(user.id)
                    if saved_session and 'phone_number' in saved_session:
                        phone_number = saved_session['phone_number']
                        context.user_data['phone_number'] = phone_number
                        save_user_session(user.id, context.user_data)
            
            # If still no phone number, show error with guidance
            if not phone_number:
                message = (
                    "❌ <b>Telefon Raqam Topilmadi</b>\n\n"
                    "Xabar yuborish uchun telefon raqamingiz kerak.\n\n"
                    "<b>Bu muammoni hal qilish uchun:</b>\n"
                    "1. 🔁 Akkauntni qayta ulang\n"
                    "2. 📱 Yangi akkaunt qo'shing\n"
                    "3. ✅ Guruhlarni yangilang\n\n"
                    "So'ng qayta urinib ko'ring."
                )
                keyboard = [
                    [InlineKeyboardButton("🔄 Akkauntni Qayta Ulash", callback_data="add_account")],
                    [InlineKeyboardButton("⬅️ Orqaga", callback_data="back_to_main")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')
                return
            
            # Send message to selected groups
            try:
                success, result = await verifier.send_message_to_groups(phone_number, scheduled_msg.message_text, selected_groups)
                
                if success:
                    message = (
                        f"✅ <b>Xabar Muvaffaqiyatli Yuborildi!</b>\n\n"
                        f"<b>Xabar:</b> {scheduled_msg.message_text[:50]}...\n"
                        f"<b>Guruhlar:</b> {len(selected_groups)} ta\n"
                        f"<b>Natija:</b> {result}"
                    )
                else:
                    message = f"❌ Xabar yuborishda xato: {result}"
            except Exception as e:
                logger.error(f"Xabar yuborishda xato: {e}")
                message = f"❌ Xabar yuborishda texnik xato: {str(e)}"
        else:
            message = "❌ Rejalashtirilgan xabar topilmadi."
            
    except Exception as e:
        logger.error(f"Xabar yuborishda xato: {e}")
        message = "❌ Xabar yuborishda xato yuz berdi."
    finally:
        db_session.close()
    
    keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="back_to_main")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')

async def new_message_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle new message callback - redirect to message schedule"""
    message = (
        "⚠️ <b>Xabarlar Rejasi Kerak!</b>\n\n"
        "Yangi xabar yaratish uchun avval 'Xabarlar Rejasi' bo'limiga o'ting.\n\n"
        "U yerda xabarlarni tayyorlab, keyin ularni yuborishingiz mumkin."
    )
    
    keyboard = [
        [InlineKeyboardButton("📅 Xabarlar Rejasiga O'tish", callback_data="message_schedule")],
        [InlineKeyboardButton("🏠 Bosh Menyu", callback_data="back_to_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')

async def handle_message_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle message text input for sending"""
    if not context.user_data.get('awaiting_message_text'):
        return
    
    message_text = update.message.text
    user = update.effective_user
    
    # Determine target groups
    if context.user_data.get('send_to_all_groups'):
        target_groups = context.user_data.get('target_groups', [])
        send_type = "barcha guruhlarga"
    elif context.user_data.get('send_to_selected_groups'):
        target_groups = context.user_data.get('target_groups', [])
        send_type = "tanlangan guruhlarga"
    else:
        await update.message.reply_text("❌ Xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring.")
        return
    
    if not target_groups:
        await update.message.reply_text("❌ Guruhlar topilmadi.")
        return
    
    # Send message to groups using Telegram API
    try:
        phone_number = context.user_data.get('phone_number')
        if not phone_number:
            await update.message.reply_text("❌ Telefon raqam topilmadi.")
            return
        
        success, result = await verifier.send_message_to_groups(phone_number, message_text, target_groups)
        
        if success:
            message = (
                f"✅ <b>Xabar Muvaffaqiyatli Yuborildi!</b>\n\n"
                f"<b>Xabar:</b> {message_text[:50]}...\n"
                f"<b>Guruhlar:</b> {len(target_groups)} ta ({send_type})\n"
                f"<b>Natija:</b> {result}"
            )
        else:
            message = f"❌ Xabar yuborishda xato: {result}"
            
    except Exception as e:
        logger.error(f"Xabar yuborishda xato: {e}")
        message = "❌ Xabar yuborishda xato yuz berdi."
    
    keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="back_to_main")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(message, reply_markup=reply_markup, parse_mode='HTML')
    
    # Clear message sending state
    context.user_data.pop('awaiting_message_text', None)
    context.user_data.pop('send_to_all_groups', None)
    context.user_data.pop('send_to_selected_groups', None)
    context.user_data.pop('target_groups', None)

async def scheduled_messages_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle scheduled messages callback - shows all repeating messages"""
    user = update.effective_user
    db_session = get_session()
    
    try:
        db_user = db_session.query(User).filter(User.telegram_id == str(user.id)).first()
        if db_user:
            # Get all active repeating messages (not just scheduled)
            scheduled_msgs = db_session.query(ScheduledMessage).filter(
                ScheduledMessage.user_id == db_user.id,
                ScheduledMessage.is_active == True,
                ScheduledMessage.is_repeat == True  # Only show repeating messages
            ).all()
            
            if not scheduled_msgs:
                message = (
                    "📋 <b>Doimiy Xabarlar</b>\n\n"
                    "Hech qanday doimiy xabar yo'q.\n\n"
                    "Yangi xabar yaratish uchun 'Xabar Rejalashtirish' tugmasini bosing."
                )
                keyboard = [
                    [InlineKeyboardButton("📝 Xabar Rejalashtirish", callback_data="schedule_message")],
                    [InlineKeyboardButton("⬅️ Orqaga", callback_data="back_to_main")]
                ]
            else:
                message = (
                    "📋 <b>Doimiy Xabarlar</b>\n\n"
                    f"<b>Jami:</b> {len(scheduled_msgs)} ta doimiy xabar\n\n"
                    "Quyidagi xabarlarni boshqarishingiz mumkin:\n"
                    "🟢 - Faol (yuborilmoqda)\n"
                    "🔴 - To'xtatilgan\n\n"
                    "Xabarni o'chirish uchun ustiga bosing:"
                )
                
                # Create keyboard with scheduled messages
                keyboard = []
                for i, msg in enumerate(scheduled_msgs[:10]):  # Show first 10 messages
                    status_icon = "🟢" if msg.is_active else "🔴"
                    interval_text = f"har {msg.repeat_interval} min" if msg.repeat_interval < 60 else f"har {msg.repeat_interval // 60} soat"
                    
                    # Convert to Uzbekistan time (UTC+5)
                    from datetime import timedelta
                    uz_time = msg.schedule_time + timedelta(hours=5)
                    time_str = uz_time.strftime("%H:%M")
                    
                    button_text = f"{status_icon} {i+1}. {msg.message_text[:20]}... ({interval_text}) [{time_str}]"
                    callback_data = f"manage_message_{msg.id}"
                    keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
                
                # Add management buttons
                keyboard.extend([
                    [InlineKeyboardButton("🆕 Yangi Xabar", callback_data="new_message")],
                    [InlineKeyboardButton("🗑 Barchasini O'chirish", callback_data="clear_all_messages")],
                    [InlineKeyboardButton("⬅️ Orqaga", callback_data="back_to_main")]
                ])
                
                # Store message list for management
                context.user_data['scheduled_messages'] = {msg.id: msg for msg in scheduled_msgs}
            
        else:
            message = "❌ Foydalanuvchi topilmadi."
            keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="back_to_main")]]
            
    except Exception as e:
        logger.error(f"Rejalashtirilgan xabarlarni olishda xato: {e}")
        message = "❌ Xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring."
        keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="back_to_main")]]
    finally:
        db_session.close()
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Try to edit message, handle "Message is not modified" error gracefully
    try:
        await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')
    except Exception as e:
        error_str = str(e).lower()
        if "message is not modified" in error_str or "exactly the same" in error_str:
            # Message content is the same, just answer the callback query
            try:
                await update.callback_query.answer("✅ Ma'lumotlar yangilandi")
            except:
                pass
        else:
            # Real error, log it
            logger.error(f"Xabarni yangilashda xato: {e}")
            try:
                await update.callback_query.message.reply_text(message, reply_markup=reply_markup, parse_mode='HTML')
            except:
                pass

async def message_schedule_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle message schedule callback - shows schedule menu"""
    user = update.effective_user
    db_session = get_session()
    
    try:
        db_user = db_session.query(User).filter(User.telegram_id == str(user.id)).first()
        if db_user:
            # Get count of scheduled messages
            from src.models.database import ScheduledMessage
            active_count = db_session.query(ScheduledMessage).filter(
                ScheduledMessage.user_id == db_user.id,
                ScheduledMessage.is_active == True
            ).count()
            
            message = (
                "📅 <b>Xabarlar Rejasi</b>\n\n"
                f"<b>Faol rejalashtirilgan xabarlar:</b> {active_count} ta\n\n"
                "Bu bo'limda siz:\n"
                "• Yangi xabarlar rejalashtirishingiz\n"
                "• Mavjud rejalashtirilgan xabarlarni ko'rishingiz\n"
                "• Xabarlarni boshqarishingiz mumkin\n\n"
                "Quyidagi variantlardan birini tanlang:"
            )
            
            keyboard = [
                [InlineKeyboardButton("📝 Xabar Rejalashtirish", callback_data="schedule_message")],
                [InlineKeyboardButton("📋 Rejalashtirilgan Xabarlar", callback_data="scheduled_messages")],
                [InlineKeyboardButton("⬅️ Orqaga", callback_data="back_to_main")]
            ]
        else:
            message = "❌ Foydalanuvchi topilmadi."
            keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="back_to_main")]]
            
    except Exception as e:
        logger.error(f"Xabarlar rejasi menyusida xato: {e}")
        message = "❌ Xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring."
        keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="back_to_main")]]
    finally:
        db_session.close()
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')

# Helper functions for target selection
async def handle_target_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle target group selection"""
    # Implementation for group selection
    pass

async def handle_media_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle media upload for messages"""
    # Implementation for media handling
    pass

# Folder-related handlers
async def use_folder_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle use saved folder callback"""
    callback_data = update.callback_query.data
    folder_id = int(callback_data.split('_')[-1])
    
    user = update.effective_user
    db_session = get_session()
    
    try:
        # Get folder
        from src.models.database import GroupFolder
        folder = db_session.query(GroupFolder).filter(GroupFolder.id == folder_id).first()
        
        if folder:
            # Parse group IDs from JSON
            import json
            try:
                group_ids = json.loads(folder.group_ids)
            except:
                group_ids = []
            
            if group_ids:
                context.user_data['selected_groups'] = group_ids
                context.user_data['using_folder'] = folder.folder_name
                
                # Show folder content and proceed to message selection
                message = (
                    f"📁 <b>Jild: {folder.folder_name}</b>\n\n"
                    f"Guruhlar soni: {len(group_ids)} ta\n\n"
                    "Endi yuboriladigan xabarni tanlang:"
                )
                
                # Get scheduled messages
                db_user = db_session.query(User).filter(User.telegram_id == str(user.id)).first()
                if db_user:
                    scheduled_msgs = db_session.query(ScheduledMessage).filter(
                        ScheduledMessage.user_id == db_user.id,
                        ScheduledMessage.is_active == True
                    ).all()
                    
                    if not scheduled_msgs:
                        message = (
                            "⚠️ <b>Xabarlar Rejasi Bo'sh!</b>\n\n"
                            "Sizda rejalashtirilgan xabarlar yo'q.\n\n"
                            "Xabar yuborish uchun avval 'Xabarlar Rejasi' bo'limida xabar tayyorlab oling."
                        )
                        keyboard = [
                            [InlineKeyboardButton("📅 Xabarlar Rejasiga O'tish", callback_data="message_schedule")],
                            [InlineKeyboardButton("🏠 Bosh Menyu", callback_data="back_to_main")]
                        ]
                    else:
                        keyboard = []
                        for i, msg in enumerate(scheduled_msgs[:5]):
                            button_text = f"📝 {msg.message_text[:20]}..."
                            callback_data = f"use_scheduled_{msg.id}"
                            keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
                        
                        keyboard.append([InlineKeyboardButton("⬅️ Orqaga", callback_data="send_message")])
                else:
                    keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="send_message")]]
            else:
                message = "❌ Jildda guruhlar topilmadi."
                keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="send_message")]]
        else:
            message = "❌ Jild topilmadi."
            keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="send_message")]]
            
    except Exception as e:
        logger.error(f"Jildni ishlatishda xato: {e}")
        message = "❌ Xatolik yuz berdi."
        keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="send_message")]]
    finally:
        db_session.close()
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')

async def new_group_selection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle new group selection callback"""
    await show_group_selection(update, context)

async def save_as_folder_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle save as folder callback"""
    selected_groups = context.user_data.get('selected_groups', [])
    
    if not selected_groups:
        await update.callback_query.answer("❌ Avval guruhlarni tanlang!", show_alert=True)
        return
    
    message = (
        "💾 <b>Jild Sifatida Saqlash</b>\n\n"
        f"Tanlangan guruhlar: {len(selected_groups)} ta\n\n"
        "Iltimos, jild uchun nom kiriting:\n"
        "(Masalan: 'Mijozlar', 'Do'stlar', 'Ish')"
    )
    
    keyboard = [[InlineKeyboardButton("⬅️ Bekor Qilish", callback_data="send_message")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')
    context.user_data['awaiting_folder_name'] = True

async def handle_folder_name_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle folder name input"""
    if not context.user_data.get('awaiting_folder_name'):
        return
    
    folder_name = update.message.text.strip()
    selected_groups = context.user_data.get('selected_groups', [])
    
    if not folder_name:
        await update.message.reply_text("❌ Jild nomi bo'sh bo'lishi mumkin emas.")
        return
    
    if len(folder_name) > 50:
        await update.message.reply_text("❌ Jild nomi 50 ta belgidan oshmasligi kerak.")
        return
    
    user = update.effective_user
    db_session = get_session()
    
    try:
        db_user = db_session.query(User).filter(User.telegram_id == str(user.id)).first()
        if db_user:
            # Save folder
            from src.models.database import GroupFolder
            import json
            
            folder = GroupFolder(
                user_id=db_user.id,
                folder_name=folder_name,
                group_ids=json.dumps(selected_groups)
            )
            db_session.add(folder)
            db_session.commit()
            
            message = (
                f"✅ <b>Jild Saqlandi!</b>\n\n"
                f"Jild nomi: {folder_name}\n"
                f"Guruhlar soni: {len(selected_groups)} ta\n\n"
                "Endi xabar yuborish uchun 'Tanlovni Yakunlash' tugmasini bosing."
            )
            
            keyboard = [
                [InlineKeyboardButton("✅ Tanlovni Yakunlash", callback_data="finish_group_selection")],
                [InlineKeyboardButton("⬅️ Orqaga", callback_data="send_message")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(message, reply_markup=reply_markup, parse_mode='HTML')
            context.user_data['awaiting_folder_name'] = False
        else:
            await update.message.reply_text("❌ Foydalanuvchi topilmadi.")
    except Exception as e:
        logger.error(f"Jildni saqlashda xato: {e}")
        await update.message.reply_text("❌ Jildni saqlashda xato yuz berdi.")
    finally:
        db_session.close()

async def clear_all_messages_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear all scheduled messages callback - with confirmation"""
    user = update.effective_user
    db_session = get_session()
    
    try:
        db_user = db_session.query(User).filter(User.telegram_id == str(user.id)).first()
        if db_user:
            # Count active scheduled messages
            scheduled_msgs = db_session.query(ScheduledMessage).filter(
                ScheduledMessage.user_id == db_user.id,
                ScheduledMessage.is_active == True
            ).all()
            
            if scheduled_msgs:
                # Show confirmation dialog
                message = (
                    "🗑 <b>Barcha Xabarlarni O'chirish</b>\n\n"
                    f"Sizda <b>{len(scheduled_msgs)} ta</b> faol rejalashtirilgan xabar bor.\n\n"
                    "<b>Diqqat!</b> Bu amalni qaytarib bo'lmaydi.\n"
                    "Barcha xabarlar o'chiriladi.\n\n"
                    "Davom etishni xohlaysizmi?"
                )
                
                keyboard = [
                    [InlineKeyboardButton("✅ Ha, O'chirish", callback_data="confirm_clear_all")],
                    [InlineKeyboardButton("❌ Yo'q, Bekor Qilish", callback_data="scheduled_messages")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')
                return
            else:
                message = "❌ O'chirish uchun rejalashtirilgan xabarlar topilmadi."
                keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="scheduled_messages")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')
                return
        else:
            message = "❌ Foydalanuvchi topilmadi."
            keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="back_to_main")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')
            return
            
    except Exception as e:
        logger.error(f"Xabarlarni o'chirishda xato: {e}")
        message = "❌ Xabarlarni o'chirishda xato yuz berdi."
        keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="scheduled_messages")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')
    finally:
        db_session.close()

async def confirm_clear_all_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm clear all messages callback"""
    user = update.effective_user
    db_session = get_session()
    
    try:
        db_user = db_session.query(User).filter(User.telegram_id == str(user.id)).first()
        if db_user:
            # Get all active messages for deletion
            scheduled_msgs = db_session.query(ScheduledMessage).filter(
                ScheduledMessage.user_id == db_user.id,
                ScheduledMessage.is_active == True
            ).all()
            
            if scheduled_msgs:
                # Permanently delete all messages
                deleted_count = 0
                for msg in scheduled_msgs:
                    db_session.delete(msg)
                    deleted_count += 1
                
                db_session.commit()
                
                message = (
                    f"✅ <b>Barcha Rejalashtirilgan Xabarlar O'chirildi!</b>\n\n"
                    f"{deleted_count} ta xabar butunlay o'chirildi.\n\n"
                    "Yangi xabarlar yaratish uchun 'Xabarlar Rejasi' bo'limiga o'ting."
                )
            else:
                message = "❌ O'chirish uchun rejalashtirilgan xabarlar topilmadi."
        else:
            message = "❌ Foydalanuvchi topilmadi."
            
    except Exception as e:
        logger.error(f"Xabarlarni o'chirishda xato: {e}")
        message = f"❌ Xabarlarni o'chirishda xato yuz berdi: {str(e)}"
    finally:
        db_session.close()
    
    keyboard = [
        [InlineKeyboardButton("📅 Xabarlar Rejasiga O'tish", callback_data="message_schedule")],
        [InlineKeyboardButton("🏠 Bosh Menyu", callback_data="back_to_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')

async def manage_message_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manage individual message callback - now with delete option"""
    callback_data = update.callback_query.data
    message_id = int(callback_data.split('_')[-1])
    
    user = update.effective_user
    db_session = get_session()
    
    try:
        # Get scheduled message
        scheduled_msg = db_session.query(ScheduledMessage).filter(ScheduledMessage.id == message_id).first()
        if scheduled_msg:
            # Check if this is a toggle or delete request
            # For now, we'll toggle status on first click, delete on confirmation
            if scheduled_msg.is_active:
                # Message is active, ask for confirmation to stop/delete
                message = (
                    f"🛑 <b>Xabarni To'xtatish</b>\n\n"
                    f"<b>Xabar:</b> {scheduled_msg.message_text[:50]}...\n"
                    f"<b>Interval:</b> Har {scheduled_msg.repeat_interval} daqiqada\n\n"
                    f"Bu xabarni to'xtatmoqchimisiz?\n\n"
                    f"⚠️ <i>Xabar to'xtatilgandan keyin qayta ishga tushirilmaydi!</i>"
                )
                
                keyboard = [
                    [InlineKeyboardButton("🗑 Ha, O'chirish", callback_data=f"confirm_delete_{message_id}")],
                    [InlineKeyboardButton("❌ Yo'q, Bekor Qilish", callback_data="scheduled_messages")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')
                return
            else:
                # Message is already inactive
                message = (
                    f"📋 <b>Xabar Boshqaruvi</b>\n\n"
                    f"<b>Xabar:</b> {scheduled_msg.message_text[:50]}...\n"
                    f"<b>Status:</b> 🔴 To'xtatilgan\n\n"
                    f"Bu xabar allaqachon to'xtatilgan."
                )
        else:
            message = "❌ Rejalashtirilgan xabar topilmadi."
            
    except Exception as e:
        logger.error(f"Xabar boshqaruvda xato: {e}")
        message = "❌ Xabar boshqaruvda xato yuz berdi."
    finally:
        db_session.close()
    
    keyboard = [
        [InlineKeyboardButton("🔄 Orqaga", callback_data="scheduled_messages")],
        [InlineKeyboardButton("⬅️ Asosiy Menyu", callback_data="back_to_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Try to edit message, handle "Message is not modified" error gracefully
    try:
        await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')
    except Exception as e:
        error_str = str(e).lower()
        if "message is not modified" in error_str or "exactly the same" in error_str:
            # Message content is the same, just answer the callback query
            try:
                await update.callback_query.answer("✅ Xabar holati o'zgartirildi")
            except:
                pass
        else:
            # Real error, log it
            logger.error(f"Xabarni yangilashda xato: {e}")
            try:
                await update.callback_query.message.reply_text(message, reply_markup=reply_markup, parse_mode='HTML')
            except:
                pass

async def confirm_delete_message_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm and permanently delete a message and cleanup session"""
    callback_data = update.callback_query.data
    message_id = int(callback_data.split('_')[-1])
    
    user = update.effective_user
    db_session = get_session()
    
    try:
        # Get and delete the message
        scheduled_msg = db_session.query(ScheduledMessage).filter(
            ScheduledMessage.id == message_id
        ).first()
        
        if scheduled_msg:
            msg_text = scheduled_msg.message_text[:50]
            
            db_session.delete(scheduled_msg)
            db_session.commit()
            
            message = (
                f"✅ <b>Xabar O'chirildi!</b>\n\n"
                f"<b>Xabar:</b> {msg_text}...\n\n"
                f"Xabar butunlay o'chirildi va endi yuborilmaydi."
            )
            logger.info(f"Message {message_id} permanently deleted by user {user.id}")
        else:
            message = "❌ Xabar topilmadi."
            
    except Exception as e:
        logger.error(f"Xabarni o'chirishda xato: {e}")
        message = "❌ Xabarni o'chirishda xato yuz berdi."
    finally:
        db_session.close()
    
    keyboard = [
        [InlineKeyboardButton("📋 Xabarlar Ro'yxati", callback_data="scheduled_messages")],
        [InlineKeyboardButton("🏠 Bosh Menyu", callback_data="back_to_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.message.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')


