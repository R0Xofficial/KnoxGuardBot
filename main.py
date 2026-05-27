# --- main.py ---
import logging
import asyncio
from datetime import datetime, timezone
from telegram import Update
from telegram.constants import ParseMode, ChatType
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from config import TOKEN, APPEAL_CHAT_USERNAME, LOG_CHANNEL_ID, OWNER_ID
import database as db
import utils

logging.basicConfig(level=logging.INFO)

# --- PROTECTION LOGIC ---

async def enforce_ban_logic(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    chat = update.effective_chat
    if not db.is_gban_enforced(chat.id) or db.is_sudo(user_id): return
    reason = db.get_gban_reason(user_id)
    if reason:
        try:
            await context.bot.ban_chat_member(chat.id, user_id)
            if update.message: await update.message.delete()
            msg = (f"⚠️ <b>Alert!</b> This user is globally banned.\n"
                   f"<i>Enforcing ban in this chat.</i>\n\n"
                   f"<b>User ID:</b> <code>{user_id}</code>\n"
                   f"<b>Reason:</b> {utils.safe_escape(reason)}\n"
                   f"<b>Appeal Chat:</b> {APPEAL_CHAT_USERNAME}")
            await context.bot.send_message(chat.id, msg, parse_mode=ParseMode.HTML)
        except: pass

# --- COMMANDS ---

async def gban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin = update.effective_user
    chat = update.effective_chat
    if not db.is_sudo(admin.id): return

    target_id, reason = None, None
    if update.message.reply_to_message:
        target_id = update.message.reply_to_message.from_user.id
        reason = " ".join(context.args) if context.args else None
    elif context.args:
        try:
            target_id = int(context.args[0])
            reason = " ".join(context.args[1:]) if len(context.args) > 1 else None
        except: pass

    if not target_id or not reason:
        await update.message.reply_text("Usage: /gban <ID/reply> <reason>"); return
    if db.is_sudo(target_id) or target_id == context.bot.id:
        await update.message.reply_text("LoL, looks like... Someone tried global ban privileged user. Nice Try."); return

    old_reason = db.get_gban_reason(target_id)
    await update.message.reply_html("Ok!")
    await asyncio.sleep(0.5)

    if db.add_to_gban(target_id, admin.id, reason):
        user_link = await utils.create_user_html_link(target_id, context)
        admin_link = await utils.create_user_html_link(admin.id, context)
        chat_display = utils.get_chat_display(chat, update.message.message_id, admin.first_name)
        curr_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        
        hashtag = "#GBANUPDATE" if old_reason else "#GBANNED"

        log_message = (f"<b>{hashtag}</b>\n"
                       f"<b>Initiated From:</b> {chat_display} [<code>{chat.id}</code>]\n\n"
                       f"<b>User:</b> {user_link} [<code>{target_id}</code>]\n"
                       f"<b>Reason:</b> <code>{utils.safe_escape(reason)}</code>\n")
        
        if old_reason: 
            log_message += f"<b>Old Reason:</b> <code>{utils.safe_escape(old_reason)}</code>\n"
            
        log_message += (f"<b>Date:</b> <code>{curr_time}</code>\n"
                        f"<b>Admin:</b> {admin_link} [<code>{admin.id}</code>]")

        await update.message.reply_html(log_message)
        if LOG_CHANNEL_ID: 
            await context.bot.send_message(LOG_CHANNEL_ID, log_message, parse_mode=ParseMode.HTML)

async def ungban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin = update.effective_user
    chat = update.effective_chat
    if not db.is_sudo(admin.id): return
    
    target_id = None
    if update.message.reply_to_message: target_id = update.message.reply_to_message.from_user.id
    elif context.args:
        try: target_id = int(context.args[0])
        except: pass
    if not target_id: return

    if db.remove_from_gban(target_id):
        user_link = await utils.create_user_html_link(target_id, context)
        admin_link = await utils.create_user_html_link(admin.id, context)
        chat_display = utils.get_chat_display(chat, update.message.message_id, admin.first_name)
        curr_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        log_message = (f"<b>#UNGBANNED</b>\n"
                       f"<b>Initiated From:</b> {chat_display} [<code>{chat.id}</code>]\n\n"
                       f"<b>User:</b> {user_link} [<code>{target_id}</code>]\n"
                       f"<b>Date:</b> <code>{curr_time}</code>\n"
                       f"<b>Admin:</b> {admin_link} [<code>{admin.id}</code>]")
        
        await update.message.reply_html(log_message)
        if LOG_CHANNEL_ID: 
            await context.bot.send_message(LOG_CHANNEL_ID, log_message, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text("User is not globally banned.")

async def gban_stat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    is_admin = db.is_sudo(user.id)
    
    target_id = None
    checking_self = False

    # Logic: Admins can check others, Users check themselves
    if is_admin:
        if update.message.reply_to_message:
            target_id = update.message.reply_to_message.from_user.id
        elif context.args:
            try: target_id = int(context.args[0])
            except: pass
        
    if not target_id:
        target_id = user.id
        checking_self = True

    details = db.get_gban_details(target_id)
    user_link = await utils.create_user_html_link(target_id, context)
    title = "Your Global Ban Status" if checking_self else "Global Ban Status"
    
    if details:
        reason, admin_id, date = details
        
        msg = (f"<b>{title}</b>\n"
               f"<b>User:</b> {user_link} [<code>{target_id}</code>]\n"
               f"<b>Status:</b> ⚠️ <code>Globally Banned</code>\n\n"
               f"<b>Reason:</b> <code>{utils.safe_escape(reason)}</code>\n"
               f"<b>Date:</b> <code>{date}</code>\n")
        
        # --- CONDITIONAL ADMIN/APPEAL DISPLAY ---
        if is_admin:
            admin_link = await utils.create_user_html_link(admin_id, context)
            msg += f"<b>Admin:</b> {admin_link} [<code>{admin_id}</code>]"
        else:
            msg += f"<b>Appeal Chat:</b> {APPEAL_CHAT_USERNAME}"
    else:
        msg = (f"<b>{title}</b>\n"
               f"<b>User:</b> {user_link} [<code>{target_id}</code>]\n"
               f"<b>Status:</b> ✅ <code>Not Banned</code>")
    
    await update.message.reply_html(msg)

# --- SUDO MGMT ---

async def add_sudo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    try:
        t_id = update.message.reply_to_message.from_user.id if update.message.reply_to_message else int(context.args[0])
        db.add_sudo(t_id)
        await update.message.reply_text(f"✅ Added {t_id} to sudo.")
    except: await update.message.reply_text("Usage: /addsudo <ID/reply>")

async def del_sudo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    try:
        t_id = update.message.reply_to_message.from_user.id if update.message.reply_to_message else int(context.args[0])
        if t_id == OWNER_ID: return
        db.remove_sudo(t_id)
        await update.message.reply_text(f"❌ Removed {t_id} from sudo.")
    except: await update.message.reply_text("Usage: /delsudo <ID/reply>")

async def enforce_gban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type == ChatType.PRIVATE: return
    member = await chat.get_member(update.effective_user.id)
    if member.status != "creator" and not db.is_sudo(update.effective_user.id): return
    if not context.args: return
    status = 1 if context.args[0].lower() in ['on', 'yes'] else 0
    db.update_chat_enforcement(chat.id, status)
    await update.message.reply_html(f"✅ Gban enforcement: {'ENABLED' if status else 'DISABLED'}")

# --- EVENTS ---

async def on_event(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or update.effective_chat.type == ChatType.PRIVATE: return
    if update.message and update.message.new_chat_members:
        for m in update.message.new_chat_members: await enforce_ban_logic(update, context, m.id)
    elif update.effective_user: await enforce_ban_logic(update, context, update.effective_user.id)

def main():
    db.init_db()
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("gban", gban_command))
    app.add_handler(CommandHandler("ungban", ungban_command))
    app.add_handler(CommandHandler("gbanstat", gban_stat_command))
    app.add_handler(CommandHandler("addsudo", add_sudo_cmd))
    app.add_handler(CommandHandler("delsudo", del_sudo_cmd))
    app.add_handler(CommandHandler("enforcegban", enforce_gban_command))

    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, on_event), group=1)
    
    print("Bot is up and running...")
    app.run_polling()

if __name__ == "__main__": main()
