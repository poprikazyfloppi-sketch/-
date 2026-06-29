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
from aiogram.enums import ParseMode

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ============================================================
# ИСПРАВЛЕННЫЕ ПЕРЕМЕННЫЕ
# ============================================================
TOKEN = "8237274374:AAHRABiO4V4MPEo68nKgdk4S-NFHXJRR5Bg"  # <-- ИСПРАВЛЕНО
ADMIN_ID = 5312536564  # <-- ИСПРАВЛЕНО (теперь int, не строка)
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
        # БАГФИКС: проверка существования колонки через PRAGMA
        async with db.execute("PRAGMA table_info(purchases)") as cur:
            columns = [row[1] for row in await cur.fetchall()]
        if "status" not in columns:
            await db.execute("ALTER TABLE purchases ADD COLUMN status TEXT DEFAULT 'pending'")
            await db.commit()
            
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

        # БАГФИКС: проверка существования колонок через PRAGMA
        async with db.execute("PRAGMA table_info(users)") as cur:
            user_columns = [row[1] for row in await cur.fetchall()]
        
        for col in ["tgrass_remaining", "tgrass_initial", "tgrass_done", "skips_used", "tgrass_subscribed_count"]:
            if col not in user_columns:
                try:
                    await db.execute(f"ALTER TABLE users ADD COLUMN {col} INTEGER DEFAULT 0")
                except Exception:
                    pass
        
        async with db.execute("PRAGMA table_info(completed_channels)") as cur:
            comp_columns = [row[1] for row in await cur.fetchall()]
        if "offer_id" not in comp_columns:
            try:
                await db.execute("ALTER TABLE completed_channels ADD COLUMN offer_id TEXT")
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

# ============================================================
# КОНСТАНТЫ
# ============================================================
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
    await message.