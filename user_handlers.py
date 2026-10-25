import html
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, LinkPreviewOptions, Message

import db
from keyboards import (
    BTN_FAVS,
    BTN_HELP,
    BTN_NEW,
    BTN_RANDOM,
    BTN_REF,
    BTN_SEARCH,
    BTN_TOP,
    main_menu,
    movie_kb,
    movies_list_kb,
    sub_kb,
)
from utils import deliver_code, gate, get_unsubscribed, send_movie

log = logging.getLogger(__name__)
router = Router()


async def send_welcome(bot: Bot, chat_id: int, user) -> None:
    admin = await db.is_admin(user.id)
    await bot.send_message(
        chat_id,
        f"👋 Assalomu alaykum, <b>{html.escape(user.full_name or 'do‘st')}</b>!\n\n"
        "🎬 Kino kodini yuboring — men kinoni darhol jo'nataman.\n"
        "🔎 Yoki kino nomini yozib qidirishingiz mumkin.\n\n"
        "Pastdagi menyudan ham foydalaning 👇",
        reply_markup=main_menu(admin),
    )


# ───────────────────────── /start ─────────────────────────
@router.message(CommandStart())
async def cmd_start(m: Message, command: CommandObject, bot: Bot, state: FSMContext, is_new: bool):
    await state.clear()
    payload = (command.args or "").strip()
    code = 0

    if payload.startswith("ref_") and is_new:
        ref = payload[4:]
        if ref.isdigit() and await db.set_referrer(m.from_user.id, int(ref)):
            try:
                await bot.send_message(
                    int(ref),
                    f"🎉 <b>{html.escape(m.from_user.full_name)}</b> sizning havolangiz orqali botga qo'shildi!",
                )
            except Exception:
                pass
    elif payload.startswith("m_") and payload[2:].isdigit():
        code = int(payload[2:])

    if not await gate(bot, m.chat.id, m.from_user.id, code):
        return

    await send_welcome(bot, m.chat.id, m.from_user)
    if code:
        await deliver_code(bot, m.chat.id, m.from_user.id, code)


# ───────────────────────── Obunani tekshirish ─────────────────────────
@router.callback_query(F.data.startswith("check:"))
async def cb_check(c: CallbackQuery, bot: Bot):
    code = int(c.data.split(":")[1])
    bad = await get_unsubscribed(bot, c.from_user.id)
    if bad and not await db.is_admin(c.from_user.id):
        await c.answer("❌ Siz hali barcha kanallarga obuna bo'lmadingiz!", show_alert=True)
        try:
            await c.message.edit_reply_markup(reply_markup=sub_kb(bad, code))
        except TelegramBadRequest:
            pass
        return

    await c.answer("✅ Rahmat! Obuna tasdiqlandi.")
    try:
        await c.message.delete()
    except TelegramBadRequest:
        pass
    await send_welcome(bot, c.message.chat.id, c.from_user)
    if code:
        await deliver_code(bot, c.message.chat.id, c.from_user.id, code)


# ───────────────────────── Menyu tugmalari ─────────────────────────
@router.message(F.text == BTN_SEARCH)
async def btn_search(m: Message, bot: Bot):
    if not await gate(bot, m.chat.id, m.from_user.id):
        return
    await m.answer("🔎 <b>Kino nomini</b> yoki <b>kodini</b> yuboring:")


@router.message(F.text == BTN_TOP)
async def btn_top(m: Message, bot: Bot):
    if not await gate(bot, m.chat.id, m.from_user.id):
        return
    movies = await db.top_movies(10)
    if not movies:
        await m.answer("😔 Hozircha kinolar qo'shilmagan.")
        return
    await m.answer("🔥 <b>Eng ko'p ko'rilgan kinolar:</b>", reply_markup=movies_list_kb(movies))


@router.message(F.text == BTN_NEW)
async def btn_new(m: Message, bot: Bot):
    if not await gate(bot, m.chat.id, m.from_user.id):
        return
    movies = await db.new_movies(10)
    if not movies:
        await m.answer("😔 Hozircha kinolar qo'shilmagan.")
        return
    await m.answer("🆕 <b>Eng so'nggi qo'shilgan kinolar:</b>", reply_markup=movies_list_kb(movies))


@router.message(F.text == BTN_RANDOM)
async def btn_random(m: Message, bot: Bot):
    if not await gate(bot, m.chat.id, m.from_user.id):
        return
    movie = await db.random_movie()
    if not movie:
        await m.answer("😔 Hozircha kinolar qo'shilmagan.")
        return
    await m.answer("🎲 Sizga tasodifiy kino tanladim:")
    await send_movie(bot, m.chat.id, m.from_user.id, movie)


@router.message(F.text == BTN_FAVS)
async def btn_favs(m: Message, bot: Bot):
    if not await gate(bot, m.chat.id, m.from_user.id):
        return
    movies = await db.list_favs(m.from_user.id)
    if not movies:
        await m.answer(
            "💔 Sevimlilar ro'yxati bo'sh.\n\nKino ostidagi «❤️ Sevimlilarga qo'shish» tugmasini bosing."
        )
        return
    await m.answer("❤️ <b>Sevimli kinolaringiz:</b>", reply_markup=movies_list_kb(movies))


@router.message(F.text == BTN_REF)
async def btn_ref(m: Message, bot: Bot):
    me = await bot.me()
    user = await db.get_user(m.from_user.id)
    link = f"https://t.me/{me.username}?start=ref_{m.from_user.id}"
    await m.answer(
        "👥 <b>Do'stlaringizni taklif qiling!</b>\n\n"
        f"🔗 Sizning havolangiz:\n<code>{link}</code>\n\n"
        f"✅ Siz taklif qilganlar: <b>{user.ref_count if user else 0}</b> ta",
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )


@router.message(F.text == BTN_HELP)
@router.message(Command("help"))
async def btn_help(m: Message):
    await m.answer(
        "ℹ️ <b>Yordam</b>\n\n"
        "1️⃣ Kanallarimizdagi postdan <b>kino kodini</b> oling.\n"
        "2️⃣ Kodni shu botga raqam ko'rinishida yuboring.\n"
        "3️⃣ Bot sizga kinoni jo'natadi 🍿\n\n"
        "🔎 Kod bilmasangiz — kino nomini yozing, bot qidiradi.\n"
        "❤️ Yoqqan kinolarni sevimlilarga saqlab qo'ying.\n"
        "👥 Do'stlaringizni taklif qiling."
    )


# ───────────────────────── Ro'yxatdan kino tanlash ─────────────────────────
@router.callback_query(F.data.startswith("movie:"))
async def cb_movie(c: CallbackQuery, bot: Bot):
    code = int(c.data.split(":")[1])
    await c.answer()
    if not await gate(bot, c.message.chat.id, c.from_user.id, code):
        return
    await deliver_code(bot, c.message.chat.id, c.from_user.id, code)


@router.callback_query(F.data.startswith("fav:"))
async def cb_fav(c: CallbackQuery, bot: Bot):
    movie_id = int(c.data.split(":")[1])
    movie = await db.get_movie(movie_id)
    if not movie:
        await c.answer("Kino topilmadi", show_alert=True)
        return
    added = await db.toggle_fav(c.from_user.id, movie_id)
    await c.answer("❤️ Sevimlilarga qo'shildi" if added else "💔 Sevimlilardan olib tashlandi")
    me = await bot.me()
    try:
        await c.message.edit_reply_markup(reply_markup=movie_kb(movie, added, me.username))
    except TelegramBadRequest:
        pass


# ───────────────────────── Kod yoki qidiruv ─────────────────────────
@router.message(F.text & ~F.text.startswith("/"))
async def on_text(m: Message, bot: Bot):
    text = m.text.strip()
    is_code = text.isdigit() and len(text) <= 9

    if not await gate(bot, m.chat.id, m.from_user.id, int(text) if is_code else 0):
        return

    if is_code:
        await deliver_code(bot, m.chat.id, m.from_user.id, int(text))
        return

    found = await db.search_movies(text)
    if not found:
        await m.answer(
            "😔 <b>Hech narsa topilmadi.</b>\n\nBoshqacha yozib ko'ring yoki kino kodini yuboring."
        )
        return
    await m.answer(
        f"🔎 <b>«{html.escape(text[:50])}»</b> bo'yicha natijalar:", reply_markup=movies_list_kb(found)
    )
