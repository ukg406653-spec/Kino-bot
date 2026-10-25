from typing import Iterable
from urllib.parse import quote

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

BTN_SEARCH = "🔎 Qidirish"
BTN_TOP = "🔥 Top kinolar"
BTN_NEW = "🆕 Yangi kinolar"
BTN_RANDOM = "🎲 Tasodifiy kino"
BTN_FAVS = "❤️ Sevimlilar"
BTN_REF = "👥 Do'st taklif qilish"
BTN_HELP = "ℹ️ Yordam"
BTN_ADMIN = "👑 Admin panel"


def main_menu(is_admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=BTN_SEARCH), KeyboardButton(text=BTN_RANDOM)],
        [KeyboardButton(text=BTN_TOP), KeyboardButton(text=BTN_NEW)],
        [KeyboardButton(text=BTN_FAVS), KeyboardButton(text=BTN_REF)],
        [KeyboardButton(text=BTN_HELP)],
    ]
    if is_admin:
        rows.append([KeyboardButton(text=BTN_ADMIN)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def sub_kb(channels: Iterable, code: int = 0) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for i, ch in enumerate(channels, 1):
        b.row(InlineKeyboardButton(text=f"📢 {i}-kanal: {ch.title}", url=ch.link))
    b.row(InlineKeyboardButton(text="✅ Obunani tekshirish", callback_data=f"check:{code}"))
    return b.as_markup()


def movie_kb(movie, is_fav: bool, bot_username: str) -> InlineKeyboardMarkup:
    share_url = (
        "https://t.me/share/url?url="
        + quote(f"https://t.me/{bot_username}?start=m_{movie.code}", safe="")
        + "&text="
        + quote(f"🎬 {movie.title}")
    )
    fav_text = "💔 Sevimlidan olish" if is_fav else "❤️ Sevimlilarga qo'shish"
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text=fav_text, callback_data=f"fav:{movie.id}"))
    b.row(InlineKeyboardButton(text="📤 Do'stlarga ulashish", url=share_url))
    return b.as_markup()


def movies_list_kb(movies: Iterable) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for m in movies:
        title = m.title if len(m.title) <= 40 else m.title[:37] + "…"
        b.row(InlineKeyboardButton(text=f"🎬 {title} • {m.code}", callback_data=f"movie:{m.code}"))
    return b.as_markup()


def cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Bekor qilish", callback_data="adm:cancel")]]
    )


def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Admin panel", callback_data="adm:home")]]
    )


def admin_kb(is_owner: bool, protect: bool) -> InlineKeyboardMarkup:
    def btn(text, data):
        return InlineKeyboardButton(text=text, callback_data=data)

    rows = [
        [btn("➕ Kino qo'shish", "adm:addmovie"), btn("🗑 Kino o'chirish", "adm:delmovie")],
        [btn("📋 Kinolar ro'yxati", "adm:list:0"), btn("📊 Statistika", "adm:stats")],
        [btn("📢 Majburiy kanallar", "adm:channels"), btn("📣 Post kanali", "adm:postch")],
        [btn("✉️ Xabar yuborish", "adm:bc"), btn("🚫 Ban / Unban", "adm:ban")],
        [btn(f"🛡 Nusxalashdan himoya: {'ON ✅' if protect else 'OFF ❌'}", "adm:protect")],
    ]
    if is_owner:
        rows.append([btn("👮 Adminlar", "adm:admins")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
