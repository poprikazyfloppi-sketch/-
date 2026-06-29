import asyncio
import aiosqlite
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
# КОНФИГУРАЦИЯ
# ============================================================
TOKEN = "8237274374:AAHRABiO4V4MPEo68nKgdk4S-NFHXJRR5Bg"
ADMIN_ID = 5312536564
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

# ============================================================
# БАЗА ДАННЫХ
# ============================================================
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
                rewarded_at TEXT,
                offer_id TEXT
            )
        """)

        defaults = {
            "welcome": "👋 Добро пожаловать в бота!\n\nЗдесь ты можешь зарабатывать звёзды ⭐\nПриглашай друзей и получай бонусы!",
            "earn_text": "🔗 Твоя реферальная ссылка:\n{link}\n\nЗа каждого друга ты получишь +3 ⭐",
            "balance_text": "💰 Твой баланс: {stars} ⭐\n👥 Приглашено: {invited}",
            "withdraw_text": "🎁 Выбери подарок:",
            "reviews_text": "📢 Наш канал с отзывами:\nhttps://t.me/example",
            "skip_cost": "0",
            "max_skips": "3",
        }
        for key, value in defaults.items():
            await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))
        await db.commit()
    logger.info("✅ Database initialized")

# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================
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

async def get_channels() -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id, channel_username, channel_link FROM required_channels") as cur:
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
        except Exception:
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

async def is_banned(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM banned_users WHERE user_id = ?", (user_id,)) as cur:
            return await cur.fetchone() is not None

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

# ============================================================
# КОНСТАНТЫ
# ============================================================
TGRASS_API_URL = "https://tgrass.space/offers"
TGRASS_API_KEY = "6c008c66eb5d456987a3b2b60d344df5"
TGRASS_CHECK_CALLBACK = "check_tgrass"
TGRASS_SKIP_CALLBACK = "skip_tgrass_block"
TGRASS_STARS_PER_CHANNEL = 0.8
TGRASS_BLOCK_SIZE = 4
REFERRAL_STARS = 3

def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💰 Заработать"), KeyboardButton(text="💎 Баланс")],
            [KeyboardButton(text="🎁 Вывод"), KeyboardButton(text="🏆 Топ")],
            [KeyboardButton(text="📋 Задания"), KeyboardButton(text="📢 Отзывы")],
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
        [InlineKeyboardButton(text="✏️ Изменить тексты", callback_data="admin_edit_texts")],
        [InlineKeyboardButton(text="📣 Рассылка", callback_data="admin_mailing")],
        [InlineKeyboardButton(text="📢 Каналы подписки", callback_data="admin_channels")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="📦 Заказы на вывод", callback_data="admin_purchases")],
        [InlineKeyboardButton(text="🔍 Найти пользователя", callback_data="admin_find_user")],
        [InlineKeyboardButton(text="🚫 Список банов", callback_data="admin_bans")],
        [InlineKeyboardButton(text="⚙️ Настройки пропуска", callback_data="admin_skip_settings")],
    ])

def edit_texts_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Приветствие", callback_data="edit_welcome")],
        [InlineKeyboardButton(text="📝 Заработать", callback_data="edit_earn_text")],
        [InlineKeyboardButton(text="📝 Баланс", callback_data="edit_balance_text")],
        [InlineKeyboardButton(text="📝 Вывод", callback_data="edit_withdraw_text")],
        [InlineKeyboardButton(text="📝 Отзывы", callback_data="edit_reviews_text")],
        [InlineKeyboardButton(text="📋 Задания", callback_data="edit_tasks_text")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")]
    ])

SETTING_NAMES = {
    "welcome": "Приветствие",
    "earn_text": "Заработать",
    "balance_text": "Баланс",
    "withdraw_text": "Вывод",
    "reviews_text": "Отзывы",
    "tasks_text": "Задания",
}

SETTING_VARS = {
    "welcome": ["{name}", "{username}", "{stars}", "{invited}", "{top_place}"],
    "earn_text": ["{name}", "{username}", "{stars}", "{invited}", "{top_place}", "{link}"],
    "balance_text": ["{name}", "{username}", "{stars}", "{invited}", "{top_place}"],
    "withdraw_text": ["{name}", "{username}", "{stars}", "{invited}", "{top_place}"],
    "reviews_text": ["{name}", "{username}"],
}

VAR_DESCRIPTIONS = {
    "{name}": "имя пользователя",
    "{username}": "@юзернейм",
    "{stars}": "баланс звёзд",
    "{invited}": "кол-во приглашённых",
    "{top_place}": "место в топе по приглашениям",
    "{link}": "реферальная ссылка",
}

def apply_vars(text: str, user_id: int = 0, first_name: str = "",
               username: str = "", stars: float = 0,
               invited: int = 0, link: str = "",
               top_place: int = 0) -> str:
    uname = f"@{username}" if username and not username.startswith("@") else username
    return (
        text
        .replace("{name}", first_name or str(user_id))
        .replace("{username}", uname or str(user_id))
        .replace("{stars}", fmt_stars(stars))
        .replace("{invited}", str(invited))
        .replace("{top_place}", f"#{top_place}" if top_place else "—")
        .replace("{link}", link)
    )

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

    stars = await get_stars(user_id)
    invited = await get_invite_count(user_id)
    top_place = await get_user_rank(user_id)
    bot_info = await bot.get_me()
    link = f"https://t.me/{bot_info.username}?start={user_id}"

    welcome = apply_vars(
        await get_setting("welcome"),
        user_id=user_id, first_name=first_name, username=username,
        stars=stars, invited=invited, link=link, top_place=top_place,
    )

    chat_id = reply_to.from_user.id if isinstance(reply_to, CallbackQuery) else reply_to.chat.id
    await bot.send_message(chat_id, welcome, reply_markup=main_keyboard())

# ============================================================
# ОСНОВНЫЕ ХЕНДЛЕРЫ
# ============================================================
@dp.message(Command("start"))
async def start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    username = message.from_user.username or f"user_{user_id}"
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
    user_id = message.from_user.id
    username = message.from_user.username or f"user_{user_id}"
    first_name = message.from_user.first_name or username
    bot_info = await bot.get_me()
    link = f"https://t.me/{bot_info.username}?start={user_id}"
    stars = await get_stars(user_id)
    invited = await get_invite_count(user_id)
    top_place = await get_user_rank(user_id)
    text = apply_vars(
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
    user_id = message.from_user.id
    username = message.from_user.username or f"user_{user_id}"
    first_name = message.from_user.first_name or username
    stars = await get_stars(user_id)
    invited = await get_invite_count(user_id)
    top_place = await get_user_rank(user_id)
    text = apply_vars(
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
    user_id = message.from_user.id
    username = message.from_user.username or f"user_{user_id}"
    first_name = message.from_user.first_name or username
    stars = await get_stars(user_id)
    invited = await get_invite_count(user_id)
    top_place = await get_user_rank(user_id)
    text = apply_vars(
        await get_setting("withdraw_text"),
        user_id=user_id, first_name=first_name, username=username,
        stars=stars, invited=invited, top_place=top_place,
    )
    await message.answer(text, reply_markup=gifts_keyboard())

@dp.message(F.text == "🏆 Топ")
async def show_top(message: Message):
    if not is_admin(message.from_user.id) and await is_banned(message.from_user.id):
        await message.answer(BAN_REPLY)
        return
    await message.answer("🏆 Топ пользователей:\n\nФункция в разработке...")

@dp.message(F.text == "📢 Отзывы")
async def reviews(message: Message):
    if not is_admin(message.from_user.id) and await is_banned(message.from_user.id):
        await message.answer(BAN_REPLY)
        return
    channels = await get_channels()
    if channels and not await check_subscription(message.from_user.id):
        await send_subscription_prompt(message, channels)
        return
    user_id = message.from_user.id
    username = message.from_user.username or f"user_{user_id}"
    first_name = message.from_user.first_name or username
    text = apply_vars(
        await get_setting("reviews_text"),
        user_id=user_id, first_name=first_name, username=username,
    )
    await message.answer(text)

@dp.message(F.text == "📋 Задания")
async def tasks_handler(message: Message):
    await message.answer("📋 Раздел заданий в разработке...")

@dp.callback_query(F.data == "check_sub")
async def check_sub(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if not await check_subscription(user_id):
        await callback.answer("❌ Вы не подписались на все каналы!", show_alert=True)
        return
    await callback.answer("✅ Проверка пройдена!", show_alert=False)
    data = await state.get_data()
    referrer_id = data.get("referrer_id")
    username = data.get("username") or callback.from_user.username or f"user_{user_id}"
    first_name = data.get("first_name") or callback.from_user.first_name or username
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
# АДМИН-ПАНЕЛЬ
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

@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    await callback.message.edit_text(
        "📊 **Статистика бота**\n\nФункция в разработке...",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")]
        ])
    )
    await callback.answer()

# ============================================================
# ЗАПУСК БОТА
# ============================================================
async def main():
    await init_db()
    bot_info = await bot.get_me()
    print("=" * 50)
    print("🚀 БОТ ЗАПУЩЕН!")
    print(f"👤 Бот: @{bot_info.username}")
    print(f"👑 Админ ID: {ADMIN_ID}")
    print("=" * 50)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())