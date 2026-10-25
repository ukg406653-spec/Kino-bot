import os
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # dotenv ixtiyoriy
    pass

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# Egalar (owner) — vergul bilan ajratilgan Telegram ID lar: 12345,67890
ADMIN_IDS = {int(x) for x in re.findall(r"-?\d+", os.getenv("ADMIN_IDS", ""))}

TIMEZONE = os.getenv("TIMEZONE", "Asia/Tashkent")


def _build_db_url():
    """DATABASE_URL (Railway Postgres) bo'lsa — Postgres, bo'lmasa — SQLite."""
    url = os.getenv("DATABASE_URL", "").strip()
    connect_args = {}

    if not url:
        os.makedirs("data", exist_ok=True)
        return "sqlite+aiosqlite:///data/kino.db", connect_args

    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://"):]

    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query))
    sslmode = query.pop("sslmode", None)
    if sslmode in ("require", "verify-ca", "verify-full"):
        connect_args["ssl"] = True
    url = urlunparse(parsed._replace(query=urlencode(query)))
    return url, connect_args


DB_URL, DB_CONNECT_ARGS = _build_db_url()
