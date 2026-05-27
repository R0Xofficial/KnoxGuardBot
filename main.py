import logging
import asyncio
import time
from datetime import datetime, timezone
from telegram import Update
from telegram.constants import ParseMode, ChatType, ChatMemberStatus
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ChatMemberHandler

from config import TOKEN, OWNER_ID, LOG_CHAT_ID, APPEAL_CHAT_USERNAME
import database as db
import utils

BOT_START_TIME = datetime.now(timezone.utc)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- USER LOGGER ---
async def log_user_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user and not user.is_bot:
        db.log_user(user.id, user.username, user.first_name)

# --- PROTECTION LOGIC ---

async def check_gban_on_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.new_chat_members:
        return
    
    chat = update.effective_chat
    if not db.is_enforced(chat.id): return

    for member in update.message.new_chat_members:
        if member.is_bot or db.is_sudo(member.id): continue
            
        ban_info = db.get_gban(member.id)
        if ban_info:
            try:
                await context.bot.ban_chat_member(chat.id, member.id)
                
                user_link = await utils.create_user_link(member.id, context)
                
                msg = (f"⚠️ <b>Alert!</b> This user is globally banned.\n"
                       f"<i>Enforcing ban in this chat.</i>\n\n"
                       f"<b>User:</b> {user_link} [<code>{member.id}</code>]\n"
                       f"<b>Reason:</b> {utils.safe_escape(ban_info[0])}\n"
                       f"<b>Appeal Chat:</b> {APPEAL_CHAT_USERNAME}")
                
                await context.bot.send_message(chat.id, text=msg, parse_mode=ParseMode.HTML)
            except Exception as e:
                logger.error(f"Gban Entry Error: {e}")

async def check_gban_on_exit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.left_chat_member:
        return
    
    chat = update.effective_chat
    if not db.is_enforced(chat.id): return

    user = update.message.left_chat_member
    if user.is_bot or db.is_sudo(user.id): return

    ban_info = db.get_gban(user.id)
    if ban_info:
        try:
            await context.bot.ban_chat_member(chat.id, user.id)
            
            user_link = await utils.create_user_link(user.id, context)
            
            msg = (f"⚠️ <b>Alert!</b> This user is globally banned.\n"
                    f"<i>Enforcing ban in this chat.</i>\n\n"
                    f"<b>User:</b> {user_link} [<code>{user.id}</code>]\n"
                    f"<b>Reason:</b> {utils.safe_escape(ban_info[0])}\n"
                    f"<b>Appeal Chat:</b> {APPEAL_CHAT_USERNAME}")
            
            await context.bot.send_message(chat.id, text=msg, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error(f"Gban Exit Error: {e}")

async def check_gban_on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not chat or chat.type == ChatType.PRIVATE: return
    if not db.is_enforced(chat.id): return

    user = update.effective_user
    if not user or user.is_bot or db.is_sudo(user.id): return

    ban_info = db.get_gban(user.id)
    if ban_info:
        try:
            await context.bot.ban_chat_member(chat.id, user.id)
            if update.effective_message:
                try: await update.effective_message.delete()
                except: pass
            
            user_link = await utils.create_user_link(user.id, context)
            
            msg = (f"⚠️ <b>Alert!</b> This user is globally banned.\n"
                   f"<i>Enforcing ban in this chat.</i>\n\n"
                   f"<b>User:</b> {user_link} [<code>{user.id}</code>]\n"
                   f"<b>Reason:</b> {utils.safe_escape(ban_info[0])}\n"
                   f"<b>Appeal Chat:</b> {APPEAL_CHAT_USERNAME}")
            
            await context.bot.send_message(chat.id, text=msg, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error(f"Gban Message Error: {e}")

# --- COMMANDS ---

async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    start_time = time.time()
    
    message = await update.message.reply_text("Pinging...")
    end_time = time.time()
    latency = round((end_time - start_time) * 1000, 2)
    
    await message.edit_text(
        f"<b>Pong!</b>\n"
        f"<b>Latency:</b> <code>{latency} ms</code>",
        parse_mode=ParseMode.HTML
    )

async def get_readable_time(seconds: int) -> str:
    count = 0
    periods = [('d', 86400), ('h', 3600), ('m', 60), ('s', 1)]
    result = []
    for period_name, period_seconds in periods:
        if seconds >= period_seconds:
            period_value, seconds = divmod(seconds, period_seconds)
            result.append(f"{period_value}{period_name}")
    return ", ".join(result) if result else "0s"

async def uptime_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows how long the bot has been running."""
    from config import BOT_START_TIME
    
    current_time = datetime.now(timezone.utc)
    uptime_seconds = int((current_time - BOT_START_TIME).total_seconds())
    readable_uptime = await get_readable_time(uptime_seconds)
    
    await update.message.reply_html(
        f"<b>Bot Uptime</b>\n"
        f"<b>Running for:</b> <code>{readable_uptime}</code>"
    )

async def gban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin = update.effective_user
    chat = update.effective_chat
    if not db.is_sudo(admin.id): return
    target_id, reason = None, None
    if update.message.reply_to_message:
        target_id = update.message.reply_to_message.from_user.id
        reason = " ".join(context.args) if context.args else None
    elif context.args:
        target_id, err = await utils.resolve_id(context, context.args[0])
        if err: await update.message.reply_text(err); return
        reason = " ".join(context.args[1:]) if len(context.args) > 1 else None

    if not target_id:
        await update.message.reply_text("Who is the target of the command? The stars in the sky?"); return
    if not reason:
        await update.message.reply_text("Give a reason!"); return
    if db.is_sudo(target_id) or target_id == context.bot.id:
        await update.message.reply_text("LoL, looks like... Someone tried gban privileged user. Nice Try."); return

    old_ban = db.get_gban(target_id)
    await update.message.reply_html("Ok!")

    db.add_gban(target_id, admin.id, reason)
    
    try: await context.bot.ban_chat_member(chat.id, target_id)
    except: pass

    await asyncio.sleep(0.5)
    user_link = await utils.create_user_link(target_id, context)
    admin_link = await utils.create_user_link(admin.id, context)
    curr_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    hashtag = "#GBANUPDATE" if old_ban else "#GBANNED"
    
    log_msg = (f"<b>{hashtag}</b>\n"
               f"<b>Initiated From:</b> {utils.safe_escape(chat.title)} [<code>{chat.id}</code>]\n\n"
               f"<b>User:</b> {user_link} [<code>{target_id}</code>]\n"
               f"<b>Reason:</b> <code>{utils.safe_escape(reason)}</code>\n")
    if old_ban: log_msg += f"<b>Old Reason:</b> <code>{utils.safe_escape(old_ban[0])}</code>\n"
    log_msg += f"<b>Date:</b> <code>{curr_time}</code>\n<b>Admin:</b> {admin_link} [<code>{admin.id}</code>]"

    await update.message.reply_html(log_msg)
    if LOG_CHAT_ID: await context.bot.send_message(LOG_CHAT_ID, log_msg, parse_mode=ParseMode.HTML)

async def ungban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin = update.effective_user
    chat = update.effective_chat
    if not db.is_sudo(admin.id): return
    target_id = None
    if update.message.reply_to_message:
        target_id = update.message.reply_to_message.from_user.id
    elif context.args:
        target_id, _ = await utils.resolve_id(context, context.args[0])
    
    if not target_id:
        await update.message.reply_text("Who is the target of the command? The stars in the sky?"); return

    await update.message.reply_html("Let's give him another chance!")
    await asyncio.sleep(0.5)

    if db.remove_gban(target_id):
        try: await context.bot.unban_chat_member(chat.id, target_id, only_if_banned=True)
        except: pass
        
        user_link = await utils.create_user_link(target_id, context)
        admin_link = await utils.create_user_link(admin.id, context)
        curr_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        log_msg = (f"<b>#UNGBANNED</b>\n"
                   f"<b>Initiated From:</b> {utils.safe_escape(chat.title)} [<code>{chat.id}</code>]\n\n"
                   f"<b>User:</b> {user_link} [<code>{target_id}</code>]\n"
                   f"<b>Date:</b> <code>{curr_time}</code>\n"
                   f"<b>Admin:</b> {admin_link} [<code>{admin.id}</code>]")
        await update.message.reply_html(log_msg)
        if LOG_CHAT_ID: await context.bot.send_message(LOG_CHAT_ID, log_msg, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(f"User {user_link} [<code>{target_id}</code>] is not globally banned.")

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
    title = "Your Global Ban Status:" if target_id == user.id else "Global Ban Status:"
    if ban:
        msg = (f"<b>{title}</b>\n<b>User:</b> {u_link} [<code>{target_id}</code>]\n"
               f"<b>Status:</b> Globally Banned\n"
               f"<b>Reason:</b> <code>{utils.safe_escape(ban[0])}</code>\n<b>Date:</b> <code>{ban[2]}</code>\n")
        if sudo:
            a_link = await utils.create_user_link(ban[1], context)
            msg += f"<b>Admin:</b> {a_link} [<code>{ban[1]}</code>]"
        else: msg += f"<b>Appeal Chat:</b> {APPEAL_CHAT_USERNAME}"
    else: msg = f"<b>{title}</b>\n<b>User:</b> {u_link} [<code>{target_id}</code>]\n\n<b>Status:</b> Not Banned"
    await update.message.reply_html(msg)

async def addsudo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: 
        return
    
    target_id = None
    if update.message.reply_to_message:
        target_id = update.message.reply_to_message.from_user.id
    elif context.args:
        target_id, err = await utils.resolve_id(context, context.args[0])
        if err:
            await update.message.reply_text(err)
            return
    
    if not target_id:
        await update.message.reply_text("Who is the target of the command? The stars in the sky?")
        return
        
    db.add_sudo(target_id)
    user_link = await utils.create_user_link(target_id, context)
    
    await update.message.reply_html(
        f"Done! User {user_link} [<code>{target_id}</code>] has been added to the <b>Sudo list</b>."
    )

async def delsudo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: 
        return
    
    target_id = None
    if update.message.reply_to_message:
        target_id = update.message.reply_to_message.from_user.id
    elif context.args:
        target_id, err = await utils.resolve_id(context, context.args[0])
        if err:
            await update.message.reply_text(err)
            return
            
    if not target_id:
        await update.message.reply_text("Who is the target of the command? The stars in the sky?")
        return

    if target_id == OWNER_ID:
        await update.message.reply_text("You cannot remove the Master Owner from Sudo.")
        return
    if db.db_query("DELETE FROM sudo_users WHERE user_id = ?", (target_id,), commit=True).rowcount > 0:
        user_link = await utils.create_user_link(target_id, context)
        await update.message.reply_html(
            f"Done! User {user_link} [<code>{target_id}</code>] has been removed from the <b>Sudo list</b>."
        )
    else:
        await update.message.reply_text("This user was not in the Sudo list.")

async def enforce_gban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not chat or chat.type == ChatType.PRIVATE: return
    
    member = await chat.get_member(update.effective_user.id)
    is_sudo = db.is_sudo(update.effective_user.id)
    if member.status != "creator" and not is_sudo:
        await update.message.reply_text("Only the chat Creator can change this setting.")
        return

    current_status = db.is_enforced(chat.id)
    status_text = "ENABLED" if current_status else "DISABLED"

    if not context.args:
        await update.message.reply_html(
            f"<b>Global Ban Enforcement</b>\n\n"
            f"Current status for this chat: <b>{status_text}</b>\n"
            f"<b>Usage:</b> <code>/enforcegban &lt;yes/on/no/off&gt;</code>"
        )
        return
    
    choice = context.args[0].lower()
    if choice in ['yes', 'on']:
        db.set_enforce(chat.id, 1)
        await update.message.reply_html("<b>Global Ban enforcement has been ENABLED.</b>")
    elif choice in ['no', 'off']:
        db.set_enforce(chat.id, 0)
        await update.message.reply_html("<b>Global Ban enforcement has been DISABLED.</b>\n<i>Warning: Gbanned users will no longer be removed automatically.</i>")
    else:
        await update.message.reply_html(
            f"<b>Invalid choice!</b>\n\n"
            f"Use: <code>/enforcegban on</code> or <code>/enforcegban off</code>"
        )

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not db.is_sudo(update.effective_user.id): return
    
    with sqlite3.connect(DB_NAME) as conn:
        gbans = conn.execute("SELECT COUNT(*) FROM gbans").fetchone()[0]
        users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        chats = conn.execute("SELECT COUNT(*) FROM bot_chats").fetchone()[0]
    
    msg = (f"<b>Bot Statistics:</b>\n\n"
           f"• <b>Global Bans:</b> <code>{gbans}</code>\n"
           f"• <b>Cached Users:</b> <code>{users}</code>\n"
           f"• <b>Total Chats:</b> <code>{chats}</code>")
    await update.message.reply_html(msg)

async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    try:
        with open(DB_NAME, 'rb') as f:
            await context.bot.send_document(OWNER_ID, document=f, caption=f"Database Backup: {datetime.now()}")
        await update.message.reply_text("Backup sent to your PM.")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

# --- MAIN ---

def main():
    db.init_db()
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("ping", ping_command))
    app.add_handler(CommandHandler("uptime", uptime_command))
    app.add_handler(CommandHandler("gban", gban_command))
    app.add_handler(CommandHandler("ungban", ungban_command))
    app.add_handler(CommandHandler("gbanstat", gbanstat_command))
    app.add_handler(CommandHandler("addsudo", addsudo_command))
    app.add_handler(CommandHandler("delsudo", delsudo_command))
    app.add_handler(CommandHandler("enforcegban", enforce_gban_command))
    app.add_handler(CommandHandler("databackup", backup_command))

    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, check_gban_on_entry), group=-10)
    app.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, check_gban_on_exit), group=-10)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, check_gban_on_message), group=-10)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, log_user_handler), group=0)

    print("Bot is up and running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
