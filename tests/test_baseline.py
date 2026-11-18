from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from momo.core.validation import validate_ticker
from momo.core.exceptions import MomoValidationError, SchemaMismatchError
from momo.core.cache import TTLCache
from momo.extractors.sveltekit import _extract_embedded_data
from momo.parsers.stocks.overview import parse_stock_overview_html
from momo.valuation import calculate_fair_values
from momo.engine.fundamental import FundamentalValuationEngine
from momo.engine.market_data import standardize_ohlcv


@pytest.mark.unit
@pytest.mark.parametrize("ticker,expected", [("aapl", "AAPL"), ("BRK.B", "BRK.B")])
def test_ticker_normalization(ticker, expected):
    assert validate_ticker(ticker) == expected


@pytest.mark.unit
@pytest.mark.parametrize("ticker", ["", "AAPL!", "-BAD", "TOO-LONG-TICKER"])
def test_ticker_rejects_invalid(ticker):
    with pytest.raises(MomoValidationError):
        validate_ticker(ticker)


@pytest.mark.unit
def test_ttl_cache_set_get_and_clear(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("momo.core.cache.time.monotonic", lambda: now[0])
    cache = TTLCache(5)
    cache.set("key", {"value": 1})
    assert cache.get("key") == {"value": 1}
    now[0] = 106
    assert cache.get("key") is None
    cache.set("key", 2)
    cache.clear()
    assert cache.get("key") is None


@pytest.mark.parser
def test_sveltekit_extractor_and_overview_parser(sveltekit_stock_html):
    embedded = _extract_embedded_data(sveltekit_stock_html)
    assert embedded["info"]["ticker"] == "AAPL"
    parsed = parse_stock_overview_html(sveltekit_stock_html)
    assert parsed["identity"]["ticker"] == "AAPL"
    assert parsed["quote"]["price"] == 225.0


@pytest.mark.parser
def test_malformed_sveltekit_parser_has_fallback_empty_shape(malformed_sveltekit_html):
    with pytest.raises(SchemaMismatchError):
        _extract_embedded_data(malformed_sveltekit_html)


@pytest.mark.unit
def test_calculate_fair_values_baseline():
    result = calculate_fair_values({
        "price": "225",
        "freeCashFlow": "110B",
        "sharesOutstanding": "15B",
        "eps": "6.20",
        "bookValuePerShare": "4.25",
    })
    assert result["market_price"] == 225
    assert result["models"]["dcf"]["status"] in {"available", "unavailable"}
    assert "formula" in result["models"]["nav"]


@pytest.mark.unit
def test_fundamental_extract_and_evaluate_baseline():
    payload = {
        "ticker": "AAPL",
        "quote": {"price": "225"},
        "financials": {
            "cashFlow": {"rows": [{"datekey": "TTM", "fcf": "110B", "fcfps": "7.33"}]},
            "balanceSheet": {"rows": [{"datekey": "TTM", "sharesOutTotalCommon": "15B"}]},
            "incomeStatement": {"rows": [{"datekey": "TTM", "epsdil": "6.20", "revenue": "420B"}]},
            "ratios": {"rows": []},
        },
        "analysis": {
            "statistics": {"sections": {"ratios": {"values": {"pe": "18"}}}},
            "forecast": {"estimates": {"stats": {"annual": {"epsNext": {"growth": "10.81%", "this": "7.25"}}}}},
        },
    }
    engine = FundamentalValuationEngine(payload)
    metrics = engine.extract_metrics()
    assert metrics["price"] == 225
    evaluation = engine.evaluate()
    assert "methods" in evaluation
    assert "consensus" in evaluation


@pytest.mark.unit
def test_ohlcv_normalization():
    frame = pd.DataFrame(
        {
            "Open": ["10", "11"],
            "High": ["12", "13"],
            "Low": ["9", "10"],
            "Close": ["11", "12.5"],
            "Volume": ["100", "-1"],
        },
        index=pd.to_datetime(["2026-11-17", "2026-11-18"]),
    )
    normalized = standardize_ohlcv(frame)
    assert list(normalized.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert normalized["Volume"].tolist() == [100.0, 0.0]
