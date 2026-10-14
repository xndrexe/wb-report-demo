"""Настройки из .env — токен и адреса вне кода."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    token: str
    days: int  # за сколько дней строить отчёт
    excel_path: Path
    gsheet_id: str
    google_credentials: str
    stats_url: str
    analytics_url: str
    advert_url: str


def load() -> Settings:
    load_dotenv(ROOT / ".env")
    env = os.environ.get
    excel = Path(env("EXCEL_PATH", "out/wb_report.xlsx"))
    return Settings(
        token=env("WB_TOKEN", ""),
        days=max(1, int(env("DAYS", "30"))),
        excel_path=excel if excel.is_absolute() else ROOT / excel,
        gsheet_id=env("GSHEET_ID", ""),
        google_credentials=env("GOOGLE_CREDENTIALS", ""),
        stats_url=env("WB_STATS_URL", "https://statistics-api.wildberries.ru"),
        analytics_url=env("WB_ANALYTICS_URL", "https://seller-analytics-api.wildberries.ru"),
        advert_url=env("WB_ADVERT_URL", "https://advert-api.wildberries.ru"),
    )
