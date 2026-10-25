import asyncio
import html
import logging
import re
from typing import Optional

from aiogram import Bot, F, Router
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.filters import Command, CommandObject, Filter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    Chat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
    MessageOriginChannel,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

import config
import db
from keyboards import BTN_ADMIN, admin_kb, back_kb, cancel_kb
from states import (
    AddAdmin,
    AddChannel,
    AddMovie,
    BanUser,
    Broadcast,
    Bulk,
    DelMovie,
    SetPostChannel,
)
from utils import extract_media, post_to_channel
from user_handlers import cmd_start as user_cmd_start

log = logging.getLogger(__name__)
router = Router()
_tasks = set()  # fon vazifalarni "yig'ib yuborishdan" saqlash uchun


class IsAdmin(Filter):
    async def __call__(self, event) -> bool:
        user = getattr(event, "from_user", None)
        return bool(user) and await db.is_admin(user.id)


router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


def is_owner(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


async def panel_text_kb(user_id: int):
    protect = (await db.get_setting("protect", "0")) == "1"
    return "👑 <b>Admin panel</b>\n\nKerakli bo'limni tanlang:", admin_kb(is_owner(user_id), protect)


async def show_panel(target: Message, user_id: int, edit: bool = False):
    text, kb = await panel_text_kb(user_id)
    if edit:
        try:
            await target.edit_text(text, reply_markup=kb)
            return
        except TelegramBadRequest:
            pass
    await target.answer(text, reply_markup=kb)


# ───────────────────────── Panel / bekor qilish ─────────────────────────
@router.message(Command("admin"))
@router.message(F.text == BTN_ADMIN)
async def open_panel(m: Message, state: FSMContext):
    await state.clear()
    await show_panel(m, m.from_user.id)


@router.message(Command("start"))
async def admin_start(m: Message, command: CommandObject, bot: Bot, state: FSMContext, is_new: bool):
    # Admin biror jarayon ichida /start bossa ham ishlashi uchun (holat handlerlari ustun bo'lmasin)
    await user_cmd_start(m, command, bot, state, is_new)


@router.message(Command("cancel"))
async def cmd_cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("❌ Bekor qilindi.")
    await show_panel(m, m.from_user.id)


@router.callback_query(F.data == "adm:cancel")
async def cb_cancel(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.answer("Bekor qilindi")
    await show_panel(c.message, c.from_user.id, edit=True)


@router.callback_query(F.data == "adm:home")
async def cb_home(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.answer()
    await show_panel(c.message, c.from_user.id, edit=True)


@router.callback_query(F.data == "adm:protect")
async def cb_protect(c: CallbackQuery):
    cur = (await db.get_setting("protect", "0")) == "1"
    await db.set_setting("protect", "0" if cur else "1")
    await c.answer("Himoya " + ("o'chirildi ❌" if cur else "yoqildi ✅"))
    await show_panel(c.message, c.from_user.id, edit=True)


# ───────────────────────── Ommaviy qo'shish ─────────────────────────
_bulk_lock = asyncio.Lock()  # forward qilingan kinolar parallel kelsa ham kodlar takrorlanmasin


def guess_title(m: Message, code: int) -> str:
    """Nomni caption'dan, bo'lmasa fayl nomidan oladi."""
    for line in (m.caption or "").splitlines():
        t = re.sub(r"https?://\S+|[#@]\S+", "", line)
        t = re.sub(r"^[^\w]+", "", t).strip(" \t-–—•|:")
        if t:
            return t[:200]
    media = m.video or m.document or m.animation
    name = getattr(media, "file_name", None) if media else None
    if name:
        name = re.sub(r"\.[A-Za-z0-9]{2,4}$", "", name)
        name = re.sub(r"[_.]+", " ", name).strip()
        if name:
            return name[:200]
    return f"Kino {code}"


def bulk_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="✅ Tugatish", callback_data="bulk:done")]]
    )


@router.callback_query(F.data == "adm:bulk")
async def bulk_start(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(Bulk.active)
    await state.update_data(items=[])
    await c.answer()
    await c.message.edit_text(
        "📥 <b>Ommaviy qo'shish</b>\n\n"
        "Kinolarni shu yerga <b>forward</b> qiling (xohlagancha). Har biriga kod ketma-ket beriladi, "
        "nomi forward qilingan postning captionidan olinadi (caption bo'lmasa fayl nomidan).\n\n"
        "Tugagach «✅ Tugatish» tugmasini yoki /done ni bosing.",
        reply_markup=bulk_kb(),
    )


async def _bulk_finish(target: Message, state: FSMContext, user_id: int):
    d = await state.get_data()
    items = d.get("items", [])
    await state.clear()
    if not items:
        await target.answer("Hech narsa qo'shilmadi.")
    else:
        lines = [f"<code>{c}</code> — {html.escape(t)}" for c, t in items]
        shown = lines[-60:]
        more = f"… (oldingi {len(lines) - 60} ta ko'rsatilmadi)\n" if len(lines) > 60 else ""
        await target.answer(
            f"✅ <b>{len(items)} ta kino qo'shildi</b>\n\n{more}" + "\n".join(shown)
            + "\n\nNomni o'zgartirish: <code>/rename KOD Yangi nom</code>"
        )
    await show_panel(target, user_id)


@router.callback_query(Bulk.active, F.data == "bulk:done")
async def bulk_done_cb(c: CallbackQuery, state: FSMContext):
    await c.answer()
    await _bulk_finish(c.message, state, c.from_user.id)


@router.message(Bulk.active, Command("done"))
async def bulk_done_cmd(m: Message, state: FSMContext):
    await _bulk_finish(m, state, m.from_user.id)


@router.message(Bulk.active)
async def bulk_item(m: Message, state: FSMContext):
    media = extract_media(m)
    if not media:
        await m.answer("⚠️ Video yoki fayl forward qiling. Tugatish: /done", reply_markup=bulk_kb())
        return
    async with _bulk_lock:
        code = await db.next_code()
        title = guess_title(m, code)
        await db.add_movie(
            code=code, title=title, description="", file_id=media[0], file_type=media[1]
        )
        d = await state.get_data()
        items = d.get("items", [])
        items.append([code, title])
        await state.update_data(items=items)
    await m.reply(f"✅ <code>{code}</code> — {html.escape(title)}")


@router.message(Command("rename"))
async def cmd_rename(m: Message, command: CommandObject):
    parts = (command.args or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[0].isdigit():
        await m.answer("Ishlatilishi: <code>/rename 534 Yangi nom</code>")
        return
    ok = await db.rename_movie(int(parts[0]), parts[1].strip()[:200])
    await m.answer("✅ Nom o'zgartirildi." if ok else "⚠️ Bunday kodli kino topilmadi.")


# ───────────────────────── Kino qo'shish ─────────────────────────
@router.callback_query(F.data == "adm:addmovie")
async def addmovie_start(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(AddMovie.file)
    await c.answer()
    await c.message.edit_text(
        "🎞 <b>1/5.</b> Kino faylini yuboring.\n\n"
        "Video, hujjat (fayl) yoki rasm bo'lishi mumkin. "
        "Boshqa kanaldan <b>forward</b> qilsangiz ham bo'ladi.",
        reply_markup=cancel_kb(),
    )


@router.message(AddMovie.file)
async def addmovie_file(m: Message, state: FSMContext):
    media = extract_media(m)
    if not media:
        await m.answer("⚠️ Iltimos, video, fayl yoki rasm yuboring.", reply_markup=cancel_kb())
        return
    await state.update_data(file_id=media[0], file_type=media[1])
    await state.set_state(AddMovie.code)
    nxt = await db.next_code()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"🔢 Avto kod: {nxt}", callback_data="addm:auto")],
            [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="adm:cancel")],
        ]
    )
    await m.answer("#️⃣ <b>2/5.</b> Kino kodini yuboring (faqat raqam) yoki avto kodni tanlang:", reply_markup=kb)


async def _ask_title(target: Message, state: FSMContext, code: int):
    await state.update_data(code=code)
    await state.set_state(AddMovie.title)
    await target.answer(f"✅ Kod: <code>{code}</code>\n\n📝 <b>3/5.</b> Kino nomini yuboring:", reply_markup=cancel_kb())


@router.callback_query(AddMovie.code, F.data == "addm:auto")
async def addmovie_auto(c: CallbackQuery, state: FSMContext):
    await c.answer()
    await _ask_title(c.message, state, await db.next_code())


@router.message(AddMovie.code, F.text)
async def addmovie_code(m: Message, state: FSMContext):
    t = m.text.strip()
    if not t.isdigit() or not (0 < int(t) <= 999_999_999):
        await m.answer("⚠️ Kod faqat raqam bo'lishi kerak (masalan: 533).", reply_markup=cancel_kb())
        return
    if await db.get_movie_by_code(int(t)):
        await m.answer("⚠️ Bu kod band. Boshqa kod yuboring.", reply_markup=cancel_kb())
        return
    await _ask_title(m, state, int(t))


@router.message(AddMovie.title, F.text)
async def addmovie_title(m: Message, state: FSMContext):
    await state.update_data(title=m.text.strip()[:200])
    await state.set_state(AddMovie.desc)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⏭ O'tkazib yuborish", callback_data="addm:skipdesc")],
            [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="adm:cancel")],
        ]
    )
    await m.answer(
        "📄 <b>4/5.</b> Tavsif yuboring (janr, yil, til, davomiylik...).\n"
        "Kerak bo'lmasa — o'tkazib yuboring.",
        reply_markup=kb,
    )


async def _ask_preview(target: Message, state: FSMContext, desc: str):
    await state.update_data(desc=desc)
    await state.set_state(AddMovie.preview)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⏭ O'tkazib yuborish", callback_data="addm:skippreview")],
            [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="adm:cancel")],
        ]
    )
    await target.answer(
        "🎥 <b>5/5.</b> Kanalga joylash uchun <b>treyler (video)</b> yoki <b>poster (rasm)</b> yuboring.\n"
        "Kerak bo'lmasa — o'tkazib yuboring.",
        reply_markup=kb,
    )


@router.callback_query(AddMovie.desc, F.data == "addm:skipdesc")
async def addmovie_skipdesc(c: CallbackQuery, state: FSMContext):
    await c.answer()
    await _ask_preview(c.message, state, "")


@router.message(AddMovie.desc, F.text)
async def addmovie_desc(m: Message, state: FSMContext):
    await _ask_preview(m, state, m.html_text[:700])


async def _save_movie(target: Message, state: FSMContext, preview: Optional[tuple]):
    d = await state.get_data()
    await state.clear()
    movie = await db.add_movie(
        code=d["code"],
        title=d["title"],
        description=d.get("desc", ""),
        file_id=d["file_id"],
        file_type=d["file_type"],
        preview_id=preview[0] if preview else None,
        preview_type=preview[1] if preview else None,
    )
    rows = []
    if await db.get_setting("post_channel"):
        rows.append([InlineKeyboardButton(text="📣 Kanalga joylash", callback_data=f"post:{movie.code}")])
    rows.append([InlineKeyboardButton(text="➕ Yana kino qo'shish", callback_data="adm:addmovie")])
    rows.append([InlineKeyboardButton(text="⬅️ Admin panel", callback_data="adm:home")])
    await target.answer(
        f"✅ <b>Kino qo'shildi!</b>\n\n🎬 {html.escape(movie.title)}\n#️⃣ Kod: <code>{movie.code}</code>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(AddMovie.preview, F.data == "addm:skippreview")
async def addmovie_skippreview(c: CallbackQuery, state: FSMContext):
    await c.answer()
    await _save_movie(c.message, state, None)


@router.message(AddMovie.preview)
async def addmovie_preview(m: Message, state: FSMContext):
    media = extract_media(m)
    if not media or media[1] == "document":
        await m.answer("⚠️ Video yoki rasm yuboring (yoki o'tkazib yuboring).")
        return
    await _save_movie(m, state, media)


@router.callback_query(F.data.startswith("post:"))
async def cb_post(c: CallbackQuery, bot: Bot):
    movie = await db.get_movie_by_code(int(c.data.split(":")[1]))
    if not movie:
        await c.answer("Kino topilmadi", show_alert=True)
        return
    try:
        await post_to_channel(bot, movie)
    except Exception as e:
        await c.answer(f"❌ Xatolik: {e}"[:190], show_alert=True)
        return
    await c.answer("✅ Kanalga joylandi!", show_alert=True)


# ───────────────────────── Kino o'chirish ─────────────────────────
@router.callback_query(F.data == "adm:delmovie")
async def delmovie_start(c: CallbackQuery, state: FSMContext):
    await state.set_state(DelMovie.code)
    await c.answer()
    await c.message.edit_text("🗑 O'chiriladigan kino <b>kodini</b> yuboring:", reply_markup=cancel_kb())


@router.message(DelMovie.code, F.text)
async def delmovie_code(m: Message, state: FSMContext):
    t = m.text.strip()
    movie = await db.get_movie_by_code(int(t)) if t.isdigit() and len(t) <= 9 else None
    if not movie:
        await m.answer("⚠️ Bunday kodli kino topilmadi. Qayta yuboring:", reply_markup=cancel_kb())
        return
    await state.clear()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Ha, o'chirish", callback_data=f"delc:{movie.code}")],
            [InlineKeyboardButton(text="❌ Yo'q", callback_data="adm:home")],
        ]
    )
    await m.answer(
        f"❓ Quyidagi kino o'chirilsinmi?\n\n🎬 <b>{html.escape(movie.title)}</b>\n"
        f"#️⃣ Kod: <code>{movie.code}</code> • 👁 {movie.views}",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("delc:"))
async def delmovie_confirm(c: CallbackQuery):
    ok = await db.delete_movie(int(c.data.split(":")[1]))
    await c.answer("🗑 O'chirildi" if ok else "Topilmadi", show_alert=True)
    await show_panel(c.message, c.from_user.id, edit=True)


# ───────────────────────── Kinolar ro'yxati ─────────────────────────
PAGE = 15


@router.callback_query(F.data.startswith("adm:list:"))
async def cb_list(c: CallbackQuery):
    page = int(c.data.split(":")[2])
    movies, total = await db.list_movies(page * PAGE, PAGE)
    await c.answer()
    if not movies and page:
        page = 0
        movies, total = await db.list_movies(0, PAGE)
    lines = [f"<code>{m.code}</code> — {html.escape(m.title)} • 👁 {m.views}" for m in movies]
    text = f"📋 <b>Kinolar ({total} ta)</b>\n\n" + ("\n".join(lines) if lines else "Hozircha bo'sh.")
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"adm:list:{page - 1}"))
    if (page + 1) * PAGE < total:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"adm:list:{page + 1}"))
    rows = [nav] if nav else []
    rows.append([InlineKeyboardButton(text="⬅️ Admin panel", callback_data="adm:home")])
    try:
        await c.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    except TelegramBadRequest:
        pass


# ───────────────────────── Statistika ─────────────────────────
@router.callback_query(F.data == "adm:stats")
async def cb_stats(c: CallbackQuery):
    s = await db.stats()
    top = await db.top_movies(5)
    top_text = "\n".join(
        f"{i}. {html.escape(m.title)} (<code>{m.code}</code>) — 👁 {m.views}" for i, m in enumerate(top, 1)
    )
    await c.answer()
    await c.message.edit_text(
        "📊 <b>Statistika</b>\n\n"
        f"👥 Foydalanuvchilar: <b>{s['users']}</b>\n"
        f"✅ Faol: <b>{s['active']}</b>\n"
        f"🆕 Bugun qo'shilgan: <b>{s['today']}</b>\n"
        f"🚫 Banlangan: <b>{s['banned']}</b>\n\n"
        f"🎬 Kinolar: <b>{s['movies']}</b>\n"
        f"👁 Jami ko'rishlar: <b>{s['views']}</b>\n"
        f"📢 Majburiy kanallar: <b>{s['channels']}</b>\n"
        + (f"\n🔥 <b>Top 5:</b>\n{top_text}" if top else ""),
        reply_markup=back_kb(),
    )


# ───────────────────────── Kanal topish (umumiy) ─────────────────────────
async def resolve_chat(bot: Bot, m: Message) -> Optional[Chat]:
    chat_id = None
    if isinstance(m.forward_origin, MessageOriginChannel):
        chat_id = m.forward_origin.chat.id
    elif m.text:
        t = m.text.strip()
        if t.startswith("https://t.me/") or t.startswith("t.me/"):
            tail = t.rstrip("/").split("/")[-1]
            if not tail.startswith("+") and tail != "joinchat":
                t = "@" + tail
        if t.lstrip("-").isdigit():
            chat_id = int(t)
        elif t.startswith("@"):
            chat_id = t
    if chat_id is None:
        return None
    try:
        return await bot.get_chat(chat_id)
    except (TelegramBadRequest, TelegramForbiddenError):
        return None


async def bot_is_admin(bot: Bot, chat_id: int) -> bool:
    try:
        me = await bot.me()
        member = await bot.get_chat_member(chat_id, me.id)
        return member.status in ("administrator", "creator")
    except (TelegramBadRequest, TelegramForbiddenError):
        return False


# ───────────────────────── Majburiy kanallar ─────────────────────────
async def render_channels(target: Message):
    channels = await db.list_channels()
    b = InlineKeyboardBuilder()
    lines = []
    for ch in channels:
        lines.append(f"• <a href=\"{ch.link}\">{html.escape(ch.title)}</a> (<code>{ch.chat_id}</code>)")
        b.row(InlineKeyboardButton(text=f"🗑 {ch.title[:40]}", callback_data=f"chdel:{ch.id}"))
    b.row(InlineKeyboardButton(text="➕ Kanal qo'shish", callback_data="adm:chadd"))
    b.row(InlineKeyboardButton(text="⬅️ Admin panel", callback_data="adm:home"))
    text = "📢 <b>Majburiy obuna kanallari</b>\n\n" + (
        "\n".join(lines) if lines else "Hozircha kanal yo'q — obuna talab qilinmaydi."
    )
    if channels:
        text += "\n\n🗑 O'chirish uchun kanal tugmasini bosing."
    no_preview = LinkPreviewOptions(is_disabled=True)
    try:
        await target.edit_text(text, reply_markup=b.as_markup(), link_preview_options=no_preview)
    except TelegramBadRequest:
        await target.answer(text, reply_markup=b.as_markup(), link_preview_options=no_preview)


@router.callback_query(F.data == "adm:channels")
async def cb_channels(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.answer()
    await render_channels(c.message)


@router.callback_query(F.data.startswith("chdel:"))
async def cb_chdel(c: CallbackQuery):
    await db.delete_channel(int(c.data.split(":")[1]))
    await c.answer("🗑 O'chirildi")
    await render_channels(c.message)


@router.callback_query(F.data == "adm:chadd")
async def cb_chadd(c: CallbackQuery, state: FSMContext):
    await state.set_state(AddChannel.ref)
    await c.answer()
    await c.message.edit_text(
        "📢 Kanalni qo'shish uchun:\n\n"
        "1️⃣ Botni kanalga <b>admin</b> qiling.\n"
        "2️⃣ Kanaldan istalgan postni <b>forward</b> qiling yoki kanal <code>@username</code> / "
        "<code>-100…</code> ID sini yuboring.",
        reply_markup=cancel_kb(),
    )


async def _finish_channel(target: Message, state: FSMContext, chat_id: int, title: str, link: str):
    await state.clear()
    new = await db.add_channel(chat_id, title, link)
    await target.answer(
        ("✅ Kanal qo'shildi: " if new else "♻️ Kanal yangilandi: ") + f"<b>{html.escape(title)}</b>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="📢 Kanallar", callback_data="adm:channels")]]
        ),
    )


@router.message(AddChannel.ref)
async def addchannel_ref(m: Message, state: FSMContext, bot: Bot):
    chat = await resolve_chat(bot, m)
    if not chat:
        await m.answer(
            "⚠️ Kanal topilmadi. Botni kanalga admin qilganingizni va to'g'ri @username / ID "
            "yuborganingizni tekshiring.",
            reply_markup=cancel_kb(),
        )
        return
    if not await bot_is_admin(bot, chat.id):
        await m.answer(
            "⚠️ Bot bu kanalda <b>admin emas</b>. Avval botni kanalga admin qiling, so'ng qayta yuboring.",
            reply_markup=cancel_kb(),
        )
        return
    title = chat.title or str(chat.id)
    if chat.username:
        await _finish_channel(m, state, chat.id, title, f"https://t.me/{chat.username}")
        return
    try:
        link = (await bot.create_chat_invite_link(chat.id)).invite_link
        await _finish_channel(m, state, chat.id, title, link)
    except (TelegramBadRequest, TelegramForbiddenError):
        await state.update_data(chat_id=chat.id, title=title)
        await state.set_state(AddChannel.link)
        await m.answer(
            "🔗 Bu yopiq kanal. Taklif havolasini (<code>https://t.me/+...</code>) yuboring:",
            reply_markup=cancel_kb(),
        )


@router.message(AddChannel.link, F.text)
async def addchannel_link(m: Message, state: FSMContext):
    link = m.text.strip()
    if not link.startswith("http"):
        await m.answer("⚠️ To'g'ri havola yuboring (https://t.me/...).", reply_markup=cancel_kb())
        return
    d = await state.get_data()
    await _finish_channel(m, state, d["chat_id"], d["title"], link)


# ───────────────────────── Post kanali ─────────────────────────
@router.callback_query(F.data == "adm:postch")
async def cb_postch(c: CallbackQuery, state: FSMContext):
    cur = await db.get_setting("post_channel")
    await state.set_state(SetPostChannel.ref)
    await c.answer()
    await c.message.edit_text(
        "📣 <b>Post kanali</b> — kinolar treyleri joylanadigan kanal.\n\n"
        f"Hozirgi: <code>{cur or 'o‘rnatilmagan'}</code>\n\n"
        "Yangisini o'rnatish uchun botni kanalga admin qiling va kanaldan post forward qiling "
        "yoki @username yuboring.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🗑 O'chirish", callback_data="postch:clear")],
                [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="adm:cancel")],
            ]
        ),
    )


@router.callback_query(F.data == "postch:clear")
async def cb_postch_clear(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await db.set_setting("post_channel", "")
    await c.answer("O'chirildi")
    await show_panel(c.message, c.from_user.id, edit=True)


@router.message(SetPostChannel.ref)
async def postch_ref(m: Message, state: FSMContext, bot: Bot):
    chat = await resolve_chat(bot, m)
    if not chat or not await bot_is_admin(bot, chat.id):
        await m.answer(
            "⚠️ Kanal topilmadi yoki bot u yerda admin emas. Qayta urinib ko'ring.", reply_markup=cancel_kb()
        )
        return
    await db.set_setting("post_channel", str(chat.id))
    await state.clear()
    await m.answer(f"✅ Post kanali o'rnatildi: <b>{html.escape(chat.title or str(chat.id))}</b>")
    await show_panel(m, m.from_user.id)


# ───────────────────────── Xabar yuborish ─────────────────────────
@router.callback_query(F.data == "adm:bc")
async def cb_bc(c: CallbackQuery, state: FSMContext):
    await state.set_state(Broadcast.msg)
    await c.answer()
    await c.message.edit_text(
        "✉️ Barcha foydalanuvchilarga yuboriladigan xabarni yuboring.\n"
        "(Matn, rasm, video — istalgan turdagi xabar, tugmalari bilan.)",
        reply_markup=cancel_kb(),
    )


@router.message(Broadcast.msg)
async def bc_msg(m: Message, state: FSMContext):
    ids = await db.active_user_ids()
    await state.update_data(chat_id=m.chat.id, message_id=m.message_id)
    await state.set_state(Broadcast.confirm)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"✅ Yuborish ({len(ids)} ta)", callback_data="bc:go")],
            [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="adm:cancel")],
        ]
    )
    await m.reply("Yuqoridagi xabar hammaga yuborilsinmi?", reply_markup=kb)


async def _run_broadcast(bot: Bot, admin_chat: int, from_chat: int, message_id: int):
    ids = await db.active_user_ids()
    ok = fail = 0
    for uid in ids:
        try:
            await bot.copy_message(uid, from_chat, message_id)
            ok += 1
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
            try:
                await bot.copy_message(uid, from_chat, message_id)
                ok += 1
            except Exception:
                fail += 1
        except TelegramForbiddenError:
            await db.mark_inactive(uid)
            fail += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)
    await bot.send_message(
        admin_chat, f"📬 <b>Xabar yuborish tugadi</b>\n\n✅ Yetkazildi: {ok}\n❌ Yetmadi: {fail}"
    )


@router.callback_query(Broadcast.confirm, F.data == "bc:go")
async def bc_go(c: CallbackQuery, state: FSMContext, bot: Bot):
    d = await state.get_data()
    await state.clear()
    await c.answer("Yuborish boshlandi")
    await c.message.edit_text("⏳ Xabar yuborilmoqda... Tugagach xabar beraman.")
    task = asyncio.create_task(_run_broadcast(bot, c.message.chat.id, d["chat_id"], d["message_id"]))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


# ───────────────────────── Ban / Unban ─────────────────────────
@router.callback_query(F.data == "adm:ban")
async def cb_ban(c: CallbackQuery):
    await c.answer()
    await c.message.edit_text(
        "🚫 Nima qilamiz?",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="🚫 Ban qilish", callback_data="ban:1"),
                    InlineKeyboardButton(text="✅ Bandan chiqarish", callback_data="ban:0"),
                ],
                [InlineKeyboardButton(text="⬅️ Admin panel", callback_data="adm:home")],
            ]
        ),
    )


@router.callback_query(F.data.in_({"ban:1", "ban:0"}))
async def cb_ban_mode(c: CallbackQuery, state: FSMContext):
    await state.set_state(BanUser.uid)
    await state.update_data(ban=c.data == "ban:1")
    await c.answer()
    await c.message.edit_text("🆔 Foydalanuvchi Telegram ID sini yuboring:", reply_markup=cancel_kb())


@router.message(BanUser.uid, F.text)
async def ban_uid(m: Message, state: FSMContext):
    t = m.text.strip()
    if not t.lstrip("-").isdigit():
        await m.answer("⚠️ Faqat raqamli ID yuboring.", reply_markup=cancel_kb())
        return
    d = await state.get_data()
    uid = int(t)
    if d.get("ban") and await db.is_admin(uid):
        await m.answer("⚠️ Adminni ban qilib bo'lmaydi.", reply_markup=cancel_kb())
        return
    ok = await db.set_banned(uid, bool(d.get("ban")))
    await state.clear()
    if not ok:
        await m.answer("⚠️ Bunday foydalanuvchi bazada topilmadi.")
    else:
        await m.answer("🚫 Foydalanuvchi banlandi." if d.get("ban") else "✅ Foydalanuvchi bandan chiqarildi.")
    await show_panel(m, m.from_user.id)


# ───────────────────────── Adminlar (faqat egalar) ─────────────────────────
async def render_admins(target: Message):
    ids = await db.list_admins()
    b = InlineKeyboardBuilder()
    for uid in ids:
        b.row(InlineKeyboardButton(text=f"🗑 {uid}", callback_data=f"adel:{uid}"))
    b.row(InlineKeyboardButton(text="➕ Admin qo'shish", callback_data="adm:aadd"))
    b.row(InlineKeyboardButton(text="⬅️ Admin panel", callback_data="adm:home"))
    owners = ", ".join(f"<code>{i}</code>" for i in sorted(config.ADMIN_IDS)) or "—"
    await target.edit_text(
        f"👮 <b>Adminlar</b>\n\n👑 Egalar: {owners}\n\n"
        + ("Qo'shilgan adminlar (o'chirish uchun bosing):" if ids else "Qo'shimcha adminlar yo'q."),
        reply_markup=b.as_markup(),
    )


@router.callback_query(F.data == "adm:admins")
async def cb_admins(c: CallbackQuery):
    if not is_owner(c.from_user.id):
        await c.answer("Faqat egalar uchun", show_alert=True)
        return
    await c.answer()
    await render_admins(c.message)


@router.callback_query(F.data.startswith("adel:"))
async def cb_adel(c: CallbackQuery):
    if not is_owner(c.from_user.id):
        await c.answer("Faqat egalar uchun", show_alert=True)
        return
    await db.remove_admin(int(c.data.split(":")[1]))
    await c.answer("🗑 O'chirildi")
    await render_admins(c.message)


@router.callback_query(F.data == "adm:aadd")
async def cb_aadd(c: CallbackQuery, state: FSMContext):
    if not is_owner(c.from_user.id):
        await c.answer("Faqat egalar uchun", show_alert=True)
        return
    await state.set_state(AddAdmin.uid)
    await c.answer()
    await c.message.edit_text("🆔 Yangi admin Telegram ID sini yuboring:", reply_markup=cancel_kb())


@router.message(AddAdmin.uid, F.text)
async def aadd_uid(m: Message, state: FSMContext):
    t = m.text.strip()
    if not t.isdigit():
        await m.answer("⚠️ Faqat raqamli ID yuboring.", reply_markup=cancel_kb())
        return
    await db.add_admin(int(t))
    await state.clear()
    await m.answer(f"✅ <code>{t}</code> admin qilindi.")
    await show_panel(m, m.from_user.id)
