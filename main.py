import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

import config
import db
import admin_handlers as admin
import user_handlers as user
from middlewares import UserMiddleware


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )
    log = logging.getLogger("kinobot")

    if not config.BOT_TOKEN:
        log.error("BOT_TOKEN o'rnatilmagan! Railway → Variables bo'limiga BOT_TOKEN qo'shing.")
        sys.exit(1)
    if not config.ADMIN_IDS:
        log.warning("ADMIN_IDS bo'sh — hech kim admin panelga kira olmaydi!")

    await db.init_db()

    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    dp.message.outer_middleware(UserMiddleware())
    dp.callback_query.outer_middleware(UserMiddleware())

    dp.include_router(admin.router)  # admin birinchi — FSM holatlari ustun bo'lishi uchun
    dp.include_router(user.router)

    me = await bot.get_me()
    log.info("Bot ishga tushdi: @%s", me.username)

    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
