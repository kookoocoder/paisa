from datetime import date

from paisa_trader.nse import equity_bhavcopy_urls, parse_date


def test_parse_date():
    assert parse_date("2024-06-03") == date(2024, 6, 3)


def test_equity_bhavcopy_urls_include_old_and_udiff_formats():
    urls = equity_bhavcopy_urls(date(2024, 6, 3))
    joined = "\n".join(urls)
    assert "cm03JUN2024bhav.csv.zip" in joined
    assert "BhavCopy_NSE_CM_0_0_0_20240603_F_0000.csv.zip" in joined
