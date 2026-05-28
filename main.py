import logging
import asyncio
import time
import traceback
import io
import os
import sys
import subprocess
import sqlite3
from datetime import datetime, timezone
from telegram import Update
from telegram.constants import ParseMode, ChatType, ChatMemberStatus
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ChatMemberHandler

from config import TOKEN, OWNER_ID, LOG_CHAT_ID, APPEAL_CHAT_USERNAME, DB_NAME
import database as db
import utils
from handlers import bot_command, command_router

BOT_START_TIME = datetime.now(timezone.utc)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- LOGGERS ---
async def log_user_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user and not user.is_bot:
        db.log_user(user.id, user.username, user.first_name)

async def chat_logger_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    
    if chat and chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]:
        db.log_chat(chat.id)
        
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

async def send_startup_log(context: ContextTypes.DEFAULT_TYPE):
    if LOG_CHAT_ID:
        try:
            await context.bot.send_message(LOG_CHAT_ID, "Started")
        except Exception as e:
            logger.error(f"Failed to send startup log: {e}")

@bot_command("help")
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    is_sudo = db.is_sudo(user_id)
    is_owner = (user_id == OWNER_ID)

    help_text = (
        "<b>Global Ban Bot Help</b>\n\n"
        "<b>User Commands:</b>\n"
        "• <code>/ping</code> - Check bot latency.\n"
        "• <code>/uptime</code> - See how long bot is running.\n"
        "• <code>/enforcegban &lt;on/off&gt;</code> - Toggle protection on current chat.\n\n"
        "• <code>/gbanstat</code> - Check your own ban status.\n\n"
    )

    if is_sudo:
        help_text += (
            "<b>Sudo Commands:</b>\n"
            "• <code>/gban &lt;target&gt; &lt;reason&gt;</code> - Issue a global ban.\n"
            "• <code>/ungban &lt;target&gt;</code> - Revoke a global ban.\n"
            "• <code>/gbanstat &lt;target&gt;</code> - Check user's detailed ban info.\n"
            "• <code>/stats</code> - View database statistics.\n"
            "• <code>/sudolist</code> - Show all bot administrators.\n"
        )

    if is_owner:
        help_text += (
            "<b>Master Owner Commands:</b>\n"
            "• <code>/addsudo &lt;target&gt;</code> - Grant sudo privileges.\n"
            "• <code>/delsudo &lt;target&gt;</code> - Revoke sudo privileges.\n"
            "• <code>/cleanup</code> - Remove inactive chats from database.\n"
            "• <code>/backup</code> - Get the latest database file.\n\n"
        )

    help_text += "<i>You can use '/' or '!' as a prefix for all commands.</i>"

    await update.message.reply_html(help_text)

@bot_command("ping")
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

@bot_command("uptime")
async def uptime_command(update: Update, context: ContextTypes.DEFAULT_TYPE):    
    current_time = datetime.now(timezone.utc)
    uptime_seconds = int((current_time - BOT_START_TIME).total_seconds())
    readable_uptime = await get_readable_time(uptime_seconds)
    
    await update.message.reply_html(
        f"<b>Bot Uptime</b>\n"
        f"<b>Running for:</b> <code>{readable_uptime}</code>"
    )

@bot_command("gban")
async def gban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin = update.effective_user
    chat = update.effective_chat
    if not db.is_sudo(admin.id): return
    target_id, reason = None, None
    if update.message.reply_to_message and not update.message.reply_to_message.forum_topic_created:
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
    await asyncio.sleep(0.5)

    db.add_gban(target_id, admin.id, reason)
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

@bot_command("ungban")
async def ungban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin = update.effective_user
    chat = update.effective_chat
    if not db.is_sudo(admin.id): return
    target_id = None
    if update.message.reply_to_message and not update.message.reply_to_message.forum_topic_created:
        target_id = update.message.reply_to_message.from_user.id
    elif context.args:
        target_id, _ = await utils.resolve_id(context, context.args[0])
    
    if not target_id:
        await update.message.reply_text("Who is the target of the command? The stars in the sky?"); return

    await update.message.reply_html("Let's give him another chance!")
    await asyncio.sleep(0.5)

    if db.remove_gban(target_id):       
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
        context.job_queue.run_once(propagate_unban, when=1, data={'user_id': target_id})
    else:
        await update.message.reply_text(f"User {user_link} [<code>{target_id}</code>] is not globally banned.")

async def propagate_unban(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    user_id = job_data['user_id']
    
    with sqlite3.connect(DB_NAME) as conn:
        chats = conn.execute("SELECT chat_id FROM bot_chats").fetchall()

    for (chat_id,) in chats:
        try:
            await context.bot.unban_chat_member(chat_id, user_id, only_if_banned=True)
            await asyncio.sleep(0.1) 
        except Exception:
            continue

@bot_command("gbanstat")
async def gbanstat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    sudo = db.is_sudo(user.id)
    target_id = None
    if sudo:
        if update.message.reply_to_message and not update.message.reply_to_message.forum_topic_created:
            target_id = update.message.reply_to_message.from_user.id
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

@bot_command("addsudo")
async def addsudo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: 
        return
    
    target_id = None
    if update.message.reply_to_message and not update.message.reply_to_message.forum_topic_created:
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
        await update.message.reply_text("You are already the Master Owner.")
        return

    if db.is_sudo(target_id):
        user_link = await utils.create_user_link(target_id, context)
        await update.message.reply_html(f"User {user_link} [<code>{target_id}</code>] is <b>already</b> sudo.")
        return

    db.add_sudo(target_id)
    user_link = await utils.create_user_link(target_id, context)
    curr_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    log_msg = (f"<b>#SUDO</b>\n"
                f"<b>User:</b> {user_link} [<code>{target_id}</code>]\n"
                f"<b>Date:</b> <code>{curr_time}</code>")

    await update.message.reply_html(log_msg)
    if LOG_CHAT_ID:
        await context.bot.send_message(LOG_CHAT_ID, log_msg, parse_mode=ParseMode.HTML)

@bot_command("delsudo")
async def delsudo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: 
        return
    
    target_id = None
    if update.message.reply_to_message and not update.message.reply_to_message.forum_topic_created:
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
        await update.message.reply_text("LoL... You cannot remove yourself.")
        return

    if db.remove_sudo(target_id):
        user_link = await utils.create_user_link(target_id, context)
        curr_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        log_msg = (f"<b>#UNSUDO</b>\n"
                   f"<b>User:</b> {user_link} [<code>{target_id}</code>]\n"
                   f"<b>Date:</b> <code>{curr_time}</code>")

        await update.message.reply_html(log_msg)
        if LOG_CHAT_ID:
            await context.bot.send_message(LOG_CHAT_ID, log_msg, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text("This user was not in the Sudo list.")

@bot_command("enforceban")
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

@bot_command("stats")
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not db.is_sudo(update.effective_user.id): return
    
    with sqlite3.connect(DB_NAME) as conn:
        gbans = conn.execute("SELECT COUNT(*) FROM gbans").fetchone()[0]
        users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        chats = conn.execute("SELECT COUNT(*) FROM bot_chats").fetchone()[0]
    
    msg = (f"<b>Bot Statistics:</b>\n\n"
           f"• <b>Global Bans:</b> <code>{gbans}</code>\n"
           f"• <b>Known Users:</b> <code>{users}</code>\n"
           f"• <b>Total Chats:</b> <code>{chats}</code>")
    await update.message.reply_html(msg)

@bot_command("databackup")
async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    try:
        with open(DB_NAME, 'rb') as f:
            await context.bot.send_document(OWNER_ID, document=f, caption=f"Database Backup: {datetime.now()}")
        await update.message.reply_text("Backup sent to your PM.")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

@bot_command("cleanup")
async def cleanup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: 
        return

    status_msg = await update.message.reply_text("Starting chat database cleanup...")
    
    with sqlite3.connect(DB_NAME) as conn:
        chats = conn.execute("SELECT chat_id FROM bot_chats").fetchall()

    total = len(chats)
    removed = 0
    checked = 0
    bot_id = context.bot.id

    for (chat_id,) in chats:
        should_remove = False
        try:
            member = await context.bot.get_chat_member(chat_id, bot_id)
            if member.status in [ChatMemberStatus.LEFT, ChatMemberStatus.BANNED]:
                should_remove = True
        except Exception as e:
            should_remove = True
        
        if should_remove:
            db.remove_chat(chat_id)
            removed += 1
            logger.info(f"Cleanup: Removed inactive chat {chat_id}")

        checked += 1
        if checked % 5 == 0:
            await asyncio.sleep(0.5)
    await status_msg.edit_text(
        f"<b>Cleanup chats complete!</b>\n\n"
        f"• Total scanned: <code>{total}</code>\n"
        f"• Removed: <code>{removed}</code>\n"
        f"• Still active: <code>{total - removed}</code>",
        parse_mode=ParseMode.HTML
    )

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)

    tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
    tb_string = "".join(tb_list)

    chat_info = "N/A"
    user_info = "N/A"
    
    if isinstance(update, Update):
        if update.effective_chat:
            chat_title = update.effective_chat.title or "Private Chat"
            chat_info = f"{utils.safe_escape(chat_title)} [<code>{update.effective_chat.id}</code>]"
        if update.effective_user:
            user_name = update.effective_user.first_name
            user_info = f"{utils.safe_escape(user_name)} [<code>{update.effective_user.id}</code>]"

    summary_message = (
        f"<b>Bot Error Detected!</b>\n\n"
        f"<b>Error:</b> <code>{utils.safe_escape(str(context.error))}</code>\n"
        f"<b>Chat:</b> {chat_info}\n"
        f"<b>User:</b> {user_info}\n\n"
        f"<i>Full traceback is attached as a file.</i>"
    )

    if LOG_CHAT_ID:
        try:
            with io.BytesIO(str.encode(tb_string)) as traceback_file:
                filename = f"traceback_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
                traceback_file.name = filename
                
                await context.bot.send_document(
                    chat_id=LOG_CHAT_ID,
                    document=traceback_file,
                    caption=summary_message,
                    parse_mode=ParseMode.HTML
                )
        except Exception as e:
            logger.critical(f"Could not send traceback file: {e}")
            try:
                await context.bot.send_message(
                    LOG_CHAT_ID, 
                    f"<b>Critical Error:</b> Failed to send traceback file.\nError: {e}"
                )
            except:
                pass

@bot_command(["sudolist", "sudos"])
async def sudolist_cmd(update, context):
    if not db.is_sudo(update.effective_user.id): return
    
    sudos = db.get_all_sudos()
    if not sudos:
        await update.message.reply_text("The Sudo list is empty.")
        return

    msg = "<b>Sudo Privileged Users:</b>\n\n"
    msg += f"• {await utils.create_user_link(OWNER_ID, context)} [<code>{OWNER_ID}</code>] (Owner)\n"
    
    for s_id in sudos:
        if s_id == OWNER_ID: continue
        u_link = await utils.create_user_link(s_id, context)
        msg += f"• {u_link} [<code>{s_id}</code>]\n"
    
    await update.message.reply_html(msg)

@bot_command("update")
async def update_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return

    msg = await update.message.reply_text("Checking updates...", parse_mode=ParseMode.HTML)

    try:
        pull_result = subprocess.check_output(["git", "pull"]).decode("utf-8")
        
        if "Already up to date." in pull_result:
            await msg.edit_text("<b>Bot is already up to date.</b>\nNo restart needed.", parse_mode=ParseMode.HTML)
            return

        successful_msg = (f"<b>Update pulled!</b>\n<i>Restarting now...</i>\n<blockquote><code>{pull_result}</code></blockquote>")

        await msg.edit_text(successful_msg, parse_mode=ParseMode.HTML)

        if LOG_CHAT_ID:
            admin_link = await utils.create_user_link(update.effective_user.id, context)
            await context.bot.send_message(LOG_CHAT_ID, successful_msg, parse_mode=ParseMode.HTML)

        os.execv(sys.executable, [sys.executable] + sys.argv)

    except subprocess.CalledProcessError as e:
        await msg.edit_text(f"<b>Update failed!</b>\nError: <code>{str(e)}</code>", parse_mode=ParseMode.HTML)
    except Exception as e:
        await msg.edit_text(f"<b>Unexpected error:</b>\n<code>{str(e)}</code>", parse_mode=ParseMode.HTML)

@bot_command("restart")
async def restart_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    try:
        await update.message.reply_html("Restarting...")
        if LOG_CHAT_ID:
            await context.bot.send_message(LOG_CHAT_ID, "Rebooting system...")
    except Exception as e:
        logger.error(f"Failed to send restart message: {e}")
    os.execv(sys.executable, [sys.executable] + sys.argv)
    
# --- main.py ---

# --- MAIN ---

def main():
    db.init_db()
    app = Application.builder().token(TOKEN).build()

    app.add_error_handler(error_handler)

    app.add_handler(MessageHandler(filters.Regex(r'^[!/]\w+'), command_router), group=1)    

    app.add_handler(MessageHandler(filters.ALL, chat_logger_handler), group=-15)
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, check_gban_on_entry), group=-10)
    app.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, check_gban_on_exit), group=-10)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, check_gban_on_message), group=-10)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, log_user_handler), group=0)

    if app.job_queue:
        app.job_queue.run_once(send_startup_log, when=1)

    print("Bot is up and running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
