# --- utils.py ---
from telegram.constants import ChatType

def safe_escape(text: str) -> str:
    if not text: return ""
    return text.replace("<", "&lt;").replace(">", "&gt;").replace("&", "&amp;")

async def create_user_html_link(user_id: int, context) -> str:
    try:
        chat = await context.bot.get_chat(user_id)
        if chat.type != ChatType.PRIVATE: return f"Invalid User [<code>{user_id}</code>]"
        name = chat.first_name or "User"
        return f'<a href="tg://user?id={user_id}">{safe_escape(name)}</a>'
    except:
        return f'Unknown User [<code>{user_id}</code>]'

def get_chat_display(chat, message_id, admin_name):
    chat_name = safe_escape(chat.title or f"PM with {admin_name}")
    if chat.type != ChatType.PRIVATE and chat.username:
        link = f"https://t.me/{chat.username}/{message_id}"
        return f"<a href='{link}'>{chat_name}</a>"
    return chat_name

async def resolve_target(context, target_input: str):
    if not target_input: return None, "No input provided."
    target_str = target_input.strip()
    
    # Check if numeric ID
    try:
        tid = int(target_str)
        if tid < 0: return None, "🧐 This action can only be applied to users."
        return tid, None
    except ValueError:
        # Try username resolution
        try:
            if not target_str.startswith("@"): target_str = f"@{target_str}"
            resolved = await context.bot.get_chat(target_str)
            if resolved.type != ChatType.PRIVATE:
                return None, "🧐 This action can only be applied to users, not chats."
            return resolved.id, None
        except:
            return None, "I can't find this user. Ensure the ID/@username is correct and the bot has seen them."
