from telegram import Update
from telegram.ext import ContextTypes

REGISTERED_CMDS = {}

def bot_command(name: str | list):
    def decorator(func):
        if isinstance(name, list):
            for n in name:
                REGISTERED_CMDS[n.lower()] = func
        else:
            REGISTERED_CMDS[name.lower()] = func
        return func
    return decorator

async def command_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message or not message.text:
        return

    text = message.text.strip()
    if not (text.startswith('/') or text.startswith('!')):
        return

    parts = text.split()
    cmd_name = parts[0][1:].lower()
    context.args = parts[1:]

    if cmd_name in REGISTERED_CMDS:
        await REGISTERED_CMDS[cmd_name](update, context)
