from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from momo.core.units import parse_number


def number(value: Any) -> Optional[float]:
    return parse_number(value)


def assets_under_management(data: Mapping[str, Any]) -> Any:
    fund_metrics = data.get("fundMetrics")
    if isinstance(fund_metrics, Mapping):
        value = fund_metrics.get("assetsUnderManagement")
        if value is not None:
            return value

    profile = data.get("profile")
    if isinstance(profile, Mapping):
        return profile.get("aum")
    return None


def format_price(value: Any) -> str:
    result = number(value)
    return f"{result:,.2f}" if result is not None else "—"


def format_date(value: Any) -> str:
    if value is None:
        return "Not available"
    try:
        import pandas as pd
        return pd.Timestamp(value).strftime("%b %d, %Y")
    except (TypeError, ValueError):
        return str(value)


def prepare_prices(price_data: Any) -> Any:
    import pandas as pd
    if not isinstance(price_data, pd.DataFrame) or price_data.empty:
        raise ValueError("Historical OHLCV is unavailable; a chart cannot be generated.")
    frame = price_data.copy()
    frame.columns = [str(column).strip().title() for column in frame.columns]
    required = {"Open", "High", "Low", "Close"}
    if not required.issubset(frame.columns):
        raise ValueError("Historical price data is missing OHLC columns.")
    if "Volume" not in frame.columns:
        frame["Volume"] = 0.0
    frame = frame.dropna(subset=["Open", "High", "Low", "Close"]).sort_index()
    for column in ("Open", "High", "Low", "Close", "Volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["Open", "High", "Low", "Close"])
    if frame.empty:
        raise ValueError("Historical price data has no complete OHLC rows.")
    from momo.engine.indicators import compute_indicators
    frame = compute_indicators(frame)
    frame["BBUpper"] = frame["BB_upper"]
    frame["BBLower"] = frame["BB_lower"]
    frame["MACDSignal"] = frame["MACD_signal"]
    frame["MACDHist"] = frame["MACD"] - frame["MACDSignal"]
    return frame.tail(252)
