# --- database.py ---
import sqlite3
from datetime import datetime, timezone
from config import DB_NAME, OWNER_ID

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        # Gban table
        conn.execute('CREATE TABLE IF NOT EXISTS gbans (user_id INTEGER PRIMARY KEY, reason TEXT, admin_id INTEGER, date TEXT)')
        # Chat settings
        conn.execute('CREATE TABLE IF NOT EXISTS bot_chats (chat_id INTEGER PRIMARY KEY, enforce_gban INTEGER DEFAULT 1)')
        # Sudo table
        conn.execute('CREATE TABLE IF NOT EXISTS sudo_users (user_id INTEGER PRIMARY KEY)')
        # User Cache table (IMPORTANT)
        conn.execute('CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT)')
        conn.commit()

def log_user(user_id, username, first_name):
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute('INSERT OR REPLACE INTO users (user_id, username, first_name) VALUES (?, ?, ?)', 
                     (user_id, username.lower() if username else None, first_name))
        conn.commit()

def get_user_by_username(username):
    username = username.lstrip('@').lower()
    with sqlite3.connect(DB_NAME) as conn:
        res = conn.execute("SELECT user_id FROM users WHERE username = ?", (username,)).fetchone()
        return res[0] if res else None

def is_sudo(user_id):
    if user_id == OWNER_ID: return True
    with sqlite3.connect(DB_NAME) as conn:
        res = conn.execute("SELECT 1 FROM sudo_users WHERE user_id = ?", (user_id,)).fetchone()
        return res is not None

def add_sudo(user_id):
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("INSERT OR IGNORE INTO sudo_users VALUES (?)", (user_id,))
        conn.commit()

def remove_sudo(user_id):
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("DELETE FROM sudo_users WHERE user_id = ?", (user_id,))
        conn.commit()

def get_gban(user_id):
    with sqlite3.connect(DB_NAME) as conn:
        return conn.execute("SELECT reason, admin_id, date FROM gbans WHERE user_id = ?", (user_id,)).fetchone()

def add_gban(user_id, admin_id, reason):
    with sqlite3.connect(DB_NAME) as conn:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        conn.execute("INSERT OR REPLACE INTO gbans VALUES (?, ?, ?, ?)", (user_id, reason, admin_id, date_str))
        conn.commit()

def remove_gban(user_id):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.execute("DELETE FROM gbans WHERE user_id = ?", (user_id,))
        conn.commit()
        return cursor.rowcount > 0

def is_enforced(chat_id):
    with sqlite3.connect(DB_NAME) as conn:
        res = conn.execute("SELECT enforce_gban FROM bot_chats WHERE chat_id = ?", (chat_id,)).fetchone()
        return res[0] == 1 if res else True

def set_enforce(chat_id, status):
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("INSERT OR REPLACE INTO bot_chats (chat_id, enforce_gban) VALUES (?, ?)", (chat_id, status))
        conn.commit()

def remove_chat(chat_id):
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("DELETE FROM bot_chats WHERE chat_id = ?", (chat_id,))
        conn.commit()

def get_all_sudos():
    with sqlite3.connect(DB_NAME) as conn:
        res = conn.execute("SELECT user_id FROM sudo_users").fetchall()
        return [row[0] for row in res]

def log_chat(chat_id):
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("INSERT OR IGNORE INTO bot_chats (chat_id, enforce_gban) VALUES (?, ?)", (chat_id, 1))
        conn.commit()
