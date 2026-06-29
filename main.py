import asyncio
import aiosqlite
import html as html_module
import httpx
import logging
import math
import os
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
    CallbackQuery, Message
)
from flask import Flask
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Бот работает! ✅", 200

@app.route('/health')
def health():
    return "OK", 200
from aiogram.enums import ParseMode

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_ID"])
DB_PATH = "bot/referrals.db"

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

class AdminFSM(StatesGroup):
    editing_text   = State()
    editing_tasks  = State()
    broadcast      = State()
    finding_user   = State()
    stars_amount   = State()

class UserFSM(StatesGroup):
    waiting_sub   = State()

async def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                stars REAL DEFAULT 0,
                referrer_id INTEGER,
                reg_date TEXT,
                tgrass_remaining INTEGER DEFAULT -1,
                tgrass_initial INTEGER DEFAULT -1,
                tgrass_done INTEGER DEFAULT 0,
                skips_used INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS required_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_username TEXT,
                channel_link TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                gift_name TEXT,
                stars_spent INTEGER,
                purchase_date TEXT,
                status TEXT DEFAULT 'pending'
            )
        """)
        try:
            await db.execute("ALTER TABLE purchases ADD COLUMN status TEXT DEFAULT 'pending'")
            await db.commit()
        except Exception:
            pass  # колонка уже существует
        await db.execute("""
            CREATE TABLE IF NOT EXISTS banned_users (
                user_id INTEGER PRIMARY KEY,
                banned_at TEXT,
                reason TEXT DEFAULT ''
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS completed_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                tg_identifier TEXT NOT NULL,
                channel_name TEXT,
                rewarded_at TEXT
            )
        """)

        for col_sql in [
            "ALTER TABLE users ADD COLUMN tgrass_remaining INTEGER DEFAULT -1",
            "ALTER TABLE users ADD COLUMN tgrass_initial INTEGER DEFAULT -1",
            "ALTER TABLE users ADD COLUMN tgrass_done INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN skips_used INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN tgrass_subscribed_count INTEGER DEFAULT 0",
            "ALTER TABLE completed_channels ADD COLUMN offer_id TEXT",
        ]:
            try:
                await db.execute(col_sql)
            except Exception:
                pass

        defaults = {
            "welcome":          "👋 Добро пожаловать в бота!\n\nЗдесь ты можешь зарабатывать звёзды ⭐\nПриглашай друзей и получай бонусы!",
            "earn_text":        "🔗 Твоя реферальная ссылка:\n{link}\n\nЗа каждого друга ты получишь +3 ⭐",
            "balance_text":     "💰 Твой баланс: {stars} ⭐\n👥 Приглашено: {invited}",
            "withdraw_text":    "🎁 Выбери подарок:",
            "reviews_text":     "📢 Наш канал с отзывами:\nhttps://t.me/example",
            "skip_cost":        "0",
            "max_skips":        "3",
        }
        for key, value in defaults.items():
            await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))
        await db.commit()
    logger.info("✅ Database initialized")

async def get_setting(key: str) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cur:
            result = await cur.fetchone()
            return result[0] if result else ""

async def set_setting(key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        await db.commit()

async def user_exists(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,)) as cur:
            return await cur.fetchone() is not None

async def add_user(user_id: int, username: str, referrer_id: int | None = None) -> bool:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO users (user_id, username, referrer_id, reg_date) VALUES (?, ?, ?, ?)",
                (user_id, username, referrer_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
            await db.commit()
        return True
    except Exception as e:
        logger.error(f"❌ Error adding user: {e}")
        return False

async def get_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cur:
            return await cur.fetchone()

async def add_stars(user_id: int, amount: float):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET stars = stars + ? WHERE user_id = ?", (amount, user_id))
        await db.commit()

async def get_stars(user_id: int) -> float:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT stars FROM users WHERE user_id = ?", (user_id,)) as cur:
            result = await cur.fetchone()
            return float(result[0]) if result else 0.0

def fmt_stars(n: float) -> str:
    n = round(n, 1)
    return str(int(n)) if n == int(n) else f"{n:.1f}"

async def get_invite_count(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users WHERE referrer_id = ?", (user_id,)) as cur:
            result = await cur.fetchone()
            return result[0] if result else 0

async def update_username(user_id: int, username: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET username = ? WHERE user_id = ?", (username, user_id))
        await db.commit()

async def get_all_user_ids() -> list[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users") as cur:
            rows = await cur.fetchall()
            return [row[0] for row in rows]

async def get_user_referrer_id(user_id: int) -> int | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT referrer_id FROM users WHERE user_id = ?", (user_id,)) as cur:
            result = await cur.fetchone()
            return result[0] if result else None

async def get_tgrass_remaining(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT tgrass_remaining FROM users WHERE user_id = ?", (user_id,)) as cur:
            result = await cur.fetchone()
            return result[0] if result is not None else -1

async def set_tgrass_remaining(user_id: int, count: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET tgrass_remaining = ? WHERE user_id = ?", (count, user_id))
        await db.commit()

async def get_tgrass_done(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT tgrass_done FROM users WHERE user_id = ?", (user_id,)) as cur:
            result = await cur.fetchone()
            return result[0] if result is not None else 0

async def mark_tgrass_done(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET tgrass_done = 1 WHERE user_id = ?", (user_id,))
        await db.commit()

async def get_tgrass_initial(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT tgrass_initial FROM users WHERE user_id = ?", (user_id,)) as cur:
            result = await cur.fetchone()
            return result[0] if result is not None else -1

async def set_tgrass_initial(user_id: int, count: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET tgrass_initial = ? WHERE user_id = ?", (count, user_id))
        await db.commit()

async def get_skips_used(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT skips_used FROM users WHERE user_id = ?", (user_id,)) as cur:
            result = await cur.fetchone()
            return result[0] if result is not None else 0

async def increment_skips_used(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET skips_used = skips_used + 1 WHERE user_id = ?", (user_id,))
        await db.commit()

async def get_user_username(user_id: int) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT username FROM users WHERE user_id=?", (user_id,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else None

async def store_completed_channels(user_id: int, block_offers: list):
    """Сохраняем offer_id каналов завершённого блока для отслеживания отписки."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(DB_PATH) as db:
        for offer in block_offers:
            offer_id = str(offer.get("offer_id") or offer.get("id") or "")
            if not offer_id:
                continue
            name = offer.get("name") or offer.get("title") or ""
            async with db.execute(
                "SELECT id FROM completed_channels WHERE user_id=? AND offer_id=?",
                (user_id, offer_id)
            ) as cur:
                exists = await cur.fetchone()
            if not exists:
                await db.execute(
                    "INSERT INTO completed_channels (user_id, tg_identifier, channel_name, rewarded_at, offer_id)"
                    " VALUES (?,?,?,?,?)",
                    (user_id, offer_id, name, now, offer_id)
                )
        await db.commit()

async def get_completed_offer_ids(user_id: int) -> list[tuple]:
    """Возвращаем список (offer_id, channel_name) для пользователя."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT offer_id, channel_name FROM completed_channels WHERE user_id=? AND offer_id IS NOT NULL",
            (user_id,)
        ) as cur:
            return await cur.fetchall()

async def remove_completed_offer(user_id: int, offer_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM completed_channels WHERE user_id=? AND offer_id=?",
            (user_id, offer_id)
        )
        await db.commit()

async def get_all_tracked_user_ids() -> list[int]:
    """Все user_id у кого есть хоть один отслеживаемый канал."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT DISTINCT user_id FROM completed_channels WHERE offer_id IS NOT NULL"
        ) as cur:
            rows = await cur.fetchall()
            return [r[0] for r in rows]

async def _fetch_tgrass_for_uid(user_id: int) -> list:
    """Вызываем Tgrass API для фонового чекера (без объекта types.User)."""
    username = await get_user_username(user_id)
    payload = {
        "tg_user_id": user_id,
        "tg_login": username,
        "lang": "ru",
        "is_premium": False,
    }
    try:
        async with httpx.AsyncClient(verify=False, timeout=15) as client:
            resp = await client.post(
                TGRASS_API_URL,
                json=payload,
                headers={"accept": "application/json",
                         "Content-Type": "application/json",
                         "Auth": TGRASS_API_KEY},
            )
        if resp.status_code == 200:
            return resp.json().get("offers", [])
    except Exception as e:
        logger.warning(f"[BGCheck] Tgrass API error for {user_id}: {e}")
    return []

async def _handle_unsubscription(user_id: int, all_offers: list,
                                  bot_instance, notify_target=None) -> bool:
    """Сравниваем offer_id выполненных каналов с текущим ответом Tgrass.
    Если offer теперь subscribed=false — штрафуем и уведомляем."""
    tracked = await get_completed_offer_ids(user_id)
    if not tracked:
        return False

    # Строим словарь offer_id → subscribed из текущего ответа Tgrass
    offer_map = {str(o.get("offer_id") or o.get("id") or ""): o for o in all_offers}

    unsubbed = []
    for offer_id, channel_name in tracked:
        offer = offer_map.get(str(offer_id))
        if offer is None:
            continue  # оффер исчез из API — не штрафуем
        if not offer.get("subscribed", True):
            unsubbed.append((offer_id, channel_name or offer.get("name") or offer_id))

    if not unsubbed:
        return False

    unsubbed_count = len(unsubbed)
    deduct = unsubbed_count * TGRASS_STARS_PER_CHANNEL
    await add_stars(user_id, -deduct)
    for oid, _ in unsubbed:
        await remove_completed_offer(user_id, oid)
    new_balance = await get_stars(user_id)

    names_list = "\n".join(f"  ⭕ {name}" for _, name in unsubbed)
    noun = "канал" if unsubbed_count == 1 else "канала" if unsubbed_count <= 4 else "каналов"
    text = (
        f"⚠️ **Обнаружена отписка!**\n\n"
        f"Ты отписался от {unsubbed_count} {noun}:\n{names_list}\n\n"
        f"💸 Списано: -{fmt_stars(deduct)} ⭐\n"
        f"💰 Баланс: {fmt_stars(new_balance)} ⭐"
    )
    if new_balance < 0:
        text += "\n\n❗️ Баланс ушёл в минус — подпишись обратно, чтобы восстановить его."

    try:
        if notify_target is None:
            await bot_instance.send_message(user_id, text, parse_mode=ParseMode.MARKDOWN)
        elif isinstance(notify_target, Message):
            await notify_target.answer(text, parse_mode=ParseMode.MARKDOWN)
        else:
            await notify_target.message.answer(text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.warning(f"[UNSUB] Не удалось уведомить {user_id}: {e}")

    referrer_id = await get_user_referrer_id(user_id)
    if referrer_id and await user_exists(referrer_id):
        await add_stars(referrer_id, -REFERRAL_STARS)
        ref_balance = await get_stars(referrer_id)
        try:
            ref_text = (
                f"⚠️ **Реферал отписался от каналов**\n\n"
                f"Пользователь `{user_id}`, которого ты пригласил, "
                f"отписался от {unsubbed_count} {noun}.\n"
                f"💸 Реферальный бонус возвращён: -{REFERRAL_STARS} ⭐\n"
                f"💰 Твой баланс: {fmt_stars(ref_balance)} ⭐"
            )
            await bot_instance.send_message(referrer_id, ref_text, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            logger.warning(f"[UNSUB] Не удалось уведомить реферера {referrer_id}: {e}")

    logger.info(f"[UNSUB] user={user_id} unsubbed={unsubbed_count} deducted={deduct} new_balance={new_balance}")
    return True

async def add_purchase(user_id: int, username: str, gift_name: str, stars_spent: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO purchases (user_id, username, gift_name, stars_spent, purchase_date, status) VALUES (?, ?, ?, ?, ?, 'pending')",
            (user_id, username, gift_name, stars_spent, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        await db.commit()

async def get_pending_purchases() -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, user_id, username, gift_name, stars_spent, purchase_date FROM purchases WHERE status='pending' ORDER BY purchase_date ASC"
        ) as cur:
            return await cur.fetchall()

async def get_all_purchases_recent() -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, user_id, username, gift_name, stars_spent, purchase_date, status FROM purchases ORDER BY purchase_date DESC LIMIT 30"
        ) as cur:
            return await cur.fetchall()

async def mark_purchase_done(purchase_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE purchases SET status='done' WHERE id=?", (purchase_id,)
        )
        await db.commit()

async def get_channels() -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id, channel_username, channel_link FROM required_channels") as cur:
            return await cur.fetchall()

async def add_channel(username: str, link: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO required_channels (channel_username, channel_link) VALUES (?, ?)", (username, link)
        )
        await db.commit()

async def delete_channel(channel_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM required_channels WHERE id = ?", (channel_id,))
        await db.commit()

async def ban_user(user_id: int, reason: str = ""):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO banned_users (user_id, banned_at, reason) VALUES (?, ?, ?)",
            (user_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), reason)
        )
        await db.commit()

async def unban_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM banned_users WHERE user_id = ?", (user_id,))
        await db.commit()

async def is_banned(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM banned_users WHERE user_id = ?", (user_id,)) as cur:
            return await cur.fetchone() is not None

async def get_top_users() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT referrer_id, COUNT(*) as cnt
            FROM users WHERE referrer_id IS NOT NULL
            GROUP BY referrer_id ORDER BY cnt DESC LIMIT 10
        """) as cur:
            top_invites = await cur.fetchall()
        async with db.execute("SELECT user_id, stars FROM users ORDER BY stars DESC LIMIT 10") as cur:
            top_stars = await cur.fetchall()
    return {"top_invites": top_invites, "top_stars": top_stars}

async def get_user_rank(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT referrer_id, COUNT(*) as cnt
            FROM users WHERE referrer_id IS NOT NULL
            GROUP BY referrer_id ORDER BY cnt DESC
        """) as cur:
            rows = await cur.fetchall()
    for i, (uid, _) in enumerate(rows, 1):
        if uid == user_id:
            return i
    return len(rows) + 1

async def get_bot_stats() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cur:
            total_users = (await cur.fetchone())[0]
        async with db.execute("SELECT SUM(stars) FROM users") as cur:
            total_stars = (await cur.fetchone())[0] or 0
        async with db.execute("""
            SELECT user_id, username, gift_name, stars_spent, purchase_date
            FROM purchases ORDER BY purchase_date DESC LIMIT 20
        """) as cur:
            purchases = await cur.fetchall()
        async with db.execute("SELECT COUNT(*) FROM banned_users") as cur:
            total_banned = (await cur.fetchone())[0]
    return {"total_users": total_users, "total_stars": total_stars,
            "purchases": purchases, "total_banned": total_banned}

async def get_banned_list() -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT b.user_id, u.username, b.banned_at, b.reason
            FROM banned_users b
            LEFT JOIN users u ON u.user_id = b.user_id
            ORDER BY b.banned_at DESC
            LIMIT 50
        """) as cur:
            return await cur.fetchall()

async def check_subscription(user_id: int) -> bool:
    channels = await get_channels()
    if not channels:
        return True
    for _, channel_username, _ in channels:
        try:
            member = await bot.get_chat_member(f"@{channel_username}", user_id)
            if member.status in ("left", "kicked", "restricted"):
                return False
        except Exception as e:
            logger.warning(f"⚠️ Не удалось проверить @{channel_username} для {user_id}: {e}")
            return False
    return True

async def send_subscription_prompt(msg_or_cb, channels: list):
    text = "📢 **Для использования бота подпишись на каналы:**\n\n"
    buttons = []
    for _, uname, link in channels:
        text += f"📌 @{uname}\n"
        buttons.append([InlineKeyboardButton(text=f"📌 Подписаться на @{uname}", url=link)])
    buttons.append([InlineKeyboardButton(text="✅ Я подписался — проверить", callback_data="check_sub")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    if isinstance(msg_or_cb, CallbackQuery):
        try:
            await msg_or_cb.message.edit_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
        except Exception:
            await msg_or_cb.message.answer(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
    else:
        await msg_or_cb.answer(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

TGRASS_API_URL = "https://tgrass.space/offers"
TGRASS_API_KEY = "6c008c66eb5d456987a3b2b60d344df5"
TGRASS_CHECK_CALLBACK = "check_tgrass"
TGRASS_SKIP_CALLBACK  = "skip_tgrass_block"
TGRASS_STARS_PER_CHANNEL = 0.8
TGRASS_BLOCK_SIZE = 4
REFERRAL_STARS = 3

def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💰 Заработать"), KeyboardButton(text="💎 Баланс")],
            [KeyboardButton(text="🎁 Вывод"),       KeyboardButton(text="🏆 Топ")],
            [KeyboardButton(text="📋 Задания"),      KeyboardButton(text="📢 Отзывы")],
        ],
        resize_keyboard=True
    )

def gifts_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧸 Мишка (15⭐)", callback_data="gift_bear")],
        [InlineKeyboardButton(text="💝 Сердце (15⭐)", callback_data="gift_heart")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
    ])

def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить тексты",    callback_data="admin_edit_texts")],
        [InlineKeyboardButton(text="📣 Рассылка",            callback_data="admin_mailing")],
        [InlineKeyboardButton(text="📢 Каналы подписки",     callback_data="admin_channels")],
        [InlineKeyboardButton(text="📊 Статистика",          callback_data="admin_stats")],
        [InlineKeyboardButton(text="📦 Заказы на вывод",      callback_data="admin_purchases")],
        [InlineKeyboardButton(text="🔍 Найти пользователя",  callback_data="admin_find_user")],
        [InlineKeyboardButton(text="🚫 Список банов",        callback_data="admin_bans")],
        [InlineKeyboardButton(text="⚙️ Настройки пропуска",  callback_data="admin_skip_settings")],
    ])

def edit_texts_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Приветствие",  callback_data="edit_welcome")],
        [InlineKeyboardButton(text="📝 Заработать",   callback_data="edit_earn_text")],
        [InlineKeyboardButton(text="📝 Баланс",       callback_data="edit_balance_text")],
        [InlineKeyboardButton(text="📝 Вывод",        callback_data="edit_withdraw_text")],
        [InlineKeyboardButton(text="📝 Отзывы",       callback_data="edit_reviews_text")],
        [InlineKeyboardButton(text="📋 Задания",      callback_data="edit_tasks_text")],
        [InlineKeyboardButton(text="🔙 Назад",        callback_data="admin_back")]
    ])

SETTING_NAMES = {
    "welcome":       "Приветствие",
    "earn_text":     "Заработать",
    "balance_text":  "Баланс",
    "withdraw_text": "Вывод",
    "reviews_text":  "Отзывы",
    "tasks_text":    "Задания",
}

SETTING_VARS = {
    "welcome":       ["{name}", "{username}", "{stars}", "{invited}", "{top_place}"],
    "earn_text":     ["{name}", "{username}", "{stars}", "{invited}", "{top_place}", "{link}"],
    "balance_text":  ["{name}", "{username}", "{stars}", "{invited}", "{top_place}"],
    "withdraw_text": ["{name}", "{username}", "{stars}", "{invited}", "{top_place}"],
    "reviews_text":  ["{name}", "{username}"],
}

VAR_DESCRIPTIONS = {
    "{name}":      "имя пользователя",
    "{username}":  "@юзернейм",
    "{stars}":     "баланс звёзд",
    "{invited}":   "кол-во приглашённых",
    "{top_place}": "место в топе по приглашениям",
    "{link}":      "реферальная ссылка",
}

def apply_vars(text: str, user_id: int = 0, first_name: str = "",
               username: str = "", stars: float = 0,
               invited: int = 0, link: str = "",
               top_place: int = 0) -> str:
    uname = f"@{username}" if username and not username.startswith("@") else username
    return (
        text
        .replace("{name}",      first_name or str(user_id))
        .replace("{username}",  uname or str(user_id))
        .replace("{stars}",     fmt_stars(stars))
        .replace("{invited}",   str(invited))
        .replace("{top_place}", f"#{top_place}" if top_place else "—")
        .replace("{link}",      link)
    )

def mask_id(user_id: int) -> str:
    uid = str(user_id)
    return uid[:2] + "***" + uid[-2:] if len(uid) > 4 else uid

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

BAN_REPLY = "🚫 Вы заблокированы и не можете использовать этого бота."

async def _complete_start(user_id: int, username: str, first_name: str,
                          referrer_id: int | None, reply_to):
    if not await user_exists(user_id):
        await add_user(user_id, username, referrer_id)
        if referrer_id and await user_exists(referrer_id):
            try:
                await bot.send_message(
                    referrer_id,
                    f"👤 По вашей ссылке зарегистрировался @{username}!\n"
                    f"🎯 Вы получите +{REFERRAL_STARS} ⭐ когда он выполнит первый блок заданий."
                )
            except Exception:
                pass
    else:
        user_data = await get_user(user_id)
        if user_data and user_data[1] != username:
            await update_username(user_id, username)

    stars     = await get_stars(user_id)
    invited   = await get_invite_count(user_id)
    top_place = await get_user_rank(user_id)
    bot_info  = await bot.get_me()
    link      = f"https://t.me/{bot_info.username}?start={user_id}"

    welcome = apply_vars(
        await get_setting("welcome"),
        user_id=user_id, first_name=first_name, username=username,
        stars=stars, invited=invited, link=link, top_place=top_place,
    )

    chat_id = reply_to.from_user.id if isinstance(reply_to, CallbackQuery) else reply_to.chat.id
    await bot.send_message(chat_id, welcome, reply_markup=main_keyboard())


@dp.message(Command("start"))
async def start(message: Message, state: FSMContext):
    user_id    = message.from_user.id
    username   = message.from_user.username or f"user_{user_id}"
    first_name = message.from_user.first_name or username

    if not is_admin(user_id) and await is_banned(user_id):
        await message.answer(BAN_REPLY)
        return

    parts = message.text.split()
    referrer_id = None
    if len(parts) > 1:
        try:
            ref = int(parts[1])
            if ref != user_id:
                referrer_id = ref
        except ValueError:
            pass

    channels = await get_channels()
    if channels and not await check_subscription(user_id):
        await state.set_state(UserFSM.waiting_sub)
        await state.update_data(referrer_id=referrer_id, username=username, first_name=first_name)
        await send_subscription_prompt(message, channels)
        return

    await state.clear()
    await _complete_start(user_id, username, first_name, referrer_id, message)


@dp.message(F.text == "💰 Заработать")
async def earn(message: Message):
    if not is_admin(message.from_user.id) and await is_banned(message.from_user.id):
        await message.answer(BAN_REPLY)
        return
    channels = await get_channels()
    if channels and not await check_subscription(message.from_user.id):
        await send_subscription_prompt(message, channels)
        return
    user_id    = message.from_user.id
    username   = message.from_user.username or f"user_{user_id}"
    first_name = message.from_user.first_name or username
    bot_info   = await bot.get_me()
    link       = f"https://t.me/{bot_info.username}?start={user_id}"
    stars      = await get_stars(user_id)
    invited    = await get_invite_count(user_id)
    top_place  = await get_user_rank(user_id)
    text       = apply_vars(
        await get_setting("earn_text"),
        user_id=user_id, first_name=first_name, username=username,
        stars=stars, invited=invited, link=f"`{link}`", top_place=top_place,
    )
    share_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Поделиться", url=f"https://t.me/share/url?url={link}")]
    ])
    await message.answer(text, parse_mode=ParseMode.MARKDOWN, reply_markup=share_kb)


@dp.message(F.text == "💎 Баланс")
async def balance(message: Message):
    if not is_admin(message.from_user.id) and await is_banned(message.from_user.id):
        await message.answer(BAN_REPLY)
        return
    channels = await get_channels()
    if channels and not await check_subscription(message.from_user.id):
        await send_subscription_prompt(message, channels)
        return
    user_id    = message.from_user.id
    username   = message.from_user.username or f"user_{user_id}"
    first_name = message.from_user.first_name or username
    stars      = await get_stars(user_id)
    invited    = await get_invite_count(user_id)
    top_place  = await get_user_rank(user_id)
    text       = apply_vars(
        await get_setting("balance_text"),
        user_id=user_id, first_name=first_name, username=username,
        stars=stars, invited=invited, top_place=top_place,
    )
    await message.answer(text)


@dp.message(F.text == "🎁 Вывод")
async def withdraw(message: Message):
    if not is_admin(message.from_user.id) and await is_banned(message.from_user.id):
        await message.answer(BAN_REPLY)
        return
    channels = await get_channels()
    if channels and not await check_subscription(message.from_user.id):
        await send_subscription_prompt(message, channels)
        return
    user_id    = message.from_user.id
    username   = message.from_user.username or f"user_{user_id}"
    first_name = message.from_user.first_name or username
    stars      = await get_stars(user_id)
    invited    = await get_invite_count(user_id)
    top_place  = await get_user_rank(user_id)
    text       = apply_vars(
        await get_setting("withdraw_text"),
        user_id=user_id, first_name=first_name, username=username,
        stars=stars, invited=invited, top_place=top_place,
    )
    await message.answer(text, reply_markup=gifts_keyboard())


@dp.callback_query(F.data.startswith("gift_"))
async def process_gift(callback: CallbackQuery):
    user_id  = callback.from_user.id
    username = callback.from_user.username or f"user_{user_id}"
    gift_type = callback.data.split("_")[1]

    stars = await get_stars(user_id)
    if stars < 15:
        await callback.answer("❌ Недостаточно звёзд! Нужно 15 ⭐", show_alert=True)
        return

    await add_stars(user_id, -15)
    gift_name = "🧸 Мишка" if gift_type == "bear" else "💝 Сердце"
    await add_purchase(user_id, username, gift_name, 15)
    new_balance = await get_stars(user_id)

    await callback.message.edit_text(
        f"✅ Поздравляю! Ты получил {gift_name}!\n"
        f"Остаток: {fmt_stars(new_balance)} ⭐\n\n"
        f"📩 Администратор получил уведомление о твоём заказе.\n"
        f"Ожидай выдачи подарка!"
    )
    await callback.answer()

    try:
        await bot.send_message(
            ADMIN_ID,
            f"🎁 **НОВЫЙ ВЫВОД ПОДАРКА!**\n\n"
            f"👤 Пользователь: @{username}\n"
            f"🆔 ID: `{user_id}`\n"
            f"🎁 Подарок: {gift_name}\n"
            f"⭐ Потрачено: 15 звёзд\n"
            f"📅 Время: {datetime.now().strftime('%d.%m.%Y %H:%M')}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📩 Написать пользователю", url=f"tg://user?id={user_id}")]
            ])
        )
    except Exception as e:
        logger.error(f"❌ Не удалось уведомить администратора: {e}")


@dp.message(F.text == "🏆 Топ")
async def show_top(message: Message):
    if not is_admin(message.from_user.id) and await is_banned(message.from_user.id):
        await message.answer(BAN_REPLY)
        return
    channels = await get_channels()
    if channels and not await check_subscription(message.from_user.id):
        await send_subscription_prompt(message, channels)
        return
    top  = await get_top_users()
    text = "🏆 **ТОП ПОЛЬЗОВАТЕЛЕЙ**\n\n"
    text += "📊 **По приглашениям:**\n"
    for i, (uid, count) in enumerate(top["top_invites"], 1):
        text += f"{i}. `{mask_id(uid)}` — {count} приглаш.\n"
    if not top["top_invites"]:
        text += "Данных пока нет.\n"
    text += "\n⭐ **По звёздам:**\n"
    for i, (uid, stars) in enumerate(top["top_stars"], 1):
        text += f"{i}. `{mask_id(uid)}` — {fmt_stars(stars)} ⭐\n"
    if not top["top_stars"]:
        text += "Данных пока нет.\n"
    await message.answer(text, parse_mode=ParseMode.MARKDOWN)


@dp.message(F.text == "📢 Отзывы")
async def reviews(message: Message):
    if not is_admin(message.from_user.id) and await is_banned(message.from_user.id):
        await message.answer(BAN_REPLY)
        return
    channels = await get_channels()
    if channels and not await check_subscription(message.from_user.id):
        await send_subscription_prompt(message, channels)
        return
    user_id    = message.from_user.id
    username   = message.from_user.username or f"user_{user_id}"
    first_name = message.from_user.first_name or username
    text       = apply_vars(
        await get_setting("reviews_text"),
        user_id=user_id, first_name=first_name, username=username,
    )
    await message.answer(text)


@dp.callback_query(F.data == "check_sub")
async def check_sub(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id

    if not await check_subscription(user_id):
        channels = await get_channels()
        not_subbed = []
        for _, uname, link in channels:
            try:
                member = await bot.get_chat_member(f"@{uname}", user_id)
                if member.status in ("left", "kicked", "restricted"):
                    not_subbed.append(f"@{uname}")
            except Exception:
                pass
        names = ", ".join(not_subbed) if not_subbed else "необходимые каналы"
        await callback.answer(f"❌ Не подписан на: {names}", show_alert=True)
        return

    await callback.answer("✅ Проверка пройдена!", show_alert=False)

    data        = await state.get_data()
    referrer_id = data.get("referrer_id")
    username    = data.get("username") or callback.from_user.username or f"user_{user_id}"
    first_name  = data.get("first_name") or callback.from_user.first_name or username
    await state.clear()

    try:
        await callback.message.delete()
    except Exception:
        pass
    await _complete_start(user_id, username, first_name, referrer_id, callback)


@dp.callback_query(F.data == "cancel")
async def cancel(callback: CallbackQuery):
    await callback.message.delete()
    await callback.answer()


# ============================================================
# ADMIN HANDLERS
# ============================================================

@dp.message(Command("console"))
async def admin_panel(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Доступ запрещён!")
        return
    await message.answer(
        "🛠 **Панель разработчика**\nВыберите действие:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=admin_keyboard()
    )


@dp.callback_query(F.data == "admin_edit_texts")
async def admin_edit_texts(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён!", show_alert=True)
        return
    await callback.message.edit_text(
        "✏️ **Редактирование текстов**\nВыберите раздел:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=edit_texts_keyboard()
    )
    await callback.answer()


@dp.callback_query(F.data == "edit_tasks_text")
async def edit_tasks_text_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён!", show_alert=True)
        return
    current_text  = await get_setting("tasks_text")
    current_media = await get_setting("tasks_text_media_type")
    media_hint = f"\n🖼 Текущее медиа: **{current_media}**" if current_media else "\nМедиа: нет"
    await state.set_state(AdminFSM.editing_tasks)
    await callback.message.answer(
        f"✏️ **Редактирование раздела «Задания»**\n\n"
        f"Текущий текст:\n```\n{current_text or '(пусто)'}\n```"
        f"{media_hint}\n\n"
        f"Отправьте новый текст, фото или видео (с подписью или без).\n"
        f"/cancel — отмена, /clear\\_media — убрать медиа",
        parse_mode=ParseMode.MARKDOWN
    )
    await callback.answer()


@dp.message(AdminFSM.editing_tasks, Command("cancel"))
async def cancel_editing_tasks(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("🛠 **Панель разработчика**", parse_mode=ParseMode.MARKDOWN,
                         reply_markup=admin_keyboard())


@dp.message(AdminFSM.editing_tasks, Command("clear_media"))
async def clear_tasks_media(message: Message, state: FSMContext):
    await set_setting("tasks_text_media_type", "")
    await set_setting("tasks_text_media_id", "")
    await state.clear()
    await message.answer("✅ Медиа для раздела «Задания» удалено.", parse_mode=ParseMode.MARKDOWN)


@dp.message(AdminFSM.editing_tasks)
async def save_tasks_text(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    if message.photo:
        file_id = message.photo[-1].file_id
        await set_setting("tasks_text_media_type", "photo")
        await set_setting("tasks_text_media_id", file_id)
        if message.caption:
            await set_setting("tasks_text", message.caption)
        await state.clear()
        await message.answer("✅ Фото для раздела «Задания» сохранено!", parse_mode=ParseMode.MARKDOWN)
    elif message.video:
        file_id = message.video.file_id
        await set_setting("tasks_text_media_type", "video")
        await set_setting("tasks_text_media_id", file_id)
        if message.caption:
            await set_setting("tasks_text", message.caption)
        await state.clear()
        await message.answer("✅ Видео для раздела «Задания» сохранено!", parse_mode=ParseMode.MARKDOWN)
    elif message.text:
        await set_setting("tasks_text", message.text)
        await state.clear()
        await message.answer("✅ Текст раздела «Задания» обновлён!", parse_mode=ParseMode.MARKDOWN)
    else:
        await message.answer("❌ Поддерживаются текст, фото и видео. Попробуйте ещё раз.")


@dp.callback_query(F.data.startswith("edit_"))
async def start_edit_text(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён!", show_alert=True)
        return
    key = callback.data[5:]
    if key not in SETTING_NAMES:
        await callback.answer()
        return
    current = await get_setting(key)
    vars_list = "\n".join(f"  `{v}` — {VAR_DESCRIPTIONS.get(v,'')}" for v in SETTING_VARS.get(key, []))
    await state.set_state(AdminFSM.editing_text)
    await state.update_data(editing_key=key)
    await callback.message.answer(
        f"✏️ Редактирование **{SETTING_NAMES[key]}**\n\n"
        f"Текущий текст:\n```\n{current}\n```\n\n"
        f"Доступные переменные:\n{vars_list}\n\n"
        f"Отправьте новый текст или /cancel для отмены:",
        parse_mode=ParseMode.MARKDOWN
    )
    await callback.answer()


@dp.message(AdminFSM.editing_text, Command("cancel"))
async def cancel_editing(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("🛠 **Панель разработчика**\nВыберите действие:",
                         parse_mode=ParseMode.MARKDOWN, reply_markup=admin_keyboard())


@dp.message(AdminFSM.editing_text)
async def save_edited_text(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    key  = data.get("editing_key")
    if not key:
        await state.clear()
        return
    await set_setting(key, message.text or "")
    await state.clear()
    await message.answer(f"✅ Текст **{SETTING_NAMES.get(key, key)}** обновлён!", parse_mode=ParseMode.MARKDOWN)


@dp.callback_query(F.data == "admin_mailing")
async def admin_mailing_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён!", show_alert=True)
        return
    await state.set_state(AdminFSM.broadcast)
    await callback.message.answer(
        "📣 **Рассылка**\n\nОтправьте текст сообщения (или /cancel):",
        parse_mode=ParseMode.MARKDOWN
    )
    await callback.answer()


@dp.message(AdminFSM.broadcast, Command("cancel"))
async def cancel_broadcast(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("🛠 **Панель разработчика**",
                         parse_mode=ParseMode.MARKDOWN, reply_markup=admin_keyboard())


@dp.message(AdminFSM.broadcast)
async def do_broadcast(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    user_ids = await get_all_user_ids()
    ok = 0
    fail = 0
    for uid in user_ids:
        try:
            await bot.send_message(uid, message.text or "")
            ok += 1
        except Exception:
            fail += 1
    await message.answer(f"✅ Рассылка завершена!\n✉️ Отправлено: {ok}\n❌ Ошибок: {fail}")


@dp.callback_query(F.data == "admin_channels")
async def admin_channels(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён!", show_alert=True)
        return
    channels = await get_channels()
    text = "📢 **Каналы обязательной подписки:**\n\n"
    buttons = []
    if channels:
        for ch_id, uname, link in channels:
            text += f"• @{uname} — {link}\n"
            buttons.append([InlineKeyboardButton(text=f"🗑 Удалить @{uname}", callback_data=f"del_ch_{ch_id}")])
    else:
        text += "_Нет каналов_\n"
    buttons.append([InlineKeyboardButton(text="➕ Добавить канал", callback_data="add_channel")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")])
    await callback.message.edit_text(text, parse_mode=ParseMode.MARKDOWN,
                                      reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()


@dp.callback_query(F.data.startswith("del_ch_"))
async def delete_channel_cb(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    ch_id = int(callback.data.split("_")[-1])
    await delete_channel(ch_id)
    await callback.answer("✅ Канал удалён")
    channels = await get_channels()
    text = "📢 **Каналы обязательной подписки:**\n\n"
    buttons = []
    if channels:
        for cid, uname, link in channels:
            text += f"• @{uname} — {link}\n"
            buttons.append([InlineKeyboardButton(text=f"🗑 Удалить @{uname}", callback_data=f"del_ch_{cid}")])
    else:
        text += "_Нет каналов_\n"
    buttons.append([InlineKeyboardButton(text="➕ Добавить канал", callback_data="add_channel")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")])
    await callback.message.edit_text(text, parse_mode=ParseMode.MARKDOWN,
                                      reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@dp.callback_query(F.data == "add_channel")
async def add_channel_prompt(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    await callback.message.answer(
        "Отправьте данные канала в формате:\n`username https://t.me/username`\n\nНапример: `mychannel https://t.me/mychannel`\n\nИли /cancel для отмены.",
        parse_mode=ParseMode.MARKDOWN
    )
    await state.set_state(AdminFSM.editing_text)
    await state.update_data(editing_key="__add_channel__")
    await callback.answer()


@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    stats = await get_bot_stats()
    text = (
        f"📊 **Статистика бота**\n\n"
        f"👥 Пользователей: {stats['total_users']}\n"
        f"⭐ Всего звёзд: {fmt_stars(stats['total_stars'])}\n"
        f"🚫 Забанено: {stats['total_banned']}\n"
    )
    await callback.message.edit_text(text, parse_mode=ParseMode.MARKDOWN,
                                      reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                          [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")]
                                      ]))
    await callback.answer()


def _purchases_keyboard(pending: list, show_history: bool = False) -> InlineKeyboardMarkup:
    buttons = []
    for row_id, uid, uname, gift, spent, date in pending:
        label = f"✅ Выдано — @{uname or uid} · {gift}"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"done_purchase_{row_id}")])
    buttons.append([
        InlineKeyboardButton(
            text="📜 История выводов" if not show_history else "📦 Ожидают выдачи",
            callback_data="admin_purchases_history" if not show_history else "admin_purchases"
        )
    ])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

@dp.callback_query(F.data == "admin_purchases")
async def admin_purchases(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    pending = await get_pending_purchases()
    if not pending:
        text = "📦 **Заказы на вывод**\n\nНет ожидающих заказов ✅"
    else:
        text = f"📦 **Заказы на вывод** — {len(pending)} ожидает:\n\n"
        for row_id, uid, uname, gift, spent, date in pending:
            text += f"🆔 `{uid}` @{uname or '—'}\n🎁 {gift} · {spent}⭐ · {date}\n\n"
    await callback.message.edit_text(
        text, parse_mode=ParseMode.MARKDOWN,
        reply_markup=_purchases_keyboard(pending)
    )
    await callback.answer()

@dp.callback_query(F.data == "admin_purchases_history")
async def admin_purchases_history(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    all_p = await get_all_purchases_recent()
    if not all_p:
        text = "📜 История выводов пуста."
    else:
        text = "📜 **Последние 30 заказов:**\n\n"
        for row_id, uid, uname, gift, spent, date, status in all_p:
            icon = "✅" if status == "done" else "⏳"
            text += f"{icon} `{uid}` @{uname or '—'} — {gift} ({spent}⭐) — {date}\n"
    await callback.message.edit_text(
        text, parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📦 Ожидают выдачи", callback_data="admin_purchases")],
            [InlineKeyboardButton(text="🔙 Назад",          callback_data="admin_back")],
        ])
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("done_purchase_"))
async def done_purchase_handler(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    purchase_id = int(callback.data.split("_")[2])
    # Получаем данные заказа перед пометкой
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id, username, gift_name FROM purchases WHERE id=? AND status='pending'",
            (purchase_id,)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        await callback.answer("⚠️ Заказ уже выдан или не найден.", show_alert=True)
        return
    user_id, uname, gift_name = row
    await mark_purchase_done(purchase_id)
    # Уведомляем пользователя
    try:
        await callback.bot.send_message(
            user_id,
            f"🎉 **Твой вывод выполнен!**\n\n"
            f"🎁 {gift_name}\n\n"
            f"Спасибо, что пользуешься нашим ботом! 💫",
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        logger.warning(f"[ORDER] Не удалось уведомить {user_id}: {e}")
    await callback.answer(f"✅ Заказ #{purchase_id} помечен как выданный", show_alert=False)
    # Обновляем список
    pending = await get_pending_purchases()
    if not pending:
        text = "📦 **Заказы на вывод**\n\nНет ожидающих заказов ✅"
    else:
        text = f"📦 **Заказы на вывод** — {len(pending)} ожидает:\n\n"
        for row_id, uid, uname2, gift, spent, date in pending:
            text += f"🆔 `{uid}` @{uname2 or '—'}\n🎁 {gift} · {spent}⭐ · {date}\n\n"
    await callback.message.edit_text(
        text, parse_mode=ParseMode.MARKDOWN,
        reply_markup=_purchases_keyboard(pending)
    )


@dp.callback_query(F.data == "admin_bans")
async def admin_bans(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    bans = await get_banned_list()
    if not bans:
        text = "🚫 Список банов пуст."
    else:
        text = "🚫 **Забаненные пользователи:**\n\n"
        for uid, uname, banned_at, reason in bans:
            text += f"• `{uid}` (@{uname or '—'}) — {banned_at}\n"
    await callback.message.edit_text(text, parse_mode=ParseMode.MARKDOWN,
                                      reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                          [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")]
                                      ]))
    await callback.answer()


@dp.callback_query(F.data == "admin_skip_settings")
async def admin_skip_settings(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    skip_cost = await get_setting("skip_cost")
    max_skips = await get_setting("max_skips")
    text = (
        f"⚙️ **Настройки пропуска блоков**\n\n"
        f"💰 Стоимость пропуска: **{skip_cost} ⭐**\n"
        f"🔢 Макс. пропусков: **{max_skips}** (0 = без лимита)\n\n"
        f"Команды для изменения:\n"
        f"`/set_skip_cost <число>` — изменить стоимость\n"
        f"`/set_max_skips <число>` — изменить лимит"
    )
    await callback.message.edit_text(text, parse_mode=ParseMode.MARKDOWN,
                                      reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                          [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")]
                                      ]))
    await callback.answer()


@dp.message(Command("set_skip_cost"))
async def set_skip_cost_cmd(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Использование: /set_skip_cost <число>")
        return
    try:
        cost = max(0, int(parts[1]))
        await set_setting("skip_cost", str(cost))
        await message.answer(f"✅ Стоимость пропуска блока установлена: {cost} ⭐")
    except ValueError:
        await message.answer("❌ Укажите целое число.")


@dp.message(Command("set_max_skips"))
async def set_max_skips_cmd(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Использование: /set_max_skips <число> (0 = без лимита)")
        return
    try:
        val = max(0, int(parts[1]))
        await set_setting("max_skips", str(val))
        limit_text = "без лимита" if val == 0 else str(val)
        await message.answer(f"✅ Лимит пропусков установлен: {limit_text}")
    except ValueError:
        await message.answer("❌ Укажите целое число.")


async def _send_user_card(target, target_id: int):
    user_data = await get_user(target_id)
    if not user_data:
        text = f"❌ Пользователь `{target_id}` не найден."
        if isinstance(target, Message):
            await target.answer(text, parse_mode=ParseMode.MARKDOWN)
        else:
            await target.message.answer(text, parse_mode=ParseMode.MARKDOWN)
        return
    uid, uname, stars, referrer, reg, *rest = user_data
    banned = await is_banned(target_id)
    skips_used = await get_skips_used(target_id)
    referral_count = await get_invite_count(target_id)
    text = (
        f"👤 **Пользователь:** @{uname or '—'}\n"
        f"🆔 **ID:** `{uid}`\n"
        f"⭐ **Звёзд:** {fmt_stars(stars or 0)}\n"
        f"👥 **Реферер:** `{referrer or '—'}`\n"
        f"🤝 **Рефералов привёл:** {referral_count}\n"
        f"📅 **Дата рег.:** {reg or '—'}\n"
        f"⏭ **Пропусков:** {skips_used}\n"
        f"🚫 **Забанен:** {'Да' if banned else 'Нет'}"
    )
    ban_btn_text = "✅ Разблокировать" if banned else "🚫 Заблокировать"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➕ Начислить ⭐", callback_data=f"stars_add_{target_id}"),
            InlineKeyboardButton(text="➖ Списать ⭐",   callback_data=f"stars_ded_{target_id}"),
        ],
        [InlineKeyboardButton(text=ban_btn_text,         callback_data=f"ban_toggle_{target_id}")],
        [InlineKeyboardButton(text="📩 Написать",        url=f"tg://user?id={target_id}")],
        [InlineKeyboardButton(text="🔙 В панель",        callback_data="admin_back")],
    ])
    if isinstance(target, Message):
        await target.answer(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
    else:
        await target.message.answer(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


@dp.callback_query(F.data == "admin_find_user")
async def admin_find_user_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён!", show_alert=True)
        return
    await state.set_state(AdminFSM.finding_user)
    await callback.message.edit_text(
        "🔍 Введите Telegram ID пользователя:\n_(или /cancel для отмены)_",
        parse_mode=ParseMode.MARKDOWN,
    )
    await callback.answer()


@dp.message(AdminFSM.finding_user, Command("cancel"))
async def cancel_finding_user(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("🛠 **Панель разработчика**",
                         parse_mode=ParseMode.MARKDOWN, reply_markup=admin_keyboard())


@dp.message(AdminFSM.finding_user)
async def admin_finding_user_handle(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    try:
        target_id = int(message.text.strip())
    except (ValueError, AttributeError):
        await message.answer("❌ Введите числовой ID. Попробуйте ещё раз или /cancel.")
        return
    if not await user_exists(target_id):
        await message.answer(f"❌ Пользователь `{target_id}` не найден.\nВведите другой ID или /cancel.",
                             parse_mode=ParseMode.MARKDOWN)
        return
    await state.clear()
    await _send_user_card(message, target_id)


@dp.callback_query(F.data.startswith("stars_add_") | F.data.startswith("stars_ded_"))
async def stars_inline_action(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён!", show_alert=True)
        return
    data      = callback.data
    action    = "add" if data.startswith("stars_add_") else "ded"
    target_id = int(data.split("_")[-1])
    await state.set_state(AdminFSM.stars_amount)
    await state.update_data(stars_action=action, stars_target=target_id)
    verb = "начислить" if action == "add" else "списать"
    await callback.message.answer(
        f"✏️ Сколько ⭐ {verb} пользователю `{target_id}`?\n_(или /cancel)_",
        parse_mode=ParseMode.MARKDOWN
    )
    await callback.answer()


@dp.message(AdminFSM.stars_amount, Command("cancel"))
async def cancel_stars_amount(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("🛠 **Панель разработчика**",
                         parse_mode=ParseMode.MARKDOWN, reply_markup=admin_keyboard())


@dp.message(AdminFSM.stars_amount)
async def admin_stars_amount_handle(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    try:
        count = abs(float(message.text.strip().replace(",", ".")))
    except (ValueError, AttributeError):
        await message.answer("❌ Введите число (например: 5 или 2.5). Попробуйте ещё раз или /cancel.")
        return
    data      = await state.get_data()
    action    = data.get("stars_action", "add")
    target_id = data.get("stars_target")
    await state.clear()
    delta = count if action == "add" else -count
    await add_stars(target_id, delta)
    new_balance = await get_stars(target_id)
    sign = "+" if delta > 0 else ""
    await message.answer(
        f"✅ Готово!\n👤 Пользователь: `{target_id}`\n⭐ Изменение: {sign}{fmt_stars(delta)}\n💰 Новый баланс: {fmt_stars(new_balance)} ⭐",
        parse_mode=ParseMode.MARKDOWN
    )
    try:
        if delta > 0:
            note = f"🎉 Администратор начислил вам +{delta} ⭐!\n💰 Ваш баланс: {fmt_stars(new_balance)} ⭐"
        else:
            note = f"⚠️ Администратор списал {abs(delta)} ⭐.\n💰 Ваш баланс: {fmt_stars(new_balance)} ⭐"
        await bot.send_message(target_id, note)
    except Exception:
        pass
    await _send_user_card(message, target_id)


@dp.callback_query(F.data.startswith("ban_toggle_"))
async def ban_toggle(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён!", show_alert=True)
        return
    target_id = int(callback.data.split("_")[-1])
    if target_id == ADMIN_ID:
        await callback.answer("⛔ Нельзя заблокировать администратора.", show_alert=True)
        return
    currently_banned = await is_banned(target_id)
    if currently_banned:
        await unban_user(target_id)
        action_text = "✅ Разблокирован"
        notify = "✅ Вы разблокированы администратором."
    else:
        await ban_user(target_id)
        action_text = "🚫 Заблокирован"
        notify = "🚫 Вы заблокированы администратором."
    try:
        await bot.send_message(target_id, notify)
    except Exception:
        pass
    await callback.answer(action_text, show_alert=True)
    await _send_user_card(callback, target_id)


@dp.callback_query(F.data == "admin_back")
async def admin_back(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён!", show_alert=True)
        return
    await callback.message.edit_text(
        "🛠 **Панель разработчика**\nВыберите действие:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=admin_keyboard()
    )
    await callback.answer()


# ============================================================
# TGRASS TASKS
# ============================================================

def _tgrass_payload(user: types.User) -> dict:
    return {
        "tg_user_id": int(user.id),
        "tg_login": user.username,
        "lang": user.language_code or "ru",
        "is_premium": bool(user.is_premium),
    }


async def _fetch_tgrass_offers(user: types.User) -> tuple[int, dict]:
    try:
        async with httpx.AsyncClient(verify=False, timeout=15) as client:
            response = await client.post(
                TGRASS_API_URL,
                json=_tgrass_payload(user),
                headers={
                    "accept": "application/json",
                    "Content-Type": "application/json",
                    "Auth": TGRASS_API_KEY,
                },
            )
        return response.status_code, response.json()
    except Exception as e:
        logger.error(f"❌ Tgrass API error: {e}")
        return 0, {}


async def _tgrass_block_msg(
    block_offers: list[dict],
    block_num: int,
    total_blocks: int,
    user_id: int,
    show_skip: bool = True,
) -> tuple[str, InlineKeyboardMarkup]:
    n = len(block_offers)
    subscribed_count = sum(1 for o in block_offers if o.get("subscribed"))
    unsubscribed_count = n - subscribed_count
    block_reward = n * TGRASS_STARS_PER_CHANNEL
    noun = "канал" if n == 1 else "канала" if n <= 4 else "каналов"

    text = (
        f"📦 **Блок {block_num} из {total_blocks}**\n\n"
        f"Подпишись на {n} {noun} и получи **{fmt_stars(block_reward)} ⭐**\n"
        f"Прогресс: {subscribed_count}/{n} ✅\n\n"
    )
    for i, offer in enumerate(block_offers):
        name = offer.get("name") or offer.get("title") or f"Канал {i + 1}"
        icon = "✅" if offer.get("subscribed") else "⭕"
        text += f"  {icon} {i + 1}. {name}\n"

    if unsubscribed_count > 0:
        text += f"\nПодпишись на оставшиеся {unsubscribed_count} и нажми ✅ Проверить:"
    else:
        text += "\nВсе подписки выполнены! Нажми ✅ Проверить:"

    kb: list[list[InlineKeyboardButton]] = []
    for i, offer in enumerate(block_offers):
        if not offer.get("subscribed"):
            btn = "📢 Подписаться" if offer.get("type") == "channel" else "▶️ Перейти"
            name = offer.get("name") or offer.get("title") or f"Канал {i + 1}"
            kb.append([InlineKeyboardButton(text=f"{btn}: {name}", url=offer["link"])])
    kb.append([InlineKeyboardButton(text="✅ Проверить выполнение", callback_data=TGRASS_CHECK_CALLBACK)])

    if show_skip:
        skip_cost = int(await get_setting("skip_cost") or "0")
        max_skips = int(await get_setting("max_skips") or "3")
        skips_used = await get_skips_used(user_id)
        can_skip = (max_skips == 0 or skips_used < max_skips)

        if can_skip:
            skips_left_text = "" if max_skips == 0 else f" ({max_skips - skips_used} осталось)"
            cost_text = f" (-{skip_cost}⭐)" if skip_cost > 0 else " (бесплатно)"
            kb.append([InlineKeyboardButton(
                text=f"⏭ Пропустить блок{cost_text}{skips_left_text}",
                callback_data=TGRASS_SKIP_CALLBACK
            )])
        else:
            kb.append([InlineKeyboardButton(
                text=f"⏭ Пропусков больше нет ({skips_used}/{max_skips})",
                callback_data="skip_limit_reached"
            )])

    return text, InlineKeyboardMarkup(inline_keyboard=kb)


@dp.message(F.text == "📋 Задания")
async def tasks_handler(message: Message):
    if not is_admin(message.from_user.id) and await is_banned(message.from_user.id):
        await message.answer(BAN_REPLY)
        return
    channels = await get_channels()
    if channels and not await check_subscription(message.from_user.id):
        await send_subscription_prompt(message, channels)
        return

    # Показываем вводный текст/медиа раздела «Задания» если задан
    t_text       = await get_setting("tasks_text")
    t_media_type = await get_setting("tasks_text_media_type")
    t_media_id   = await get_setting("tasks_text_media_id")
    if t_media_type == "photo" and t_media_id:
        await message.answer_photo(t_media_id, caption=t_text or None, parse_mode=ParseMode.MARKDOWN)
    elif t_media_type == "video" and t_media_id:
        await message.answer_video(t_media_id, caption=t_text or None, parse_mode=ParseMode.MARKDOWN)
    elif t_text:
        await message.answer(t_text, parse_mode=ParseMode.MARKDOWN)

    user_id = message.from_user.id
    status_code, response_json = await _fetch_tgrass_offers(message.from_user)

    if status_code != 200:
        await message.answer("⚠️ Не удалось загрузить задания. Попробуй позже.")
        return

    all_offers = response_json.get("offers", [])

    # Инициализация при первом открытии
    initial = await get_tgrass_initial(user_id)
    if initial == -1:
        if not all_offers:
            await message.answer("✅ Все блоки выполнены! Загляни позже.")
            return
        # Округляем вниз до кратного TGRASS_BLOCK_SIZE — первый блок всегда будет ровно 4
        raw = len(all_offers)
        initial = (raw // TGRASS_BLOCK_SIZE) * TGRASS_BLOCK_SIZE
        if initial == 0:
            initial = raw  # если офферов < 4 — берём все
        await set_tgrass_initial(user_id, initial)
        await set_tgrass_remaining(user_id, initial)

    # Проверка отписки через Tgrass API
    await _handle_unsubscription(user_id, all_offers, message.bot, message)

    if response_json.get("status") == "ok" or not all_offers:
        await message.answer("✅ Все блоки выполнены! Загляни позже.")
        return

    remaining = await get_tgrass_remaining(user_id)
    if remaining <= 0:
        await message.answer("✅ Все блоки выполнены! Загляни позже.")
        return

    # Текущий блок: срез от позиции (initial - remaining)
    offset = initial - remaining
    block_offers = all_offers[offset: offset + TGRASS_BLOCK_SIZE]
    if not block_offers:
        await message.answer("✅ Все блоки выполнены! Загляни позже.")
        return

    total_blocks = math.ceil(initial / TGRASS_BLOCK_SIZE)
    current_block_num = offset // TGRASS_BLOCK_SIZE + 1
    text, kb = await _tgrass_block_msg(block_offers, current_block_num, total_blocks, user_id)
    await message.answer(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


@dp.callback_query(F.data == TGRASS_CHECK_CALLBACK)
async def check_tgrass_handler(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id

    remaining = await get_tgrass_remaining(user_id)
    initial   = await get_tgrass_initial(user_id)

    if remaining == -1 or initial == -1:
        await callback.message.answer("❌ Сначала открой раздел 📋 Задания.")
        return

    if remaining <= 0:
        await callback.message.answer("✅ Все задания уже выполнены!")
        return

    status_code, response_json = await _fetch_tgrass_offers(callback.from_user)
    logger.info(f"[CHECK] user={user_id} status_code={status_code} remaining={remaining} initial={initial}")

    if status_code != 200:
        await callback.message.answer("⚠️ Сервер заданий временно недоступен. Попробуй позже.")
        return

    all_offers = response_json.get("offers", [])

    # Проверка отписки через Tgrass API
    await _handle_unsubscription(user_id, all_offers, callback.bot, callback)

    if response_json.get("status") == "ok":
        # Всё выполнено по мнению API — засчитываем всё оставшееся
        offset = initial - remaining
        block_offers_ok = all_offers[offset: offset + TGRASS_BLOCK_SIZE] if all_offers else []
        await store_completed_channels(user_id, block_offers_ok)
        block_size = min(TGRASS_BLOCK_SIZE, remaining)
        stars_earned = block_size * TGRASS_STARS_PER_CHANNEL
        await add_stars(user_id, stars_earned)
        await set_tgrass_remaining(user_id, 0)
        new_balance = await get_stars(user_id)
        await callback.message.edit_text(
            f"✅ **Блок выполнен!**\n\n"
            f"⭐ Начислено: +{fmt_stars(stars_earned)} ⭐\n"
            f"💰 Баланс: {fmt_stars(new_balance)} ⭐",
            parse_mode=ParseMode.MARKDOWN, reply_markup=None,
        )
        await callback.message.answer(
            "🎉 **Все блоки выполнены!** Возвращайся позже за новыми заданиями.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # Текущий блок
    offset = initial - remaining
    block_offers = all_offers[offset: offset + TGRASS_BLOCK_SIZE]

    if not block_offers:
        await callback.message.answer("✅ Все задания уже выполнены!")
        return

    not_subscribed = [o for o in block_offers if not o.get("subscribed")]
    logger.info(f"[CHECK] user={user_id} offset={offset} block_size={len(block_offers)} not_subscribed={len(not_subscribed)}")

    if not_subscribed:
        # Показываем прогресс и какие ещё не подписан
        done_count = len(block_offers) - len(not_subscribed)
        names = "\n".join(
            f"  ⭕ {o.get('name') or o.get('title') or 'Канал'}"
            for o in not_subscribed
        )
        await callback.message.answer(
            f"❌ **Ещё не подписан на {len(not_subscribed)} из {len(block_offers)}**\n\n"
            f"Выполнено в блоке: {done_count}/{len(block_offers)} ✅\n\n"
            f"Осталось подписаться:\n{names}\n\n"
            f"Подпишись и нажми ✅ Проверить снова.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                *[[InlineKeyboardButton(
                    text=f"📢 {o.get('name') or o.get('title') or 'Канал'}",
                    url=o["link"]
                )] for o in not_subscribed],
                [InlineKeyboardButton(text="✅ Проверить снова", callback_data=TGRASS_CHECK_CALLBACK)],
            ])
        )
        return

    # Все в блоке подписаны — начисляем звёзды и запоминаем каналы
    block_size    = len(block_offers)
    stars_earned  = block_size * TGRASS_STARS_PER_CHANNEL
    new_remaining = remaining - block_size
    await add_stars(user_id, stars_earned)
    await set_tgrass_remaining(user_id, new_remaining)
    await store_completed_channels(user_id, block_offers)
    new_balance = await get_stars(user_id)

    try:
        await callback.message.edit_text(
            f"✅ **Блок выполнен!**\n\n"
            f"📊 Подписок: {block_size}\n"
            f"⭐ Начислено: +{fmt_stars(stars_earned)} ⭐\n"
            f"💰 Баланс: {fmt_stars(new_balance)} ⭐",
            parse_mode=ParseMode.MARKDOWN, reply_markup=None,
        )
    except Exception:
        pass

    # Реферальный бонус за первый выполненный блок
    was_done = await get_tgrass_done(user_id)
    if not was_done:
        await mark_tgrass_done(user_id)
        referrer_id = await get_user_referrer_id(user_id)
        if referrer_id:
            await add_stars(referrer_id, REFERRAL_STARS)
            try:
                ref_balance = await get_stars(referrer_id)
                await bot.send_message(
                    referrer_id,
                    f"🎉 Ваш реферал выполнил первый блок заданий!\n"
                    f"⭐ Начислено: +{REFERRAL_STARS} ⭐\n"
                    f"💰 Ваш баланс: {fmt_stars(ref_balance)} ⭐"
                )
            except Exception:
                pass

    if new_remaining > 0:
        next_offset = initial - new_remaining
        next_block_offers = all_offers[next_offset: next_offset + TGRASS_BLOCK_SIZE]
        total_blocks = math.ceil(initial / TGRASS_BLOCK_SIZE)
        next_block_num = next_offset // TGRASS_BLOCK_SIZE + 1
        if next_block_offers:
            next_text, next_kb = await _tgrass_block_msg(
                next_block_offers, next_block_num, total_blocks, user_id
            )
            await callback.message.answer(
                f"🔓 **Следующий блок разблокирован!**\n\n{next_text}",
                parse_mode=ParseMode.MARKDOWN, reply_markup=next_kb,
            )
            return

    await callback.message.answer(
        "🎉 **Все блоки выполнены!** Возвращайся позже за новыми заданиями.",
        parse_mode=ParseMode.MARKDOWN,
    )


# ============================================================
# SKIP BLOCK HANDLER
# ============================================================

@dp.callback_query(F.data == TGRASS_SKIP_CALLBACK)
async def skip_tgrass_block_handler(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id

    skip_cost  = int(await get_setting("skip_cost") or "0")
    max_skips  = int(await get_setting("max_skips") or "3")
    skips_used = await get_skips_used(user_id)

    if max_skips != 0 and skips_used >= max_skips:
        await callback.answer(f"❌ Лимит пропусков исчерпан ({skips_used}/{max_skips})", show_alert=True)
        return

    if skip_cost > 0:
        balance = await get_stars(user_id)
        if balance < skip_cost:
            await callback.answer(
                f"❌ Недостаточно звёзд!\nНужно: {skip_cost} ⭐, у тебя: {fmt_stars(balance)} ⭐",
                show_alert=True
            )
            return
        await add_stars(user_id, -skip_cost)

    remaining = await get_tgrass_remaining(user_id)
    initial   = await get_tgrass_initial(user_id)

    if remaining == -1 or initial == -1:
        await callback.message.answer("❌ Сначала открой раздел 📋 Задания.")
        return

    block_size    = min(TGRASS_BLOCK_SIZE, remaining)
    new_remaining = remaining - block_size
    await set_tgrass_remaining(user_id, new_remaining)
    await increment_skips_used(user_id)

    skip_num     = skips_used + 1
    cost_text    = f"\n💸 Списано: -{skip_cost} ⭐" if skip_cost > 0 else ""
    balance_line = f"\n💰 Баланс: {fmt_stars(await get_stars(user_id))} ⭐" if skip_cost > 0 else ""

    try:
        await callback.message.edit_text(
            f"⏭ **Блок пропущен** (пропуск #{skip_num}){cost_text}{balance_line}",
            parse_mode=ParseMode.MARKDOWN, reply_markup=None,
        )
    except Exception:
        pass

    if new_remaining <= 0:
        await callback.message.answer(
            "🎉 **Все блоки пройдены!** Возвращайся позже за новыми заданиями.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # Получаем следующий блок из API
    status_code, response_json = await _fetch_tgrass_offers(callback.from_user)
    if status_code == 200 and response_json.get("offers"):
        all_offers     = response_json["offers"]
        next_offset    = initial - new_remaining
        next_block_offers = all_offers[next_offset: next_offset + TGRASS_BLOCK_SIZE]
        total_blocks   = math.ceil(initial / TGRASS_BLOCK_SIZE)
        next_block_num = next_offset // TGRASS_BLOCK_SIZE + 1

        if next_block_offers:
            next_text, next_kb = await _tgrass_block_msg(
                next_block_offers, next_block_num, total_blocks, user_id
            )
            await callback.message.answer(
                f"🔓 **Следующий блок:**\n\n{next_text}",
                parse_mode=ParseMode.MARKDOWN, reply_markup=next_kb,
            )
            return

    await callback.message.answer(
        "🎉 **Все блоки пройдены!** Возвращайся позже за новыми заданиями.",
        parse_mode=ParseMode.MARKDOWN,
    )


@dp.callback_query(F.data == "skip_limit_reached")
async def skip_limit_reached_handler(callback: CallbackQuery):
    max_skips = await get_setting("max_skips")
    await callback.answer(f"❌ Лимит пропусков исчерпан ({max_skips} из {max_skips})", show_alert=True)


# ============================================================
# CATCH-ALL
# ============================================================

@dp.message()
async def catch_all(message: Message):
    if not is_admin(message.from_user.id) and await is_banned(message.from_user.id):
        await message.answer(BAN_REPLY)
        return
    await message.answer("Используй кнопки меню или команду /start.", reply_markup=main_keyboard())


@dp.callback_query()
async def catch_all_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id) and await is_banned(callback.from_user.id):
        await callback.answer(BAN_REPLY, show_alert=True)
        return
    await callback.answer()


UNSUB_CHECK_INTERVAL = 120  # секунд между проверками

async def subscription_checker_loop():
    """Фоновая задача: каждые UNSUB_CHECK_INTERVAL секунд проверяет
    всех пользователей с сохранёнными каналами на факт отписки."""
    await asyncio.sleep(30)  # даём боту время на старт
    while True:
        try:
            user_ids = await get_all_tracked_user_ids()
            if user_ids:
                logger.info(f"[BGCheck] Проверяем {len(user_ids)} пользователей...")
                for uid in user_ids:
                    try:
                        offers = await _fetch_tgrass_for_uid(uid)
                        if offers:
                            await _handle_unsubscription(uid, offers, bot, notify_target=None)
                    except Exception as e:
                        logger.warning(f"[BGCheck] Ошибка для user={uid}: {e}")
                    await asyncio.sleep(1.0)  # пауза между пользователями (Tgrass rate limit)
                logger.info(f"[BGCheck] Проверка завершена")
        except Exception as e:
            logger.error(f"[BGCheck] Глобальная ошибка: {e}")
        await asyncio.sleep(UNSUB_CHECK_INTERVAL)


async def main():
    await init_db()
    bot_info = await bot.get_me()
    print("=" * 50)
    print("🚀 БОТ ЗАПУЩЕН!")
    print(f"👤 Бот: @{bot_info.username}")
    print(f"👑 Админ ID: {ADMIN_ID}")
    print("=" * 50)
    asyncio.create_task(subscription_checker_loop())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
