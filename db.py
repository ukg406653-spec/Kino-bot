from datetime import datetime, timezone
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Integer,
    String,
    Text,
    delete,
    func,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

import config


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def today_start_utc() -> datetime:
    tz = ZoneInfo(config.TIMEZONE)
    start = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    full_name: Mapped[str] = mapped_column(String(255), default="")
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    referrer_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    ref_count: Mapped[int] = mapped_column(Integer, default=0)


class Movie(Base):
    __tablename__ = "movies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    file_id: Mapped[str] = mapped_column(String(512))
    file_type: Mapped[str] = mapped_column(String(16))  # video | document | photo | animation
    preview_id: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    preview_type: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    views: Mapped[int] = mapped_column(Integer, default=0)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    title: Mapped[str] = mapped_column(String(255))
    link: Mapped[str] = mapped_column(String(512))


class Favorite(Base):
    __tablename__ = "favorites"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    movie_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)


class AdminUser(Base):
    __tablename__ = "admins"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


engine = create_async_engine(
    config.DB_URL, pool_pre_ping=True, connect_args=config.DB_CONNECT_ARGS
)
Session = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# ───────────────────────── Foydalanuvchilar ─────────────────────────
async def get_or_create_user(tg_user) -> Tuple[User, bool]:
    name = tg_user.full_name or ""
    async with Session() as s:
        u = await s.get(User, tg_user.id)
        if u:
            changed = False
            if u.full_name != name:
                u.full_name = name
                changed = True
            if u.username != tg_user.username:
                u.username = tg_user.username
                changed = True
            if not u.is_active:
                u.is_active = True
                changed = True
            if changed:
                await s.commit()
            return u, False
        u = User(id=tg_user.id, full_name=name, username=tg_user.username)
        s.add(u)
        try:
            await s.commit()
        except IntegrityError:
            await s.rollback()
            u = await s.get(User, tg_user.id)
            return u, False
        return u, True


async def get_user(user_id: int) -> Optional[User]:
    async with Session() as s:
        return await s.get(User, user_id)


async def set_referrer(user_id: int, ref_id: int) -> bool:
    if user_id == ref_id:
        return False
    async with Session() as s:
        u = await s.get(User, user_id)
        r = await s.get(User, ref_id)
        if not u or not r or u.referrer_id is not None:
            return False
        u.referrer_id = ref_id
        r.ref_count += 1
        await s.commit()
        return True


async def set_banned(user_id: int, banned: bool) -> bool:
    async with Session() as s:
        u = await s.get(User, user_id)
        if not u:
            return False
        u.is_banned = banned
        await s.commit()
        return True


async def mark_inactive(user_id: int) -> None:
    async with Session() as s:
        await s.execute(update(User).where(User.id == user_id).values(is_active=False))
        await s.commit()


async def active_user_ids() -> List[int]:
    async with Session() as s:
        res = await s.execute(
            select(User.id).where(User.is_active.is_(True), User.is_banned.is_(False))
        )
        return list(res.scalars().all())


async def stats() -> dict:
    async with Session() as s:
        async def cnt(stmt):
            return (await s.execute(stmt)).scalar() or 0

        return {
            "users": await cnt(select(func.count()).select_from(User)),
            "active": await cnt(
                select(func.count()).select_from(User).where(User.is_active.is_(True))
            ),
            "today": await cnt(
                select(func.count()).select_from(User).where(User.joined_at >= today_start_utc())
            ),
            "banned": await cnt(
                select(func.count()).select_from(User).where(User.is_banned.is_(True))
            ),
            "movies": await cnt(select(func.count()).select_from(Movie)),
            "views": await cnt(select(func.coalesce(func.sum(Movie.views), 0))),
            "channels": await cnt(select(func.count()).select_from(Channel)),
        }


# ───────────────────────── Kinolar ─────────────────────────
async def next_code() -> int:
    async with Session() as s:
        m = (await s.execute(select(func.max(Movie.code)))).scalar()
        return (m or 0) + 1


async def add_movie(**kw) -> Movie:
    async with Session() as s:
        m = Movie(**kw)
        s.add(m)
        await s.commit()
        return m


async def get_movie_by_code(code: int) -> Optional[Movie]:
    async with Session() as s:
        return (await s.execute(select(Movie).where(Movie.code == code))).scalar_one_or_none()


async def get_movie(movie_id: int) -> Optional[Movie]:
    async with Session() as s:
        return await s.get(Movie, movie_id)


async def delete_movie(code: int) -> bool:
    async with Session() as s:
        m = (await s.execute(select(Movie).where(Movie.code == code))).scalar_one_or_none()
        if not m:
            return False
        await s.execute(delete(Favorite).where(Favorite.movie_id == m.id))
        await s.execute(delete(Movie).where(Movie.id == m.id))
        await s.commit()
        return True


async def inc_views(movie_id: int) -> None:
    async with Session() as s:
        await s.execute(update(Movie).where(Movie.id == movie_id).values(views=Movie.views + 1))
        await s.commit()


async def search_movies(query: str, limit: int = 10) -> List[Movie]:
    q = query.strip().replace("%", "").replace("_", "")
    if not q:
        return []
    async with Session() as s:
        res = await s.execute(
            select(Movie).where(Movie.title.ilike(f"%{q}%")).order_by(Movie.views.desc()).limit(limit)
        )
        return list(res.scalars().all())


async def top_movies(limit: int = 10) -> List[Movie]:
    async with Session() as s:
        res = await s.execute(select(Movie).order_by(Movie.views.desc(), Movie.id.desc()).limit(limit))
        return list(res.scalars().all())


async def new_movies(limit: int = 10) -> List[Movie]:
    async with Session() as s:
        res = await s.execute(select(Movie).order_by(Movie.id.desc()).limit(limit))
        return list(res.scalars().all())


async def random_movie() -> Optional[Movie]:
    async with Session() as s:
        return (await s.execute(select(Movie).order_by(func.random()).limit(1))).scalar_one_or_none()


async def list_movies(offset: int, limit: int) -> Tuple[List[Movie], int]:
    async with Session() as s:
        total = (await s.execute(select(func.count()).select_from(Movie))).scalar() or 0
        res = await s.execute(select(Movie).order_by(Movie.code).offset(offset).limit(limit))
        return list(res.scalars().all()), total


# ───────────────────────── Sevimlilar ─────────────────────────
async def is_fav(user_id: int, movie_id: int) -> bool:
    async with Session() as s:
        return await s.get(Favorite, (user_id, movie_id)) is not None


async def toggle_fav(user_id: int, movie_id: int) -> bool:
    """True — qo'shildi, False — olib tashlandi."""
    async with Session() as s:
        f = await s.get(Favorite, (user_id, movie_id))
        if f:
            await s.delete(f)
            await s.commit()
            return False
        s.add(Favorite(user_id=user_id, movie_id=movie_id))
        await s.commit()
        return True


async def list_favs(user_id: int) -> List[Movie]:
    async with Session() as s:
        res = await s.execute(
            select(Movie)
            .join(Favorite, Favorite.movie_id == Movie.id)
            .where(Favorite.user_id == user_id)
            .order_by(Movie.id.desc())
        )
        return list(res.scalars().all())


# ───────────────────────── Kanallar ─────────────────────────
async def add_channel(chat_id: int, title: str, link: str) -> bool:
    async with Session() as s:
        exists = (await s.execute(select(Channel).where(Channel.chat_id == chat_id))).scalar_one_or_none()
        if exists:
            exists.title = title
            exists.link = link
            await s.commit()
            return False
        s.add(Channel(chat_id=chat_id, title=title, link=link))
        await s.commit()
        return True


async def list_channels() -> List[Channel]:
    async with Session() as s:
        return list((await s.execute(select(Channel).order_by(Channel.id))).scalars().all())


async def delete_channel(channel_id: int) -> None:
    async with Session() as s:
        await s.execute(delete(Channel).where(Channel.id == channel_id))
        await s.commit()


# ───────────────────────── Adminlar ─────────────────────────
async def is_admin(user_id: int) -> bool:
    if user_id in config.ADMIN_IDS:
        return True
    async with Session() as s:
        return await s.get(AdminUser, user_id) is not None


async def add_admin(user_id: int) -> None:
    async with Session() as s:
        if not await s.get(AdminUser, user_id):
            s.add(AdminUser(user_id=user_id))
            await s.commit()


async def remove_admin(user_id: int) -> None:
    async with Session() as s:
        await s.execute(delete(AdminUser).where(AdminUser.user_id == user_id))
        await s.commit()


async def list_admins() -> List[int]:
    async with Session() as s:
        return list((await s.execute(select(AdminUser.user_id))).scalars().all())


# ───────────────────────── Sozlamalar ─────────────────────────
async def get_setting(key: str, default: str = "") -> str:
    async with Session() as s:
        row = await s.get(Setting, key)
        return row.value if row else default


async def set_setting(key: str, value: str) -> None:
    async with Session() as s:
        row = await s.get(Setting, key)
        if row:
            row.value = value
        else:
            s.add(Setting(key=key, value=value))
        await s.commit()
