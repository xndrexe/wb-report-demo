"""Листы отчёта WB из готовых строк: суммы, возвраты, отмены, прогноз остатков."""

from datetime import date

from wbreport import report

D = date(2026, 10, 14)


def order(day, price, nm=1, cancel=False):
    return {"date": f"{day}T10:00:00", "priceWithDisc": price, "isCancel": cancel, "nmId": nm}


def sale(day, price, for_pay, sid="S1"):
    return {"date": f"{day}T12:00:00", "priceWithDisc": price, "forPay": for_pay, "saleID": sid}


def test_sales_by_day_with_returns_and_drr():
    orders = [order("2026-10-14", 1000), order("2026-10-14", 1000, cancel=True), order("2026-10-13", 500)]
    sales = [sale("2026-10-14", 1000, 760), sale("2026-10-14", -500, -380, sid="R2")]
    ads = [{"updTime": "2026-10-14T09:00:00", "updSum": 100, "advertId": 1}]
    sh = report.sales_sheet(orders, sales, ads, date(2026, 10, 13), D)
    assert sh.rows[0] == ["14.10.2026", 2, 2000, 1, 1, 1, 500, 380, 100, 5.0]
    assert sh.rows[1] == ["13.10.2026", 1, 500, 0, 0, 0, 0, 0, 0, 0.0]
    assert sh.total == ["Итого", 3, 2500, 1, 1, 1, 500, 380, 100, 4.0]


def test_return_sign_does_not_matter():
    a = report.sales_sheet([], [sale("2026-10-14", -500, -380, sid="R1")], [], D, D)
    b = report.sales_sheet([], [sale("2026-10-14", 500, 380, sid="R1")], [], D, D)
    assert a.rows == b.rows and a.rows[0][6] == -500


def test_empty_day_has_no_drr():
    sh = report.sales_sheet([], [], [], D, D)
    assert sh.rows[0][-1] == ""


def test_stocks_skip_transit_rows_and_count_days_left():
    stocks = [
        {
            "vendorCode": "A",
            "nmId": 1,
            "subjectName": "Пледы",
            "brand": "Б",
            "warehouses": [
                {"warehouseName": "Коледино", "quantity": 20},
                {"warehouseName": "Казань", "quantity": 10},
                {"warehouseName": "В пути до получателей", "quantity": 4},
                {"warehouseName": "Всего находится на складах", "quantity": 30},
            ],
        }
    ]
    orders = [order("2026-10-14", 1, nm=1)] * 20 + [order("2026-10-14", 1, nm=1, cancel=True)] * 5
    sh = report.stocks_sheet(stocks, orders, days=10)
    assert sh.headers[4:6] == ["Казань", "Коледино"]
    assert sh.rows[0] == ["A", 1, "Пледы", "Б", 10, 20, 30, 4, 2.0, 15]
    assert sh.warn == (len(sh.headers) - 1, report.LOW_STOCK_DAYS)


def test_no_orders_means_no_days_left_estimate():
    sh = report.stocks_sheet([{"vendorCode": "A", "nmId": 1, "warehouses": []}], [], days=30)
    assert sh.rows[0][-2:] == [0, ""]


def test_ads_by_campaign():
    ads = [
        {
            "advertId": 1,
            "campName": "Поиск",
            "advertType": 9,
            "updSum": 300,
            "updTime": "2026-10-13T10:00:00",
        },
        {
            "advertId": 1,
            "campName": "Поиск",
            "advertType": 9,
            "updSum": 500,
            "updTime": "2026-10-14T11:30:00",
        },
        {
            "advertId": 2,
            "campName": "Авто",
            "advertType": 8,
            "updSum": 1000,
            "updTime": "2026-10-14T09:00:00",
        },
    ]
    sh = report.ads_sheet(ads)
    assert sh.rows == [
        ["Авто", 2, "Автоматическая", 1000, 1, "14.10.2026 09:00"],
        ["Поиск", 1, "Аукцион", 800, 2, "14.10.2026 11:30"],
    ]
    assert sh.total[3:5] == [1800, 3]
