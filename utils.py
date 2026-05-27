# --- utils.py ---
from telegram.constants import ChatType

def safe_escape(text: str) -> str:
    if not text: return ""
    return text.replace("<", "&lt;").replace(">", "&gt;").replace("&", "&amp;")

async def create_user_html_link(user_id: int, context) -> str:
    try:
        chat = await context.bot.get_chat(user_id)
        name = chat.first_name or "User"
        return f'<a href="tg://user?id={user_id}">{safe_escape(name)}</a>'
    except:
        return f'Unknown User'

def get_chat_display(chat, message_id, admin_name):
    chat_name = safe_escape(chat.title or f"PM with {admin_name}")
    if chat.type != ChatType.PRIVATE and chat.username:
        link = f"https://t.me/{chat.username}/{message_id}"
        return f"<a href='{link}'>{chat_name}</a>"
    return chat_name
