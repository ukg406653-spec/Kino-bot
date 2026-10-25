import asyncio
import html
import logging
from typing import List, Optional, Tuple

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

import db
from keyboards import movie_kb, sub_kb

log = logging.getLogger(__name__)


# ───────────────────────── Obuna tekshiruvi ─────────────────────────
async def _is_member(bot: Bot, ch, user_id: int) -> bool:
    try:
        m = await bot.get_chat_member(ch.chat_id, user_id)
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after)
        return await _is_member(bot, ch, user_id)
    except (TelegramBadRequest, TelegramForbiddenError) as e:
        # Bot kanalda admin emas / kanal topilmadi — foydalanuvchini bloklamaymiz
        log.warning("Kanal %s (%s) tekshirib bo'lmadi: %s", ch.title, ch.chat_id, e)
        return True
    if m.status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED):
        return False
    if m.status == ChatMemberStatus.RESTRICTED and not getattr(m, "is_member", True):
        return False
    return True


async def get_unsubscribed(bot: Bot, user_id: int) -> List:
    channels = await db.list_channels()
    if not channels:
        return []
    results = await asyncio.gather(*[_is_member(bot, ch, user_id) for ch in channels])
    return [ch for ch, ok in zip(channels, results) if not ok]


async def gate(bot: Bot, chat_id: int, user_id: int, code: int = 0) -> bool:
    """Obuna bo'lgan bo'lsa True. Aks holda kanallar ro'yxatini yuboradi va False qaytaradi."""
    if await db.is_admin(user_id):
        return True
    bad = await get_unsubscribed(bot, user_id)
    if not bad:
        return True
    await bot.send_message(
        chat_id,
        "🔒 <b>Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling:</b>\n\n"
        "Obuna bo'lgach, <b>«✅ Obunani tekshirish»</b> tugmasini bosing.",
        reply_markup=sub_kb(bad, code),
    )
    return False


# ───────────────────────── Kino yuborish ─────────────────────────
def build_caption(movie, bot_username: str, views: Optional[int] = None) -> str:
    views = movie.views if views is None else views
    desc = f"\n\n{movie.description}" if movie.description else ""
    text = (
        f"🎬 <b>{html.escape(movie.title)}</b>{desc}\n\n"
        f"#️⃣ Kino kodi: <code>{movie.code}</code>\n"
        f"👁 Ko'rishlar: {views}\n\n"
        f"🤖 @{bot_username}"
    )
    if len(text) > 1024:
        # Juda uzun tavsifni qisqartiramiz
        short = movie.description[: max(0, len(movie.description) - (len(text) - 1000))]
        desc = f"\n\n{short}…" if short else ""
        text = (
            f"🎬 <b>{html.escape(movie.title)}</b>{desc}\n\n"
            f"#️⃣ Kino kodi: <code>{movie.code}</code>\n🤖 @{bot_username}"
        )
    return text[:1024]


async def _send_media(bot: Bot, chat_id: int, ftype: str, file_id: str, **kw) -> Message:
    if ftype == "video":
        return await bot.send_video(chat_id, file_id, supports_streaming=True, **kw)
    if ftype == "photo":
        return await bot.send_photo(chat_id, file_id, **kw)
    if ftype == "animation":
        return await bot.send_animation(chat_id, file_id, **kw)
    return await bot.send_document(chat_id, file_id, **kw)


async def send_movie(bot: Bot, chat_id: int, user_id: int, movie) -> bool:
    me = await bot.me()
    protect = (await db.get_setting("protect", "0")) == "1"
    fav = await db.is_fav(user_id, movie.id)
    try:
        await _send_media(
            bot,
            chat_id,
            movie.file_type,
            movie.file_id,
            caption=build_caption(movie, me.username, movie.views + 1),
            reply_markup=movie_kb(movie, fav, me.username),
            protect_content=protect,
        )
    except TelegramBadRequest as e:
        log.error("Kino yuborilmadi (kod %s): %s", movie.code, e)
        await bot.send_message(chat_id, "⚠️ Kinoni yuborishda xatolik. Iltimos, keyinroq urinib ko'ring.")
        return False
    await db.inc_views(movie.id)
    return True


async def deliver_code(bot: Bot, chat_id: int, user_id: int, code: int) -> None:
    movie = await db.get_movie_by_code(code)
    if not movie:
        await bot.send_message(
            chat_id,
            "❌ <b>Bunday kodli kino topilmadi.</b>\n\nKodni to'g'ri yozganingizni tekshiring "
            "yoki kino nomini yozib qidiring.",
        )
        return
    await send_movie(bot, chat_id, user_id, movie)


# ───────────────────────── Kanalga post ─────────────────────────
async def post_to_channel(bot: Bot, movie) -> None:
    chat = await db.get_setting("post_channel")
    if not chat:
        raise ValueError("Post kanali o'rnatilmagan")
    me = await bot.me()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📥 Kinoni yuklash", url=f"https://t.me/{me.username}?start=m_{movie.code}"
                )
            ]
        ]
    )
    desc = f"\n\n{movie.description}" if movie.description else ""
    text = (
        f"🎬 <b>{html.escape(movie.title)}</b>{desc}\n\n"
        f"#️⃣ Kino kodi: <code>{movie.code}</code>\n\n"
        f"🤖 To'liq kinoni bot orqali ko'rishingiz mumkin!\n"
        f"👇👇\n@{me.username}"
    )
    chat_id = int(chat)
    if movie.preview_id:
        await _send_media(
            bot, chat_id, movie.preview_type or "video", movie.preview_id,
            caption=text[:1024], reply_markup=kb,
        )
    else:
        await bot.send_message(chat_id, text, reply_markup=kb)


# ───────────────────────── Admin yordamchilari ─────────────────────────
def extract_media(m: Message) -> Optional[Tuple[str, str]]:
    if m.video:
        return m.video.file_id, "video"
    if m.animation:
        return m.animation.file_id, "animation"
    if m.document:
        return m.document.file_id, "document"
    if m.photo:
        return m.photo[-1].file_id, "photo"
    return None
