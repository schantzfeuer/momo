from __future__ import annotations
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence
from .client import Momo

def get_overview(ticker: str, **config_overrides: Any) -> Dict[str, Any]:
    with Momo(**config_overrides) as momo:
        return momo.get_overview(ticker)

def get_holdings(ticker: str, **config_overrides: Any) -> Dict[str, Any]:
    with Momo(**config_overrides) as momo:
        return momo.get_holdings(ticker)

def get_dividends(ticker: str, **config_overrides: Any) -> Dict[str, Any]:
    with Momo(**config_overrides) as momo:
        return momo.get_dividends(ticker)

def get_news(ticker: str, **config_overrides: Any) -> List[Dict[str, Any]]:
    with Momo(**config_overrides) as momo:
        return momo.get_news(ticker)

def get_etf_data(
    ticker: str,
    dump_json: bool = False,
    dump_path: Optional[Path] = None,
    **config_overrides: Any,
) -> Dict[str, Any]:
    with Momo(**config_overrides) as momo:
        return momo.get_etf_data(ticker, dump_json=dump_json, dump_path=dump_path)

def get_data(
    ticker: str,
    asset_type: str = "etf",
    sections: Optional[Sequence[str]] = None,
    dump_json: bool = False,
    dump_path: Optional[Path] = None,
    **config_overrides: Any,
) -> Dict[str, Any]:
    with Momo(**config_overrides) as momo:
        return momo.get_data(
            ticker, asset_type=asset_type, sections=sections,
            dump_json=dump_json, dump_path=dump_path
        )

def get_stock_section(ticker: str, section: str, **config_overrides: Any) -> Dict[str, Any]:
    with Momo(**config_overrides) as momo:
        return momo.get_stock_section(ticker, section)

def get_stock_data(
    ticker: str,
    sections: Optional[Sequence[str]] = None,
    dump_json: bool = False,
    dump_path: Optional[Path] = None,
    **config_overrides: Any,
) -> Dict[str, Any]:
    with Momo(**config_overrides) as momo:
        return momo.get_stock_data(ticker, sections=sections, dump_json=dump_json, dump_path=dump_path)

def get_stock_overview(ticker: str, **config_overrides: Any) -> Dict[str, Any]:
    return get_stock_section(ticker, "overview", **config_overrides)

def get_stock_company(ticker: str, **config_overrides: Any) -> Dict[str, Any]:
    return get_stock_section(ticker, "company", **config_overrides)

def get_stock_financials(ticker: str, **config_overrides: Any) -> Dict[str, Any]:
    return get_stock_section(ticker, "financials", **config_overrides)

def get_stock_statistics(ticker: str, **config_overrides: Any) -> Dict[str, Any]:
    return get_stock_section(ticker, "statistics", **config_overrides)

def get_stock_forecast(ticker: str, **config_overrides: Any) -> Dict[str, Any]:
    return get_stock_section(ticker, "forecast", **config_overrides)

def get_stock_ratings(ticker: str, **config_overrides: Any) -> Dict[str, Any]:
    return get_stock_section(ticker, "ratings", **config_overrides)

def get_stock_market_cap(ticker: str, **config_overrides: Any) -> Dict[str, Any]:
    return get_stock_section(ticker, "market_cap", **config_overrides)

def get_stock_revenue(ticker: str, **config_overrides: Any) -> Dict[str, Any]:
    return get_stock_section(ticker, "revenue", **config_overrides)

def get_stock_dividends(ticker: str, **config_overrides: Any) -> Dict[str, Any]:
    return get_stock_section(ticker, "dividends", **config_overrides)

def get_stock_business_metrics(ticker: str, **config_overrides: Any) -> Dict[str, Any]:
    return get_stock_section(ticker, "business_metrics", **config_overrides)

def analyze_stock_data(
    stock_data: Mapping[str, Any],
    price_data: Any = None,
    period: str = "5y",
    interval: str = "1d",
    auto_fetch_price: bool = True,
    timeframes: Optional[Sequence[Any]] = None,
    **config_overrides: Any,
) -> Dict[str, Any]:
    """Analyze an existing stock payload without refetching fundamentals."""
    with Momo(**config_overrides) as momo:
        return momo.analyze_stock_data(
            stock_data,
            price_data=price_data,
            period=period,
            interval=interval,
            auto_fetch_price=auto_fetch_price,
            timeframes=timeframes,
        )

def analyze_stock(
    ticker: str,
    sections: Optional[Sequence[str]] = None,
    price_data: Any = None,
    period: str = "5y",
    interval: str = "1d",
    auto_fetch_price: bool = True,
    timeframes: Optional[Sequence[Any]] = None,
    **config_overrides: Any,
) -> Dict[str, Any]:
    """Fetch stock data and produce the complete multi-timeframe analysis report."""
    with Momo(**config_overrides) as momo:
        return momo.analyze_stock(
            ticker,
            sections=sections,
            price_data=price_data,
            period=period,
            interval=interval,
            auto_fetch_price=auto_fetch_price,
            timeframes=timeframes,
        )

def compare_stocks(
    tickers: Sequence[str],
    sections: Optional[Sequence[str]] = None,
    **config_overrides: Any,
) -> List[Dict[str, Any]]:
    with Momo(**config_overrides) as momo:
        return momo.compare_stocks(tickers, sections=sections)

def compare(tickers: List[str], **config_overrides: Any) -> List[Dict[str, Any]]:
    with Momo(**config_overrides) as momo:
        return momo.compare(tickers)
