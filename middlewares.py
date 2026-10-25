from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

import db


class UserMiddleware(BaseMiddleware):
    """Foydalanuvchini bazaga yozadi va banlanganlarni to'xtatadi."""

    async def __call__(self, handler, event, data):
        tg_user = data.get("event_from_user")
        if tg_user is None or tg_user.is_bot:
            return await handler(event, data)

        db_user, is_new = await db.get_or_create_user(tg_user)
        data["db_user"] = db_user
        data["is_new"] = is_new

        if db_user.is_banned and not await db.is_admin(tg_user.id):
            if isinstance(event, Message):
                await event.answer("🚫 Siz botdan foydalanishdan bloklangansiz.")
            elif isinstance(event, CallbackQuery):
                await event.answer("🚫 Siz bloklangansiz.", show_alert=True)
            return None

        return await handler(event, data)
