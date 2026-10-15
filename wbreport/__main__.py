"""Запуск: python -m wbreport [--market wb|ozon] [--demo] [--days 30] [--every 60]."""

from __future__ import annotations

import argparse
import contextlib
import logging
import time
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from zoneinfo import ZoneInfo

from . import config, export, ozon_report, report
from .api import WBClient
from .ozon_api import OzonClient

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
    _save(cfg, sheets, "wb")
    return sheets


def run_ozon(cfg: config.Settings, client: OzonClient, today: date | None = None) -> list[report.Sheet]:
    """Отчёт Ozon: период — московские сутки, а Ozon ждёт границы в UTC."""
    end = today or today_msk()
    start = end - timedelta(days=cfg.days - 1)
    since = datetime.combine(start, dtime.min, MSK).astimezone(UTC)
    to = datetime.combine(end + timedelta(days=1), dtime.min, MSK).astimezone(UTC)
    postings = client.fbo_postings(since, to) + client.fbs_postings(since, to)
    returns = client.returns(since, to)
    products = client.products()
    skus = sorted({s["sku"] for p in products for s in p.get("stocks", []) if s.get("sku")})
    fbo = client.fbo_stocks(skus)
    accruals = [a for i in range(cfg.days) for a in client.accruals(start + timedelta(days=i))]
    log.info(
        "ozon: %d postings, %d returns, %d products, %d fbo stock rows, %d accruals",
        len(postings),
        len(returns),
        len(products),
        len(fbo),
        len(accruals),
    )
    sheets = ozon_report.build(postings, returns, products, fbo, accruals, start, end)
    _save(cfg, sheets, "ozon")
    return sheets


def _save(cfg: config.Settings, sheets: list[report.Sheet], theme: str) -> None:
    export.to_excel(sheets, cfg.excel_path, theme)
    log.info("excel saved: %s", cfg.excel_path)
    if cfg.gsheet_id and cfg.google_credentials:
        export.to_gsheet(sheets, cfg.gsheet_id, cfg.google_credentials)
        log.info("google sheet updated: %s", cfg.gsheet_id)


def _days(v: str) -> int:
    """--days: целое не меньше 1, иначе период выходит пустым или «задом наперёд»."""
    n = int(v)
    if n < 1:
        raise argparse.ArgumentTypeError("нужно целое число дней, не меньше 1")
    return n


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="wbreport", description="Отчёт Wildberries или Ozon в таблицу")
    ap.add_argument("--market", choices=config.MARKETS, help="площадка (по умолчанию из .env, wb)")
    ap.add_argument("--demo", action="store_true", help="выдуманный магазин на местном сервере вместо API")
    ap.add_argument("--days", type=_days, help="за сколько дней (по умолчанию из .env, 30)")
    ap.add_argument("--every", type=int, default=0, metavar="MIN", help="повторять каждые MIN минут")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    cfg = config.load()
    if args.days:
        cfg = replace(cfg, days=args.days)
    if args.market and args.market != cfg.market:
        cfg = replace(cfg, market=args.market, excel_path=config.excel_for(args.market))
    with contextlib.ExitStack() as stack:
        if cfg.market == "ozon":
            run = _ozon_runner(cfg, args.demo, stack)
        else:
            run = _wb_runner(cfg, args.demo, stack)
        while True:
            try:
                run()
            except Exception:
                if not args.every:
                    raise
                log.exception("run failed, next try in %d min", args.every)
            if not args.every:
                return
            time.sleep(args.every * 60)


def _ozon_runner(cfg: config.Settings, demo: bool, stack: contextlib.ExitStack):
    if demo:
        from . import ozon_fake

        srv = stack.enter_context(ozon_fake.FakeOzon(ozon_fake.generate(today_msk())))
        cfg = replace(
            cfg, ozon_client_id=ozon_fake.CLIENT_ID, ozon_api_key=ozon_fake.API_KEY, ozon_url=srv.url
        )
        log.info("demo mode: fake Ozon at %s", srv.url)
    if not (cfg.ozon_client_id and cfg.ozon_api_key):
        raise SystemExit("Нет ключа: впишите OZON_CLIENT_ID и OZON_API_KEY в .env или запустите с --demo")
    client = OzonClient(cfg.ozon_client_id, cfg.ozon_api_key, url=cfg.ozon_url)
    return lambda: run_ozon(cfg, client)


def _wb_runner(cfg: config.Settings, demo: bool, stack: contextlib.ExitStack):
    interval = 61.0
    if demo:
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
    return lambda: run_once(cfg, client)


if __name__ == "__main__":
    main()
