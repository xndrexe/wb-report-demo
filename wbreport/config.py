"""Настройки из .env — ключи и адреса вне кода."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    market: str  # wb или ozon
    # ключи не попадают в repr: настройки можно вывести в лог или в отладке без утечки
    token: str = field(repr=False)
    days: int  # за сколько дней строить отчёт
    excel_path: Path
    gsheet_id: str
    google_credentials: str
    stats_url: str
    analytics_url: str
    advert_url: str
    ozon_client_id: str
    ozon_api_key: str = field(repr=False)
    ozon_url: str
    ozon_ads_client_id: str  # Performance API — свой ключ, не Seller API; пусто — отчёт без рекламы
    ozon_ads_secret: str = field(repr=False)
    ozon_ads_url: str


MARKETS = ("wb", "ozon")


def excel_for(market: str) -> Path:
    """Куда класть Excel: EXCEL_PATH из .env или свой файл на каждую площадку."""
    excel = Path(os.environ.get("EXCEL_PATH") or f"out/{market}_report.xlsx")
    return excel if excel.is_absolute() else ROOT / excel


LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def api_url(name: str, default: str) -> str:
    """Адрес API из .env: ключ уходит в заголовке, поэтому только https (http — лишь на свою машину)."""
    url = os.environ.get(name) or default
    parts = urlsplit(url)
    if parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in LOCAL_HOSTS):
        return url
    raise SystemExit(f"{name}={url}: нужен адрес https://, иначе ключ пойдёт по сети открытым текстом")


def load() -> Settings:
    load_dotenv(ROOT / ".env")
    env = os.environ.get
    market = env("MARKETPLACE", "wb").lower()
    if market not in MARKETS:
        raise SystemExit(f"MARKETPLACE={market}: можно wb или ozon")
    return Settings(
        market=market,
        token=env("WB_TOKEN", ""),
        days=max(1, int(env("DAYS", "30"))),
        excel_path=excel_for(market),
        gsheet_id=env("GSHEET_ID", ""),
        google_credentials=env("GOOGLE_CREDENTIALS", ""),
        stats_url=api_url("WB_STATS_URL", "https://statistics-api.wildberries.ru"),
        analytics_url=api_url("WB_ANALYTICS_URL", "https://seller-analytics-api.wildberries.ru"),
        advert_url=api_url("WB_ADVERT_URL", "https://advert-api.wildberries.ru"),
        ozon_client_id=env("OZON_CLIENT_ID", ""),
        ozon_api_key=env("OZON_API_KEY", ""),
        ozon_url=api_url("OZON_URL", "https://api-seller.ozon.ru"),
        ozon_ads_client_id=env("OZON_PERF_CLIENT_ID", ""),
        ozon_ads_secret=env("OZON_PERF_CLIENT_SECRET", ""),
        ozon_ads_url=api_url("OZON_PERF_URL", "https://api-performance.ozon.ru"),
    )
