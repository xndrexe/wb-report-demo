"""Запуск: python -m wbreport [--demo] [--days 30] [--every 60]."""

from __future__ import annotations

import argparse
import contextlib
import logging
import time
from dataclasses import replace
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import config, export, report
from .api import WBClient

log = logging.getLogger("wbreport")

# WB отдаёт даты по Москве без пояса: «сегодня» считаем так же, а не по часам сервера
MSK = ZoneInfo("Europe/Moscow")


def today_msk() -> date:
    return datetime.now(MSK).date()


def run_once(cfg: config.Settings, client: WBClient, today: date | None = None) -> list[report.Sheet]:
    end = today or today_msk()
    start = end - timedelta(days=cfg.days - 1)
    orders = client.orders(start)
    sales = client.sales(start)
    stocks = client.stocks()
    ads = client.ad_costs(start, end)
    log.info(
        "wb: %d orders, %d sales, %d stock rows, %d ad charges",
        len(orders),
        len(sales),
        len(stocks),
        len(ads),
    )
    # только строки внутри периода: более поздние (если today в прошлом) не должны
    # попадать в среднее число заказов в день на листе остатков
    lo, hi = start.isoformat(), end.isoformat()
    in_period = [o for o in orders if lo <= o["date"][:10] <= hi]
    sheets = report.build(
        in_period, [s for s in sales if lo <= s["date"][:10] <= hi], stocks, ads, start, end
    )
    export.to_excel(sheets, cfg.excel_path)
    log.info("excel saved: %s", cfg.excel_path)
    if cfg.gsheet_id and cfg.google_credentials:
        export.to_gsheet(sheets, cfg.gsheet_id, cfg.google_credentials)
        log.info("google sheet updated: %s", cfg.gsheet_id)
    return sheets


def _days(v: str) -> int:
    """--days: целое не меньше 1, иначе период выходит пустым или «задом наперёд»."""
    n = int(v)
    if n < 1:
        raise argparse.ArgumentTypeError("нужно целое число дней, не меньше 1")
    return n


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="wbreport", description="Отчёт Wildberries в таблицу")
    ap.add_argument("--demo", action="store_true", help="выдуманный магазин на местном сервере вместо WB")
    ap.add_argument("--days", type=_days, help="за сколько дней (по умолчанию из .env, 30)")
    ap.add_argument("--every", type=int, default=0, metavar="MIN", help="повторять каждые MIN минут")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    cfg = config.load()
    if args.days:
        cfg = replace(cfg, days=args.days)
    with contextlib.ExitStack() as stack:
        interval = 61.0
        if args.demo:
            from . import fake

            srv = stack.enter_context(fake.FakeWB(fake.generate(today_msk())))
            cfg = replace(cfg, token=fake.TOKEN, stats_url=srv.url, analytics_url=srv.url, advert_url=srv.url)
            interval = 0.0
            log.info("demo mode: fake WB at %s", srv.url)
        if not cfg.token:
            raise SystemExit("Нет токена: впишите WB_TOKEN в .env или запустите с --demo")
        client = WBClient(
            cfg.token,
            stats_url=cfg.stats_url,
            analytics_url=cfg.analytics_url,
            advert_url=cfg.advert_url,
            min_interval=interval,
        )
        while True:
            try:
                run_once(cfg, client)
            except Exception:
                if not args.every:
                    raise
                log.exception("run failed, next try in %d min", args.every)
            if not args.every:
                return
            time.sleep(args.every * 60)


if __name__ == "__main__":
    main()
