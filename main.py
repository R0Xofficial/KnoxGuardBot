# --- main.py ---
import logging
import asyncio
from datetime import datetime, timezone
from telegram import Update
from telegram.constants import ParseMode, ChatType
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from config import TOKEN, OWNER_ID, LOG_CHAT_ID, APPEAL_CHAT_USERNAME
import database as db
import utils

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- USER LOGGER (Zapisywanie osób do bazy) ---

async def log_user_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user and not user.is_bot:
        db.log_user(user.id, user.username, user.first_name)

# --- PROTECTION LOGIC ---

async def check_gban_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not chat or chat.type == ChatType.PRIVATE: 
        return

    if not db.is_enforced(chat.id): 
        return
    
    user = update.effective_user
    if not user or db.is_sudo(user.id): 
        return

    ban_info = db.get_gban(user.id)
    
    if ban_info:
        reason = ban_info[0]
        try:
            await context.bot.ban_chat_member(chat.id, user.id)
            if update.message:
                try:
                    await update.message.delete()
                except Exception:
                    pass

            msg = (f"⚠️ <b>Alert!</b> This user is globally banned.\n"
                   f"<i>Enforcing ban in this chat.</i>\n\n"
                   f"<b>User ID:</b> <code>{user.id}</code>\n"
                   f"<b>Reason:</b> {utils.safe_escape(reason)}\n"
                   f"<b>Appeal Chat:</b> {APPEAL_CHAT_USERNAME}")
            
            await context.bot.send_message(chat.id, msg, parse_mode=ParseMode.HTML)
            
        except Exception as e:
            logging.error(f"Failed to enforce gban in {chat.id} for user {user.id}: {e}")

# --- COMMANDS ---

async def gban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin = update.effective_user
    if not db.is_sudo(admin.id): return
    target_id, reason = None, None
    if update.message.reply_to_message:
        target_id = update.message.reply_to_message.from_user.id
        reason = " ".join(context.args) if context.args else None
    elif context.args:
        target_id, err = await utils.resolve_id(context, context.args[0])
        if err: await update.message.reply_text(err); return
        reason = " ".join(context.args[1:]) if len(context.args) > 1 else None

    if not target_id or not reason:
        await update.message.reply_text("Usage: /gban <ID/@user/reply> <reason>"); return
    if db.is_sudo(target_id) or target_id == context.bot.id:
        await update.message.reply_text("LoL, looks like... Someone tried gban privileged user. Nice Try."); return

    old_ban = db.get_gban(target_id)
    await update.message.reply_html("Ok!")
    await asyncio.sleep(0.5)

    db.add_gban(target_id, admin.id, reason)
    user_link = await utils.create_user_link(target_id, context)
    admin_link = await utils.create_user_link(admin.id, context)
    curr_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    hashtag = "#GBANUPDATE" if old_ban else "#GBANNED"
    
    log_msg = (f"<b>{hashtag}</b>\n"
               f"<b>Initiated From:</b> {utils.safe_escape(update.effective_chat.title)} [<code>{update.effective_chat.id}</code>]\n\n"
               f"<b>User:</b> {user_link} [<code>{target_id}</code>]\n"
               f"<b>Reason:</b> <code>{utils.safe_escape(reason)}</code>\n")
    if old_ban: log_msg += f"<b>Old Reason:</b> <code>{utils.safe_escape(old_ban[0])}</code>\n"
    log_msg += f"<b>Date:</b> <code>{curr_time}</code>\n<b>Admin:</b> {admin_link} [<code>{admin.id}</code>]"

    await update.message.reply_html(log_msg)
    if LOG_CHAT_ID: await context.bot.send_message(LOG_CHAT_ID, log_msg, parse_mode=ParseMode.HTML)

async def ungban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin = update.effective_user
    if not db.is_sudo(admin.id): return
    target_id = None
    if update.message.reply_to_message:
        target_id = update.message.reply_to_message.from_user.id
    elif context.args:
        target_id, _ = await utils.resolve_id(context, context.args[0])
    
    if not target_id: await update.message.reply_text("User ID not found."); return

    await update.message.reply_html("Let's give him another chance!")
    await asyncio.sleep(0.5)

    if db.remove_gban(target_id):
        user_link = await utils.create_user_link(target_id, context)
        admin_link = await utils.create_user_link(admin.id, context)
        curr_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        log_msg = (f"<b>#UNGBANNED</b>\n"
                   f"<b>Initiated From:</b> {utils.safe_escape(update.effective_chat.title)} [<code>{update.effective_chat.id}</code>]\n\n"
                   f"<b>User:</b> {user_link} [<code>{target_id}</code>]\n"
                   f"<b>Date:</b> <code>{curr_time}</code>\n"
                   f"<b>Admin:</b> {admin_link} [<code>{admin.id}</code>]")
        await update.message.reply_html(log_msg)
        if LOG_CHAT_ID: await context.bot.send_message(LOG_CHAT_ID, log_msg, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text("User is not globally banned.")

async def gbanstat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    sudo = db.is_sudo(user.id)
    target_id = None
    if sudo:
        if update.message.reply_to_message: target_id = update.message.reply_to_message.from_user.id
        elif context.args: target_id, _ = await utils.resolve_id(context, context.args[0])
    if not target_id: target_id = user.id
    
    ban = db.get_gban(target_id)
    u_link = await utils.create_user_link(target_id, context)
    title = "Your Global Ban Status" if target_id == user.id else "Global Ban Status"
    if ban:
        msg = (f"<b>{title}</b>\n<b>User:</b> {u_link} [<code>{target_id}</code>]\n"
               f"<b>Status:</b> Banned\n"
               f"<b>Reason:</b> <code>{utils.safe_escape(ban[0])}</code>\n<b>Date:</b> <code>{ban[2]}</code>\n")
        if sudo:
            a_link = await utils.create_user_link(ban[1], context)
            msg += f"<b>Admin:</b> {a_link} [<code>{ban[1]}</code>]"
        else: msg += f"<b>Appeal Chat:</b> {APPEAL_CHAT_USERNAME}"
    else: msg = f"<b>{title}</b>\n<b>User:</b> {u_link} [<code>{target_id}</code>]\n\n<b>Status:</b> Not Banned"
    await update.message.reply_html(msg)

async def addsudo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    target_id = None
    if update.message.reply_to_message: target_id = update.message.reply_to_message.from_user.id
    elif context.args: target_id, _ = await utils.resolve_id(context, context.args[0])
    if target_id:
        db.add_sudo(target_id)
        await update.message.reply_text(f"✅ User {target_id} added to sudo list.")

async def delsudo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    target_id = None
    if update.message.reply_to_message: target_id = update.message.reply_to_message.from_user.id
    elif context.args: target_id, _ = await utils.resolve_id(context, context.args[0])
    if target_id and target_id != OWNER_ID:
        db.remove_sudo(target_id)
        await update.message.reply_text(f"❌ User {target_id} removed from sudo list.")

async def enforce_gban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type == ChatType.PRIVATE: return
    member = await chat.get_member(update.effective_user.id)
    if member.status != "creator" and not db.is_sudo(update.effective_user.id): return
    if not context.args: return
    
    choice = context.args[0].lower()
    if choice in ['yes', 'on']:
        db.set_enforce(chat.id, 1)
        await update.message.reply_html("✅ <b>Global Ban enforcement is now ENABLED.</b>")
    elif choice in ['no', 'off']:
        db.set_enforce(chat.id, 0)
        await update.message.reply_html("❌ <b>Global Ban enforcement is now DISABLED.</b>")

# --- MAIN ---

def main():
    db.init_db()
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("gban", gban_command))
    app.add_handler(CommandHandler("ungban", ungban_command))
    app.add_handler(CommandHandler("gbanstat", gbanstat_command))
    app.add_handler(CommandHandler("addsudo", addsudo_command))
    app.add_handler(CommandHandler("delsudo", delsudo_command))
    app.add_handler(CommandHandler("enforcegban", enforce_gban_command))

    # User Logger (Group 0 - zawsze loguje)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, log_user_handler), group=0)
    # Gban Checker (Group 1 - sprawdza po zlogowaniu)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, check_gban_handler), group=-1)

    print("Gban Bot with UserCache Started...")
    app.run_polling()

if __name__ == "__main__":
    main()
