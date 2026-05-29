import logging
import asyncio
import time
import traceback
import io
import os
import sys
import subprocess
import sqlite3
import telegram
from telegram.error import Forbidden, BadRequest, RetryAfter, TimedOut
from datetime import datetime, timezone
from telegram import Update
from telegram.constants import ParseMode, ChatType, ChatMemberStatus
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ChatMemberHandler, ApplicationHandlerStop

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
async def passive_data_logger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Quietly records user and chat data to support global ban efficiency."""
    user = update.effective_user
    chat = update.effective_chat
    
    # We only log real users to keep the database clean
    if user and not user.is_bot:
        # 1. Update user identity in cache (id, username, first_name)
        db.log_user(user.id, user.username, user.first_name)
        
        if chat and chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]:
            # 2. Ensure the chat is registered in our records
            db.log_chat(chat.id)
            # 3. Map user to this chat (needed for fast unbanning later)
            db.log_user_in_chat(user.id, chat.id)
        
# --- PROTECTION LOGIC ---

async def gban_enforcer_action(user, chat, update: Update, context: ContextTypes.DEFAULT_TYPE, send_alert: bool = True):
    """Internal helper to execute the ban and send alerts."""
    ban_info = db.get_gban(user.id)
    if ban_info:
        try:
            # 1. Ban the user technically across Telegram
            await context.bot.ban_chat_member(chat.id, user.id)
            
            # 2. Send alert message only if requested (usually on Join or Message)
            if send_alert:
                user_link = await utils.create_user_link(user.id, context)
                msg = (f"⚠️ <b>Alert!</b> This user is globally banned.\n"
                       f"<i>Enforcing ban in this chat.</i>\n\n"
                       f"<b>User:</b> {user_link} [<code>{user.id}</code>]\n"
                       f"<b>Reason:</b> <code>{utils.safe_escape(ban_info[0])}</code>\n"
                       f"<b>Appeal Chat:</b> {APPEAL_CHAT_USERNAME}")
                
                # Send as a fresh message to avoid "Message not found" errors
                await context.bot.send_message(chat.id, msg, parse_mode=ParseMode.HTML)
            
            # 3. Stop processing other handlers (Security Layering)
            raise ApplicationHandlerStop()
        except ApplicationHandlerStop:
            raise
        except Exception as e:
            logger.error(f"Enforcer execution failed in {chat.id}: {e}")

async def enforcer_radar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Radar: Detects joins/leaves AND logs clean users for the federation map."""
    result = update.chat_member
    if not result: return
    
    chat = update.effective_chat
    # 1. Registration check
    if not db.is_enforced(chat.id): 
        return

    status_before = result.old_chat_member.status
    status_after = result.new_chat_member.status
    user = result.new_chat_member.user

    if user.is_bot or db.is_sudo(user.id): 
        return

    is_joining = (status_after == ChatMemberStatus.MEMBER and status_before != ChatMemberStatus.MEMBER)
    is_leaving = (status_after in [ChatMemberStatus.LEFT, ChatMemberStatus.BANNED] and status_before == ChatMemberStatus.MEMBER)

    if not (is_joining or is_leaving): 
        return

    # 2. Check for Global Ban
    ban_info = db.get_gban(user.id)
    if ban_info:
        try:
            await context.bot.ban_chat_member(chat.id, user.id)
            if is_joining:
                user_link = await utils.create_user_link(user.id, context)
                msg = (f"⚠️ <b>Alert!</b> I found a user who is globally banned.\n"
                       f"<i>I banned him here!</i>"
                       f"<b>Appeal Chat:</b> {APPEAL_CHAT_USERNAME}\n"
                       f"<b>User:</b> {user_link} [<code>{user.id}</code>]\n"
                       f"<b>Reason:</b> <code>{utils.safe_escape(ban_info[0])}</code>\n")
                await context.bot.send_message(chat.id, msg, parse_mode=ParseMode.HTML)
            
            # Stop the process if banned
            raise ApplicationHandlerStop()
        except ApplicationHandlerStop: raise
        except: pass

    # 3. IF NOT BANNED -> LOG DATA
    # If the user is clean and just joined, we map them immediately
    if is_joining:
        db.log_user(user.id, user.username, user.first_name)
        db.log_chat(chat.id)
        db.log_user_in_chat(user.id, chat.id)
        # logger.info(f"Radar: Logged join for {user.id} in {chat.id}")

async def enforcer_message_checker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Checker: Bans users who are already in chat and try to speak."""
    chat = update.effective_chat
    user = update.effective_user
    
    if not chat or chat.type == ChatType.PRIVATE or not user:
        return
    
    # Ignore system service messages to prevent duplicate alerts
    if update.message and (update.message.new_chat_members or update.message.left_chat_member):
        return

    if not db.is_enforced(chat.id) or db.is_sudo(user.id):
        return

    # Active user check: Ban + Delete Message + Alert
    ban_info = db.get_gban(user.id)
    if ban_info:
        if update.effective_message:
            try: await update.effective_message.delete()
            except: pass
        await gban_enforcer_action(user, chat, update, context, send_alert=True)

# --- COMMANDS ---

async def ignore_edited_commands(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info(f"Ignoring edited command: {update.edited_message.text}")
    raise ApplicationHandlerStop

async def ignore_old_updates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message:
        return

    message_date = update.effective_message.date
    current_time = datetime.now(timezone.utc)

    if (current_time - message_date).total_seconds() > 60:
        logger.info(f"Skipped old update from chat {update.effective_chat.id} (Sent {int((current_time - message_date).total_seconds())}s ago)")
        
        raise ApplicationHandlerStop

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

    help_parts = [
        "<b>Bot Help</b>\n",
        "<b>User Commands:</b>",
        "• <code>/help</code> - Sends this help message.",
        "• <code>/ping</code> - Check bot latency.",
        "• <code>/uptime</code> - See how long bot is running.",
        "• <code>/enforcegban &lt;on/off&gt;</code> - Toggle protection on chat.",
        "• <code>/gbanstat</code> - Check your own ban status.\n"
    ]

    if is_sudo:
        help_parts.extend([
            "<b>Sudo Commands:</b>",
            "• <code>/gban &lt;target&gt; &lt;reason&gt;</code> - Issue a global ban.",
            "• <code>/ungban &lt;target&gt;</code> - Revoke a global ban.",
            "• <code>/gbanstat &lt;target&gt;</code> - Check user's detailed ban info.",
            "• <code>/stats</code> - View database statistics.",
            "• <code>/sudolist</code> - Show all bot administrators.",
            "• <code>/leave</code> - Bot leaving current chat.\n"
        ])

    if is_owner:
        help_parts.extend([
            "<b>Master Owner Commands:</b>",
            "• <code>/addsudo &lt;target&gt;</code> - Grant sudo privileges.",
            "• <code>/delsudo &lt;target&gt;</code> - Revoke sudo privileges.",
            "• <code>/cleanup</code> - Remove inactive chats from database.",
            "• <code>/restart</code> - Restart bot process.",
            "• <code>/update</code> - Update bot from Git.",
            "• <code>/restore</code> - Restore database from file.",
            "• <code>/databackup</code> - Get the latest database file.\n"
        ])

    help_parts.append("<i>You can use '/' or '!' as a prefix for all commands.</i>")

    final_text = "\n".join(help_parts)
    
    try:
        await utils.send_safe_reply(update, context, final_text)
    except Exception as e:
        logger.error(f"Help HTML Error: {e}")
        await update.message.reply_text("Error: There is a formatting issue in the help message.")

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
    
    await utils.send_safe_reply(update, context, 
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
        target_id, err = await utils.resolve_id(update, context, context.args[0])
        if err: await update.message.reply_text(err); return
        reason = " ".join(context.args[1:]) if len(context.args) > 1 else None

    if db.is_sudo(target_id) or target_id == context.bot.id:
        await update.message.reply_text("LoL, looks like... Someone tried gban privileged user. Nice Try."); return
    if not target_id:
        await update.message.reply_text("Who is the target of the command? The stars in the sky?"); return
    if not reason:
        await update.message.reply_text("Give a reason!"); return
        
    old_ban = db.get_gban(target_id)
    if old_ban:
        old_reason = old_ban[0]
        if old_reason.strip() == reason.strip():
            user_link = await utils.create_user_link(target_id, context)
            await utils.send_safe_reply(update, context, f"User {user_link} [<code>{target_id}</code>] is already globally banned for the same reason. <b>No changes made.</b>")
            return

    if chat.type == ChatType.PRIVATE:
        chat_display = f"PM with {utils.safe_escape(admin.first_name)}"
    elif chat.username:
        chat_link = f"https://t.me/{chat.username}/{update.effective_message.message_id}"
        chat_display = f"<a href='{chat_link}'>{utils.safe_escape(chat.title)}</a>"
    else:
        chat_display = utils.safe_escape(chat.title)

    await utils.send_safe_reply(update, context, f"Ok!")

    if db.is_enforced(chat.id):
        try:
            await context.bot.ban_chat_member(chat.id, target_id)
        except Exception as e:
            logger.warning(f"Could not locally ban {target_id}: {e}")

    db.add_gban(target_id, admin.id, reason)
    user_link = await utils.create_user_link(target_id, context)
    admin_link = await utils.create_user_link(admin.id, context)
    curr_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    hashtag = "#GBANUPDATE" if old_ban else "#GBANNED"
    
    log_msg = (f"<b>{hashtag}</b>\n"
               f"<b>Initiated From:</b> {chat_display} [<code>{chat.id}</code>]\n\n"
               f"<b>User:</b> {user_link} [<code>{target_id}</code>]\n"
               f"<b>Reason:</b> <code>{utils.safe_escape(reason)}</code>\n")
    if old_ban: log_msg += f"<b>Old Reason:</b> <code>{utils.safe_escape(old_ban[0])}</code>\n"
    log_msg += f"<b>Date:</b> <code>{curr_time}</code>\n<b>Admin:</b> {admin_link} [<code>{admin.id}</code>]"

    # await utils.send_safe_reply(update, context, log_msg)

    await asyncio.sleep(0.5)
    await utils.send_safe_reply(update, context, "Done! Gbanned.")

@bot_command("ungban")
async def ungban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin = update.effective_user
    chat = update.effective_chat
    thread_id = update.effective_message.message_thread_id
    is_private = update.effective_chat.type == ChatType.PRIVATE
    if not db.is_sudo(admin.id): return
    target_id = None
    if update.message.reply_to_message and not update.message.reply_to_message.forum_topic_created:
        target_id = update.message.reply_to_message.from_user.id
    elif context.args:
        target_id, _ = await utils.resolve_id(update, context, context.args[0])

    if db.is_sudo(target_id) or target_id == context.bot.id:
        await update.message.reply_text("Privileged users is never gbanned..."); return
    if not target_id:
        await update.message.reply_text("Who is the target of the command? The stars in the sky?"); return

    await utils.send_safe_reply(update, context, "Let's give him another chance!")
    await asyncio.sleep(0.5)
    user_link = await utils.create_user_link(target_id, context)

    if chat.type == ChatType.PRIVATE:
        chat_display = f"PM with {utils.safe_escape(admin.first_name)}"
    elif chat.username:
        chat_link = f"https://t.me/{chat.username}/{update.effective_message.message_id}"
        chat_display = f"<a href='{chat_link}'>{utils.safe_escape(chat.title)}</a>"
    else:
        chat_display = utils.safe_escape(chat.title)

    if db.remove_gban(target_id):       
        admin_link = await utils.create_user_link(admin.id, context)
        curr_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        log_msg = (f"<b>#UNGBANNED</b>\n"
                   f"<b>Initiated From:</b> {chat_display} [<code>{chat.id}</code>]\n\n"
                   f"<b>User:</b> {user_link} [<code>{target_id}</code>]\n"
                   f"<b>Date:</b> <code>{curr_time}</code>\n"
                   f"<b>Admin:</b> {admin_link} [<code>{admin.id}</code>]")
        # await utils.send_safe_reply(update, context, log_msg)
        if LOG_CHAT_ID: await context.bot.send_message(LOG_CHAT_ID, log_msg, parse_mode=ParseMode.HTML)
        context.job_queue.run_once(propagate_unban, when=1, data={
            'user_id': target_id,
            'chat_id': chat.id,
            'reply_to': update.message.message_id,
            'thread_id': thread_id,
            'is_private': is_private
        })
    else:
        await update.message.reply_text(f"User {user_link} [<code>{target_id}</code>] is not globally banned.")

async def propagate_unban(context: ContextTypes.DEFAULT_TYPE):
    """Astrako Style: High-speed unban using chat mapping."""   
    start_time = time.time()
    job_data = context.job.data
    user_id = job_data['user_id']
    target_chat_id = job_data['chat_id']
    command_msg_id = job_data['reply_to']
    thread_id = job_data.get('thread_id')
    is_private = job_data.get('is_private', False)

    # 1. FETCH ONLY RELATED CHATS (Federation Mapping)
    # Instead of all chats, we only target where the user was seen.
    chats = db.get_user_seen_chats(user_id)
    
    # Always include the current chat in the sync list
    if target_chat_id not in chats:
        chats.append(target_chat_id)

    logger.info(f"Starting unban for {user_id} on {len(chats)} known chats.")

    for chat_id in chats:
        try:
            # Silent unban: Telegram handles the check via only_if_banned
            await context.bot.unban_chat_member(chat_id, user_id, only_if_banned=True)
                
        except Forbidden:
            # Bot was kicked or blocked -> Remove chat from DB
            db.remove_chat(chat_id)
            
        except BadRequest as e:
            err = str(e).lower()
            if any(x in err for x in ["chat not found", "bot was kicked", "not member"]):
                db.remove_chat(chat_id)
            
        except RetryAfter as e:
            # Respect Telegram's flood limits
            await asyncio.sleep(e.retry_after)
            
        except (TimedOut, Exception):
            pass
            
        # Very short sleep because we have very few requests to make now
        await asyncio.sleep(0.05)

    # 2. FINAL REPORT
    duration = round(time.time() - start_time, 2)
    final_text = f"User has been un-gbanned.\nTime taken: <code>{duration}s</code>"
    
    try:
        if is_private:
            await context.bot.send_message(chat_id=target_chat_id, text=final_text, parse_mode=ParseMode.HTML)
        else:
            await context.bot.send_message(
                chat_id=target_chat_id, text=final_text, parse_mode=ParseMode.HTML,
                reply_to_message_id=command_msg_id, message_thread_id=thread_id
            )
    except:
        pass
        
@bot_command("gbanstat")
async def gbanstat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    sudo = db.is_sudo(user.id)
    target_id = None
    if sudo:
        if update.message.reply_to_message and not update.message.reply_to_message.forum_topic_created:
            target_id = update.message.reply_to_message.from_user.id
        elif context.args: target_id, _ = await utils.resolve_id(update, context, context.args[0])
    if not target_id: target_id = user.id
    
    ban = db.get_gban(target_id)
    u_link = await utils.create_user_link(target_id, context)
    title = "Your Global Ban Status:" if target_id == user.id else "Global Ban Status:"
    if ban:
        msg = (f"<b>{title}</b>\n<b>User:</b> {u_link} [<code>{target_id}</code>]\n"
               f"<b>Status:</b> Banned\n"
               f"<b>Reason:</b> <code>{utils.safe_escape(ban[0])}</code>\n<b>Date:</b> <code>{ban[2]}</code>\n")
        if sudo:
            a_link = await utils.create_user_link(ban[1], context)
            msg += f"<b>Admin:</b> {a_link} [<code>{ban[1]}</code>]"
        else: msg += f"<b>Appeal Chat:</b> {APPEAL_CHAT_USERNAME}"
    else: msg = f"<b>{title}</b>\n<b>User:</b> {u_link} [<code>{target_id}</code>]\n\n<b>Status:</b> Not Banned"
    await utils.send_safe_reply(update, context, msg)

@bot_command("addsudo")
async def addsudo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: 
        return
    
    target_id = None
    if update.message.reply_to_message and not update.message.reply_to_message.forum_topic_created:
        target_id = update.message.reply_to_message.from_user.id
    elif context.args:
        target_id, err = await utils.resolve_id(update, context, context.args[0])
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
        await utils.send_safe_reply(update, context, f"User {user_link} [<code>{target_id}</code>] is <b>already</b> sudo.")
        return

    db.add_sudo(target_id)
    user_link = await utils.create_user_link(target_id, context)
    curr_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    log_msg = (f"<b>#SUDO</b>\n"
                f"<b>User:</b> {user_link} [<code>{target_id}</code>]\n"
                f"<b>Date:</b> <code>{curr_time}</code>")

    await utils.send_safe_reply(update, context, log_msg)
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
        target_id, err = await utils.resolve_id(update, context, context.args[0])
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

        await utils.send_safe_reply(update, context, log_msg)
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
        await utils.send_safe_reply(update, context, 
            f"<b>Global Ban Enforcement</b>\n\n"
            f"Current status for this chat: <b>{status_text}</b>\n"
            f"<b>Usage:</b> <code>/enforcegban &lt;yes/on/no/off&gt;</code>"
        )
        return
    
    choice = context.args[0].lower()
    if choice in ['yes', 'on']:
        db.set_enforce(chat.id, 1)
        await utils.send_safe_reply(update, context, "<b>Global Ban enforcement has been ENABLED.</b>")
    elif choice in ['no', 'off']:
        db.set_enforce(chat.id, 0)
        await utils.send_safe_reply(update, context, "<b>Global Ban enforcement has been DISABLED.</b>\n<i>Warning: Gbanned users will no longer be removed automatically.</i>")
    else:
        await utils.send_safe_reply(update, context, 
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
    await utils.send_safe_reply(update, context, msg)

@bot_command("backup")
async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    curr_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(DB_NAME, 'rb') as f:
            await context.bot.send_document(OWNER_ID, document=f, caption=f"Database Backup: {curr_time}")
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
    
    await utils.send_safe_reply(update, context, msg)

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
        await utils.send_safe_reply(update, context, "Restarting...")
        if LOG_CHAT_ID:
            await context.bot.send_message(LOG_CHAT_ID, "Rebooting system...")
    except Exception as e:
        logger.error(f"Failed to send restart message: {e}")
    os.execv(sys.executable, [sys.executable] + sys.argv)

@bot_command("leave")
async def leave_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not db.is_sudo(update.effective_user.id):
        return

    if update.effective_chat.type == ChatType.PRIVATE:
        await update.message.reply_text("I can only leave groups.")
        return

    chat_id = update.effective_chat.id

    try:
        await update.message.reply_text("Farewell! My duties here are finished. 🫡")
        
        db.remove_chat(chat_id)
        await context.bot.leave_chat(chat_id)
        
        logger.info(f"Bot left chat {chat_id} via leave command.")
    except Exception as e:
        logger.error(f"Error while leaving chat {chat_id}: {e}")

@bot_command("restore")
async def restore_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return

    message = update.effective_message
    document = message.document or (message.reply_to_message.document if message.reply_to_message else None)

    if not document:
        await message.reply_text("Send the database file or reply to one.")
        return

    required_filename = os.path.basename(DB_NAME)

    if document.file_name != required_filename:
        await message.reply_html(
            f"<b>Wrong filename!</b>\n"
            f"I only accepts: <code>{required_filename}</code>"
        )
        return
        
    status_msg = await utils.send_safe_reply(update, context, "Downloading database...")

    try:
        new_db_file = await context.bot.get_file(document.file_id)
        
        await new_db_file.download_to_drive(DB_NAME)
        
        await status_msg.edit_text(
            f"<b>Database restored!</b>\nRestarting system now...", 
            parse_mode=ParseMode.HTML
        )

        os.execv(sys.executable, [sys.executable] + sys.argv)

    except Exception as e:
        logger.error(f"Restore failed: {e}")
        await status_msg.edit_text(f"<b>Error during restore:</b>\n<code>{str(e)}</code>", parse_mode=ParseMode.HTML)

async def auto_backup_job(context: ContextTypes.DEFAULT_TYPE):
    if OWNER_ID:
        try:
            db_filename = os.path.basename(DB_NAME)
            curr_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            
            with open(DB_NAME, 'rb') as db_file:
                await context.bot.send_document(
                    chat_id=OWNER_ID,
                    document=db_file,
                    filename=db_filename,
                    caption=f"Auto-Database Backup: {curr_time}",
                    parse_mode=ParseMode.HTML
                )
            logger.info("Automatic backup sent to owner.")
        except Exception as e:
            logger.error(f"Auto-backup failed: {e}")

# --- main.py ---

# --- MAIN ---

def main():
    db.init_db()
    app = Application.builder().token(TOKEN).build()

    app.add_handler(MessageHandler(filters.ALL, ignore_old_updates), group=-200)
    app.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE & filters.COMMAND, ignore_edited_commands), group=-50)

    app.add_error_handler(error_handler)

    app.add_handler(MessageHandler(filters.Regex(r'^[!/]\w+'), command_router), group=1)    

    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, enforcer_message_checker), group=-100)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, passive_data_logger), group=10)

    if app.job_queue:
        app.job_queue.run_once(send_startup_log, when=1)

    app.job_queue.run_repeating(auto_backup_job, interval=3600, first=30)

    print("Bot is up and running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
