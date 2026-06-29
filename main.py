import asyncio
import logging
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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================
# КОНФИГУРАЦИЯ
# ============================================================
TOKEN = "8237274374:AAHRABiO4V4MPEo68nKgdk4S-NFHXJRR5Bg"
ADMIN_ID = 5312536564

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ============================================================
# КЛАВИАТУРЫ
# ============================================================
def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💰 Заработать"), KeyboardButton(text="💎 Баланс")],
            [KeyboardButton(text="🎁 Вывод"), KeyboardButton(text="🏆 Топ")],
            [KeyboardButton(text="📋 Задания"), KeyboardButton(text="📢 Отзывы")],
        ],
        resize_keyboard=True
    )

def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="📣 Рассылка", callback_data="admin_mailing")],
    ])

# ============================================================
# ХЕНДЛЕРЫ
# ============================================================
@dp.message(Command("start"))
async def start(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username or f"user_{user_id}"
    first_name = message.from_user.first_name or username
    
    await message.answer(
        f"👋 Привет, {first_name}!\n\n"
        f"Я бот для заработка звёзд ⭐\n"
        f"Используй кнопки меню для навигации.",
        reply_markup=main_keyboard()
    )

@dp.message(F.text == "💰 Заработать")
async def earn(message: Message):
    await message.answer(
        "💰 Заработок звёзд:\n\n"
        "Приглашай друзей и получай бонусы!",
        reply_markup=main_keyboard()
    )

@dp.message(F.text == "💎 Баланс")
async def balance(message: Message):
    await message.answer(
        "💎 Ваш баланс: 0 ⭐\n"
        "Пригласите друзей чтобы заработать!",
        reply_markup=main_keyboard()
    )

@dp.message(F.text == "🎁 Вывод")
async def withdraw(message: Message):
    await message.answer(
        "🎁 Вывод подарков\n\n"
        "Функция в разработке.",
        reply_markup=main_keyboard()
    )

@dp.message(F.text == "🏆 Топ")
async def top(message: Message):
    await message.answer(
        "🏆 Топ пользователей\n\n"
        "Функция в разработке.",
        reply_markup=main_keyboard()
    )

@dp.message(F.text == "📋 Задания")
async def tasks(message: Message):
    await message.answer(
        "📋 Задания\n\n"
        "Функция в разработке.",
        reply_markup=main_keyboard()
    )

@dp.message(F.text == "📢 Отзывы")
async def reviews(message: Message):
    await message.answer(
        "📢 Отзывы\n\n"
        "Функция в разработке.",
        reply_markup=main_keyboard()
    )

@dp.message(Command("console"))
async def admin_panel(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Доступ запрещён!")
        return
    await message.answer(
        "🛠 Панель администратора",
        reply_markup=admin_keyboard()
    )

@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Доступ запрещён!", show_alert=True)
        return
    await callback.message.answer("📊 Статистика: 0 пользователей")
    await callback.answer()

@dp.callback_query(F.data == "admin_mailing")
async def admin_mailing(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Доступ запрещён!", show_alert=True)
        return
    await callback.message.answer("📣 Функция рассылки в разработке")
    await callback.answer()

# ============================================================
# ЗАПУСК
# ============================================================
async def main():
    print("🚀 Бот запускается...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())