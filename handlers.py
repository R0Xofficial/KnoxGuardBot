# --- handlers.py ---
from telegram import Update
from telegram.ext import ContextTypes

REGISTERED_CMDS = {}

def bot_command(name: str | list):
    def decorator(func):
        if isinstance(name, list):
            for n in name: REGISTERED_CMDS[n.lower()] = func
        else: REGISTERED_CMDS[name.lower()] = func
        return func
    return decorator

async def command_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg: return
    
    text = msg.text or msg.caption
    if not text: return

    if not (text.startswith('/') or text.startswith('!')):
        return

    parts = text.split()
    cmd_name = parts[0][1:].split('@')[0].lower()
    
    if cmd_name in REGISTERED_CMDS:
        context.args = parts[1:]
        
        thread_id = msg.message_thread_id if msg.is_topic_message else None
        
        async def custom_reply(text, **kwargs):
            return await msg.reply_text(text, message_thread_id=thread_id, **kwargs)
        
        original_reply = msg.reply_text
        msg.reply_text = custom_reply
        
        try:
            await REGISTERED_CMDS[cmd_name](update, context)
        finally:
            msg.reply_text = original_reply
