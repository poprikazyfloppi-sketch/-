"""Tgrass integration for Telegram bots (aiogram 3).

Можно использовать как готовую интеграцию или как основу для своей реализации.

Подключение (готовый вариант):
1. Скопируйте файл в проект.
2. Установите зависимости: pip install httpx aiogram
3. В основном файле бота:
       from tgrass_integration import router as tgrass_router
       dp.include_router(tgrass_router)
4. Реализуйте выдачу награды в send_coins_to_user().

Свой вариант: перенесите _fetch_offers() и обработчики в свой код
и адаптируйте под свои команды, тексты и бизнес-логику.
"""

import httpx
from aiogram import Router, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

TGRASS_API_URL = "https://tgrass.space/offers"
TGRASS_API_KEY = "6c008c66eb5d456987a3b2b60d344df5"
TGRASS_CHECK_CALLBACK = "check_tgrass"

router = Router()


def _offers_payload(user: types.User) -> dict:
    return {
        "tg_user_id": int(user.id),
        "tg_login": user.username,
        "lang": user.language_code or "ru",
        "is_premium": bool(user.is_premium),
    }


async def _fetch_offers(user: types.User) -> tuple[int, dict]:
    async with httpx.AsyncClient(verify=False, timeout=60) as client:
        response = await client.post(
            TGRASS_API_URL,
            json=_offers_payload(user),
            headers={
                "accept": "application/json",
                "Content-Type": "application/json",
                "Auth": TGRASS_API_KEY,
            },
        )
    return response.status_code, response.json()


def _offers_keyboard(offers: list[dict]) -> InlineKeyboardMarkup:
    kb = []
    for offer in offers:
        kb.append(
            [
                InlineKeyboardButton(
                    text="Подписаться" if offer.get("type") == "channel" else "Перейти",
                    url=offer["link"],
                ),
            ]
        )
    kb.append(
        [
            InlineKeyboardButton(
                text="Проверить ✅",
                callback_data=TGRASS_CHECK_CALLBACK,
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=kb)


@router.message(Command("tasks"))
async def tasks_command_handler(message: types.Message):
    status_code, response_json = await _fetch_offers(message.from_user)

    if status_code == 200 and response_json.get("status") == "not_ok":
        await message.answer(
            text="Выполните задание",
            reply_markup=_offers_keyboard(response_json.get("offers", [])),
        )
        return

    await message.answer(text="На данный момент нет доступных заданий")


@router.callback_query(lambda c: c.data == TGRASS_CHECK_CALLBACK)
async def check_tgrass_handler(callback_query: types.CallbackQuery):
    await callback_query.answer()
    status_code, response_json = await _fetch_offers(callback_query.from_user)

    if status_code == 200 and response_json.get("status") == "ok":
        await callback_query.message.answer(text="Задание успешно выполнено!")
        await send_coins_to_user(callback_query.from_user.id)
        return

    await callback_query.message.answer(text="Задание не выполнено!")


async def send_coins_to_user(user_id: int):
    """Выдайте награду пользователю после успешной проверки."""
    ...
