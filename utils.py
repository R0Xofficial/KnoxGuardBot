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

async def resolve_target(context, target_input):
    """
    Resolves a username or ID into a numeric ID.
    Returns (user_id, error_message)
    """
    if not target_input:
        return None, "No input provided."
    
    target_str = str(target_input).strip()
    
    try:
        target_id = int(target_str)
        if target_id < 0:
            return None, "🧐 This action can only be applied to users, not chats/channels."
        return target_id, None
    except ValueError:
        try:
            if not target_str.startswith("@"):
                target_str = f"@{target_str}"
            
            resolved_chat = await context.bot.get_chat(target_str)
            
            if resolved_chat.type != ChatType.PRIVATE:
                return None, "🧐 This action can only be applied to users, not channels/groups."
            
            return resolved_chat.id, None
        except Exception:
            return None, "I can't find this user. They must interact with me or be in the same group."
