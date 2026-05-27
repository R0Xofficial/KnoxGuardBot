# --- utils.py ---
import html
import database as db
from telegram.constants import ChatType

def safe_escape(text: str) -> str:
    return html.escape(str(text)).replace("&#x27;", "’")

async def create_user_link(user_id: int, context) -> str:
    try:
        chat = await context.bot.get_chat(user_id)
        name = chat.first_name or "User"
        return f'<a href="tg://user?id={user_id}">{safe_escape(name)}</a>'
    except:
        return f'Unknown User'

async def resolve_id(context, input_str: str):
    input_str = input_str.strip()
    
    # 1. Sprawdź czy to ID (cyfry)
    if input_str.isdigit() or (input_str.startswith("-") and input_str[1:].isdigit()):
        uid = int(input_str)
        if uid < 0: return None, "🧐 Channels/Chats cannot be globally banned."
        return uid, None
    
    # 2. Sprawdź w lokalnej bazie danych (po username)
    if input_str.startswith("@"):
        db_id = db.get_user_by_username(input_str)
        if db_id: return db_id, None
        
        # 3. Jeśli nie ma w bazie, spróbuj API (rzadko działa dla obcych)
        try:
            res = await context.bot.get_chat(input_str)
            if res.type == ChatType.PRIVATE:
                return res.id, None
        except:
            pass

    return None, "I can't find this user. They must send a message in a group where I am present first."
