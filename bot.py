import json
import os
import sqlite3
import logging
import random
from datetime import datetime, timedelta, time
from pathlib import Path

import requests
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ==================== SOZLAMALAR ====================

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
ADMIN_ID = os.environ.get("ADMIN_ID", "6299702947")

# RASMIY GEMINI MODELI NOMI (gemini-3.5 degani yo'q)
GEMINI_MODEL = "gemini-3.5-flash-lite"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

DATA_DIR = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") or os.environ.get("DATA_DIR", ".")
DB_PATH = Path(DATA_DIR) / "tasks.db"
TASHKENT_OFFSET = timedelta(hours=5)
CHAT_HISTORY_LIMIT = 16

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def now_tashkent() -> datetime:
    return datetime.utcnow() + TASHKENT_OFFSET


# ==================== MA'LUMOTLARNI SAQLASH (SQLite) ====================

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    Path(DATA_DIR).mkdir(parents=True, exist_ok=True)
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            joined_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            text TEXT NOT NULL,
            category TEXT DEFAULT '📌 Shaxsiy',
            done INTEGER NOT NULL DEFAULT 0,
            created TEXT NOT NULL,
            proposed_remind_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            task_id INTEGER,
            task_text TEXT NOT NULL,
            remind_at TEXT NOT NULL,
            sent INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            role TEXT NOT NULL,
            message TEXT NOT NULL,
            created TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pending_actions (
            user_id TEXT PRIMARY KEY,
            action TEXT NOT NULL,
            task_id INTEGER NOT NULL
        )
    """)
    conn.commit()
    conn.close()


# ---------- Foydalanuvchilar ----------

def save_or_update_user(user_id: str, username: str, full_name: str) -> None:
    conn = get_connection()
    conn.execute("""
        INSERT INTO users (user_id, username, full_name, joined_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username = excluded.username,
            full_name = excluded.full_name
    """, (user_id, username, full_name, now_tashkent().strftime("%Y-%m-%d %H:%M")))
    conn.commit()
    conn.close()


def get_all_users() -> list:
    conn = get_connection()
    rows = conn.execute("SELECT user_id, username, full_name, joined_at FROM users").fetchall()
    conn.close()
    return [dict(row) for row in rows]


# ---------- Vazifalar ----------

def get_user_tasks(user_id: str) -> list:
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, text, category, done, created FROM tasks WHERE user_id = ? ORDER BY id DESC",
        (user_id,),
    ).fetchall()
    conn.close()
    return [
        {
            "id": row["id"],
            "text": row["text"],
            "category": row["category"] or "📌 Shaxsiy",
            "done": bool(row["done"]),
            "created": row["created"],
        }
        for row in rows
    ]


def get_all_tasks() -> list:
    """Barcha foydalanuvchilarning barcha vazifalarini qaytaradi (admin eksporti uchun)."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, user_id, text, category, done, created FROM tasks ORDER BY user_id, id"
    ).fetchall()
    conn.close()
    return [
        {
            "id": row["id"],
            "user_id": row["user_id"],
            "text": row["text"],
            "category": row["category"] or "📌 Shaxsiy",
            "done": bool(row["done"]),
            "created": row["created"],
        }
        for row in rows
    ]


def add_user_task(user_id: str, text: str, category: str = "📌 Shaxsiy") -> int:
    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO tasks (user_id, text, category, done, created) VALUES (?, ?, ?, 0, ?)",
        (user_id, text, category, now_tashkent().strftime("%Y-%m-%d %H:%M")),
    )
    conn.commit()
    task_id = cursor.lastrowid
    conn.close()
    return task_id


def toggle_task(task_id: int, done: bool) -> None:
    conn = get_connection()
    conn.execute("UPDATE tasks SET done = ? WHERE id = ?", (int(done), task_id))
    conn.commit()
    conn.close()


def delete_task(task_id: int) -> None:
    conn = get_connection()
    conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.execute("DELETE FROM reminders WHERE task_id = ?", (task_id,))
    conn.commit()
    conn.close()


def clear_done_tasks(user_id: str) -> int:
    conn = get_connection()
    cursor = conn.execute("DELETE FROM tasks WHERE user_id = ? AND done = 1", (user_id,))
    conn.commit()
    removed = cursor.rowcount
    conn.close()
    return removed


def set_proposed_reminder(task_id: int, remind_at) -> None:
    conn = get_connection()
    conn.execute("UPDATE tasks SET proposed_remind_at = ? WHERE id = ?", (remind_at, task_id))
    conn.commit()
    conn.close()


def get_task(task_id: int):
    conn = get_connection()
    row = conn.execute(
        "SELECT id, user_id, text, category, done, created, proposed_remind_at FROM tasks WHERE id = ?",
        (task_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ---------- Eslatmalar ----------

def create_reminder(user_id: str, task_id, task_text: str, remind_at: str) -> None:
    conn = get_connection()
    conn.execute(
        "INSERT INTO reminders (user_id, task_id, task_text, remind_at, sent) VALUES (?, ?, ?, ?, 0)",
        (user_id, task_id, task_text, remind_at),
    )
    conn.commit()
    conn.close()


def get_due_reminders(now_str: str) -> list:
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, user_id, task_text, remind_at FROM reminders WHERE sent = 0 AND remind_at <= ?",
        (now_str,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def mark_reminder_sent(reminder_id: int) -> None:
    conn = get_connection()
    conn.execute("UPDATE reminders SET sent = 1 WHERE id = ?", (reminder_id,))
    conn.commit()
    conn.close()


# ---------- Kutilayotgan amallar ----------

def set_pending_action(user_id: str, action: str, task_id: int) -> None:
    conn = get_connection()
    conn.execute(
        "INSERT INTO pending_actions (user_id, action, task_id) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET action = excluded.action, task_id = excluded.task_id",
        (user_id, action, task_id),
    )
    conn.commit()
    conn.close()


def get_pending_action(user_id: str):
    conn = get_connection()
    row = conn.execute(
        "SELECT action, task_id FROM pending_actions WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def clear_pending_action(user_id: str) -> None:
    conn = get_connection()
    conn.execute("DELETE FROM pending_actions WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


# ---------- Suhbat xotirasi ----------

def add_chat_message(user_id: str, role: str, message: str) -> None:
    conn = get_connection()
    conn.execute(
        "INSERT INTO chat_history (user_id, role, message, created) VALUES (?, ?, ?, ?)",
        (user_id, role, message, now_tashkent().strftime("%Y-%m-%d %H:%M")),
    )
    conn.execute(
        """
        DELETE FROM chat_history
        WHERE user_id = ? AND id NOT IN (
            SELECT id FROM chat_history WHERE user_id = ? ORDER BY id DESC LIMIT ?
        )
        """,
        (user_id, user_id, CHAT_HISTORY_LIMIT),
    )
    conn.commit()
    conn.close()


def get_chat_history(user_id: str) -> list:
    conn = get_connection()
    rows = conn.execute(
        "SELECT role, message FROM chat_history WHERE user_id = ? ORDER BY id",
        (user_id,),
    ).fetchall()
    conn.close()
    return [{"role": row["role"], "message": row["message"]} for row in rows]


def get_all_chat_history() -> list:
    """Barcha foydalanuvchilarning AI bilan suhbat tarixini qaytaradi (admin eksporti uchun)."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, user_id, role, message, created FROM chat_history ORDER BY user_id, id"
    ).fetchall()
    conn.close()
    return [
        {
            "id": row["id"],
            "user_id": row["user_id"],
            "role": row["role"],
            "message": row["message"],
            "created": row["created"],
        }
        for row in rows
    ]


def clear_chat_history(user_id: str) -> None:
    conn = get_connection()
    conn.execute("DELETE FROM chat_history WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


# ==================== GEMINI API ====================

def _gemini_request(contents: list, system_instruction: str = ""):
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY,
    }
    payload = {
        "contents": contents,
    }
    if system_instruction:
        payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}

    for attempt in range(2):
        try:
            response = requests.post(GEMINI_URL, headers=headers, json=payload, timeout=60)
            if response.status_code != 200:
                logger.error(f"Gemini API xatosi [{response.status_code}]: {response.text}")
                return None
            result = response.json()
            return result["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as e:
            logger.error(f"Gemini API ulashda xatolik: {e}")
            return None


def ask_gemini_chat(user_id: str, user_message: str) -> str:
    history = get_chat_history(user_id)
    contents = [
        {"role": ("user" if h["role"] == "user" else "model"), "parts": [{"text": h["message"]}]}
        for h in history
    ]
    contents.append({"role": "user", "parts": [{"text": user_message}]})

    reply = _gemini_request(contents, ASSISTANT_SYSTEM_PROMPT)
    if reply is None:
        return "⚠️ Kechirasiz, AI xizmatida vaqtinchalik uzilish yuz berdi. Birozdan so'ng qayta urinib ko'ring."

    add_chat_message(user_id, "user", user_message)
    add_chat_message(user_id, "model", reply)
    return reply


def extract_datetime(text: str) -> dict:
    current = now_tashkent().strftime("%Y-%m-%d %H:%M (%A)")
    system_instruction = (
        f"Joriy sana va vaqt (Toshkent, UTC+5): {current}. "
        "Foydalanuvchi matnidan sana/vaqtni va mos kelsa kategoriyani (💼 Ish, 📚 O'qish, 📌 Shaxsiy) top. "
        "FAQAT quyidagi JSON formatida javob ber:\n"
        '{"has_time": true/false, "datetime": "YYYY-MM-DD HH:MM" yoki null, '
        '"display": "odam o\'qiydigan qisqa format" yoki null, "category": "💼 Ish"|"📚 O\'qish"|"📌 Shaxsiy"}'
    )
    contents = [{"role": "user", "parts": [{"text": text}]}]
    reply = _gemini_request(contents, system_instruction)
    if reply is None:
        return {"has_time": False, "datetime": None, "display": None, "category": "📌 Shaxsiy"}

    cleaned = reply.strip().strip("`")
    if cleaned.startswith("json"):
        cleaned = cleaned[4:].strip()
    try:
        data = json.loads(cleaned)
        if data.get("has_time") and data.get("datetime"):
            datetime.strptime(data["datetime"], "%Y-%m-%d %H:%M")
        return data
    except Exception:
        return {"has_time": False, "datetime": None, "display": None, "category": "📌 Shaxsiy"}


ASSISTANT_SYSTEM_PROMPT = (
    "Sen aqlli shaxsiy yordamchisan. O'zbek tilida chiroyli, emojilar bilan va qisqa javob ber."
)

# ==================== TELEGRAM MENYUSI VA BUYRUQLARI ====================

def main_keyboard():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("📝 Yangi vazifa"), KeyboardButton("📋 Vazifalarim")],
            [KeyboardButton("🧹 Bajarilganlarni tozalash"), KeyboardButton("🧠 AI Xotirasini tozalash")],
        ],
        resize_keyboard=True,
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    save_or_update_user(str(user.id), user.username or "", user.full_name or "")
    
    text = (
        "<b>Salom! 👋 Men sizning shaxsiy AI yordamchingizman.</b>\n\n"
        "🧠 <b>AI bilan muloqot:</b> Menga shunchaki xabar yozing!\n"
        "📝 <b>Vazifalar:</b> Pastdagi tugmalardan foydalaning yoki <code>/task Vazifa matni</code> buyrug'ini yuboring."
    )
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=main_keyboard())


async def export_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """ADMIN BUYRUQ: Foydalanuvchilar, vazifalar va AI suhbatlarini JSON fayl qilib yuboradi."""
    user = update.effective_user
    user_id = str(user.id)

    logger.info(f"/info bajarilmoqda. User: {user_id}, Admin Target: {ADMIN_ID}")

    # Adminlikni tekshirish (str formatida solishtirish)
    if str(user_id) != str(ADMIN_ID):
        await update.message.reply_text(
            f"⛔ Bu buyruq faqat bot admini uchun!\nSizning ID: {user_id}"
        )
        return

    try:
        users = get_all_users()
        tasks = get_all_tasks()
        chat_history = get_all_chat_history()

        if not users and not tasks and not chat_history:
            await update.message.reply_text("⚠️ Bazada hozircha hech qanday ma'lumot topilmadi.")
            return

        data = {
            "foydalanuvchilar": users,
            "vazifalar": tasks,
            "ai_suhbatlari": chat_history,
        }

        file_path = Path(DATA_DIR) / "bot_export.json"

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

        with open(file_path, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename="bot_export.json",
                caption=(
                    "📊 <b>To'liq ma'lumotlar eksporti</b>\n\n"
                    f"👥 Foydalanuvchilar: {len(users)} ta\n"
                    f"📋 Vazifalar: {len(tasks)} ta\n"
                    f"💬 AI suhbat xabarlari: {len(chat_history)} ta"
                ),
                parse_mode="HTML",
            )
    except Exception as e:
        logger.error(f"/info bajarishda xatolik: {e}")
        await update.message.reply_text(f"❌ Xatolik yuz berdi: {e}")


async def forget_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    clear_chat_history(user_id)
    await update.message.reply_text("🧹 <b>AI suhbat xotirasi tozalandi!</b>", parse_mode="HTML")


async def add_task_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📝 <b>Yangi vazifa qo'shish uchun:</b>\n\n"
        "<code>/task [vazifa va vaqti]</code> ko'rinishida yozing.\n"
        "<i>Masalan:</i> <code>/task Ertaga soat 10:00 da hisobotni topshirish</code>",
        parse_mode="HTML",
    )


async def add_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    task_text = " ".join(context.args)
    if not task_text:
        await add_task_prompt(update, context)
        return

    await update.message.reply_chat_action("typing")
    info = extract_datetime(task_text)
    category = info.get("category", "📌 Shaxsiy")

    task_id = add_user_task(user_id, task_text, category)

    msg = f"✅ <b>Vazifa saqlandi!</b>\n\n🎯 <b>Vazifa:</b> {task_text}\n📂 <b>Kategoriya:</b> {category}"

    if info.get("has_time") and info.get("datetime"):
        set_proposed_reminder(task_id, info["datetime"])
        display = info.get("display") or info["datetime"]
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔔 Ha, eslatma qo'y", callback_data=f"remindyes_{task_id}"),
            InlineKeyboardButton("❌ Yo'q", callback_data=f"remindno_{task_id}"),
        ]])
        await update.message.reply_text(
            f"{msg}\n\n⏰ <b>Vaqt aniqlandi:</b> {display}\nEslatishni xohlaysizmi?",
            parse_mode="HTML",
            reply_markup=keyboard,
        )
    else:
        set_pending_action(user_id, "awaiting_time", task_id)
        await update.message.reply_text(
            f"{msg}\n\n⏰ <b>Eslatma vaqtini kiriting</b> (masalan: <i>'ertaga 15:00'</i> yoki <i>'yo'q'</i> deb yozing):",
            parse_mode="HTML",
        )


async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    tasks = get_user_tasks(user_id)

    if not tasks:
        await update.message.reply_text("📋 <b>Sizda hozircha vazifalar yo'q.</b>", parse_mode="HTML")
        return

    text = "📋 <b>Sizning vazifalaringiz ro'yxati:</b>\n\n"
    keyboard = []

    for idx, task in enumerate(tasks, 1):
        status = "✅" if task["done"] else "🔲"
        text += f"{idx}. {status} <b>{task['text']}</b>\n   └ {task['category']} | 📅 <i>{task['created']}</i>\n\n"
        
        btn_status = "↩️ Tiklash" if task["done"] else "✅ Bajarildi"
        keyboard.append([
            InlineKeyboardButton(f"{idx}. {btn_status}", callback_data=f"toggle_{task['id']}"),
            InlineKeyboardButton(f"{idx}. 🗑️ O'chirish", callback_data=f"delete_{task['id']}"),
        ])

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def clear_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    removed = clear_done_tasks(user_id)
    await update.message.reply_text(f"🗑️ <b>{removed} ta bajarilgan vazifa tozalandi!</b>", parse_mode="HTML")


async def debug_gemini(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    if user_id != str(ADMIN_ID):
        await update.message.reply_text("⛔ Bu buyruq faqat bot admini uchun!")
        return

    await update.message.reply_chat_action("typing")
    try:
        response = requests.post(
            GEMINI_URL,
            headers={"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY},
            json={"contents": [{"parts": [{"text": "Salom"}]}]},
            timeout=30,
        )
        safe_body = response.text.replace(GEMINI_API_KEY, "[YASHIRILGAN]")[:1000]
        await update.message.reply_text(
            f"🛠️ Debug Ma'lumoti:\n\nStatus: {response.status_code}\nJavob:\n{safe_body}"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Xatolik: {e}")


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    action, id_str = query.data.split("_")
    task_id = int(id_str)

    if action in ("remindyes", "remindno"):
        task = get_task(task_id)
        if task is None:
            await query.edit_message_text("❌ Bu vazifa topilmadi.")
            return
        if action == "remindyes" and task["proposed_remind_at"]:
            create_reminder(user_id, task_id, task["text"], task["proposed_remind_at"])
            set_proposed_reminder(task_id, None)
            await query.edit_message_text(
                f"🔔 <b>Eslatma o'rnatildi!</b>\n\n🎯 {task['text']}\n⏰ {task['proposed_remind_at']}",
                parse_mode="HTML",
            )
        else:
            set_proposed_reminder(task_id, None)
            await query.edit_message_text("Tushunarli, eslatma o'rnatilmadi.")
        return

    if action == "toggle":
        task = get_task(task_id)
        if task:
            toggle_task(task_id, not task["done"])
            await list_tasks_refresh(query, user_id)
    elif action == "delete":
        delete_task(task_id)
        await list_tasks_refresh(query, user_id)


async def list_tasks_refresh(query, user_id: str):
    tasks = get_user_tasks(user_id)
    if not tasks:
        await query.edit_message_text("📋 <b>Sizda boshqa vazifalar qolmapdi.</b>", parse_mode="HTML")
        return

    text = "📋 <b>Sizning vazifalaringiz ro'yxati:</b>\n\n"
    keyboard = []

    for idx, task in enumerate(tasks, 1):
        status = "✅" if task["done"] else "🔲"
        text += f"{idx}. {status} <b>{task['text']}</b>\n   └ {task['category']} | 📅 <i>{task['created']}</i>\n\n"
        btn_status = "↩️ Tiklash" if task["done"] else "✅ Bajarildi"
        keyboard.append([
            InlineKeyboardButton(f"{idx}. {btn_status}", callback_data=f"toggle_{task['id']}"),
            InlineKeyboardButton(f"{idx}. 🗑️ O'chirish", callback_data=f"delete_{task['id']}"),
        ])

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    save_or_update_user(str(user.id), user.username or "", user.full_name or "")
    
    user_id = str(user.id)
    user_message = update.message.text

    if user_message == "📝 Yangi vazifa":
        await add_task_prompt(update, context)
        return
    elif user_message == "📋 Vazifalarim":
        await list_tasks(update, context)
        return
    elif user_message == "🧹 Bajarilganlarni tozalash":
        await clear_done(update, context)
        return
    elif user_message == "🧠 AI Xotirasini tozalash":
        await forget_command(update, context)
        return

    pending = get_pending_action(user_id)
    if pending and pending["action"] == "awaiting_time":
        task_id = pending["task_id"]
        task = get_task(task_id)
        clear_pending_action(user_id)

        if user_message.strip().lower() in ("yo'q", "yoq", "kerak emas", "yo'q rahmat"):
            await update.message.reply_text("Tushunarli, eslatma qo'yilmadi.")
            return

        await update.message.reply_chat_action("typing")
        info = extract_datetime(user_message)
        if info.get("has_time") and info.get("datetime") and task:
            create_reminder(user_id, task_id, task["text"], info["datetime"])
            display = info.get("display") or info["datetime"]
            await update.message.reply_text(
                f"🔔 <b>Eslatma o'rnatildi!</b>\n\n🎯 {task['text']}\n⏰ {display}",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text("⚠️ Vaqtni aniqlay olmadim. Eslatma qo'yilmadi.")
        return

    await update.message.reply_chat_action("typing")
    reply = ask_gemini_chat(user_id, user_message)
    await update.message.reply_text(reply)


async def check_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    now_str = now_tashkent().strftime("%Y-%m-%d %H:%M")
    due = get_due_reminders(now_str)
    for reminder in due:
        try:
            await context.bot.send_message(
                chat_id=int(reminder["user_id"]),
                text=f"🔔 <b>Eslatma!</b>\n\n🎯 {reminder['task_text']}",
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error(f"Eslatma yuborishda xato: {e}")
        finally:
            mark_reminder_sent(reminder["id"])


def generate_islamic_quote() -> str:
    """Gemini API orqali har kuni yangi oyat va hadis generatsiya qiladi."""
    prompt = (
        "Menga har kunlik tonggi eslatma uchun bitta Qur'on oyati (surasi va oyat raqami bilan) "
        "yoki sahix hadis, va uning ketidan 1-2 jumladan iborat qisqa, ilhomlantiruvchi ta'sirli ma'naviy xulosa yozib ber. "
        "Javob o'zbek tilida, chiroyli formatda va emojilar bilan bo'lsin. Keraksiz kirish so'zlarisiz faqat matnning o'zini ber."
    )
    contents = [{"role": "user", "parts": [{"text": prompt}]}]
    reply = _gemini_request(contents, system_instruction="Sen islomiy bilimlar bo'yicha odobli va aniq yordamchisan.")
    
    if reply:
        return reply
    return "📖 <b>Qur'oni Karim:</b> «Albatta, qiyinchilik bilan birga yengillik bordir.» (Sharh surasi, 6-oyat)"


async def send_daily_quote(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Har kuni ertalab barcha foydalanuvchilarga AI tomonidan tayyorlangan yangi oyat/hadis yuboradi."""
    users = get_all_users()
    quote = generate_islamic_quote()
    message = f"☀️ <b>Xayrli tong!</b>\n\n{quote}"

    for u in users:
        try:
            await context.bot.send_message(
                chat_id=int(u["user_id"]),
                text=message,
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error(f"Ertalabki xabar yuborishda xatolik ({u['user_id']}): {e}")


def main() -> None:
    if not TELEGRAM_BOT_TOKEN or not GEMINI_API_KEY:
        raise RuntimeError("TOKEN yoki API KEY muhit o'zgaruvchisi topilmadi.")

    init_db()

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("task", add_task))
    app.add_handler(CommandHandler("list", list_tasks))
    app.add_handler(CommandHandler("clear", clear_done))
    app.add_handler(CommandHandler("forget", forget_command))
    app.add_handler(CommandHandler("debug", debug_gemini))
    app.add_handler(CommandHandler("info", export_info))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    if app.job_queue is not None:
        app.job_queue.run_repeating(check_reminders, interval=30, first=10)
        app.job_queue.run_daily(send_daily_quote, time=time(2, 0, 0))

    logger.info("Bot ishga tushmoqda...")
    app.run_polling()


if __name__ == "__main__":
    main()
