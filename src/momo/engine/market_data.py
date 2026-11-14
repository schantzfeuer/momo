from __future__ import annotations
from math import isfinite
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd
from ._utils import _utc_now_iso

def _py(value: Any, digits: Optional[int] = None) -> Any:
    """Convert a numpy/pandas scalar to a JSON-friendly Python type."""
    if value is None:
        return None
    if isinstance(value, (np.floating, float)):
        if not np.isfinite(value):
            return None
        out = float(value)
        return round(out, digits) if digits is not None else out
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return pd.Timestamp(value).isoformat()
    return value

def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))

def _safe_div(a: Any, b: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        a = float(a)
        b = float(b)
        if b == 0 or not isfinite(a) or not isfinite(b):
            return default
        return a / b
    except (TypeError, ValueError):
        return default

def _series_last(series: pd.Series) -> Optional[float]:
    clean = series.dropna()
    return _py(clean.iloc[-1]) if not clean.empty else None

def _flatten_single_ticker_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Flatten yfinance single-ticker MultiIndex columns without guessing multi-ticker data."""
    if not isinstance(df.columns, pd.MultiIndex):
        return df
    out = df.copy()
    tuples = list(out.columns)
    first = [str(x[0]) for x in tuples]
    second = [str(x[1]) if len(x) > 1 else "" for x in tuples]
    price_names = {"Open", "High", "Low", "Close", "Adj Close", "Volume"}
    if set(first) & price_names:
        out.columns = first
    elif set(second) & price_names:
        out.columns = second
    else:
        raise ValueError("Multi-ticker price data is not supported; provide one ticker at a time")
    return out

def assess_price_history(df: pd.DataFrame, interval: str = "1d") -> Dict[str, Any]:
    """Return deterministic quality/freshness diagnostics for cleaned OHLCV data."""
    if df is None or df.empty:
        return {"qualityScore": 0.0, "rowCount": 0, "stale": True, "warnings": ["No historical prices available"]}
    idx = pd.to_datetime(df.index, errors="coerce", utc=True)
    valid_idx = idx[~pd.isna(idx)]
    latest = valid_idx.max() if len(valid_idx) else None
    earliest = valid_idx.min() if len(valid_idx) else None
    age_days = None
    if latest is not None:
        age_days = max(0.0, (pd.Timestamp.now(tz="UTC") - latest).total_seconds() / 86400.0)
    interval_low = str(interval or "1d").lower()
    if interval_low.endswith("m") and interval_low not in {"1mo", "3mo"}:
        stale_after_days = 1.0
    elif interval_low.endswith("h"):
        stale_after_days = 2.0
    elif interval_low in {"1wk", "1w"}:
        stale_after_days = 14.0
    elif interval_low in {"1mo", "3mo"}:
        stale_after_days = 45.0
    else:
        stale_after_days = 7.0
    stale = age_days is None or age_days > stale_after_days
    dropped = int((df.attrs.get("market_meta") or {}).get("droppedRows") or 0)
    row_count = int(len(df))
    retention = row_count / max(row_count + dropped, 1)
    coverage_score = min(1.0, row_count / 252.0)
    freshness_score = 0.45 if stale else 1.0
    quality = _clip((0.55 * retention + 0.30 * coverage_score + 0.15 * freshness_score) * 100.0, 0.0, 100.0)
    warnings: List[str] = []
    if stale:
        warnings.append(f"Historical price data is {age_days:.1f} days old" if age_days is not None else "Historical price freshness is unknown")
    if retention < 0.98:
        warnings.append(f"{dropped} malformed/duplicate price rows were removed")
    if row_count < 60:
        warnings.append("Limited price history reduces technical confidence")
    return {
        "qualityScore": round(quality, 2),
        "rowCount": row_count,
        "from": earliest.isoformat() if earliest is not None else None,
        "to": latest.isoformat() if latest is not None else None,
        "ageCalendarDays": round(age_days, 2) if age_days is not None else None,
        "staleAfterDays": stale_after_days,
        "stale": stale,
        "droppedRows": dropped,
        "warnings": warnings,
    }

def standardize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(df, pd.DataFrame) or df.empty:
        raise ValueError("price_data must be a non-empty OHLCV DataFrame")
    inherited_attrs = dict(getattr(df, "attrs", {}) or {})
    out = _flatten_single_ticker_columns(df.copy())
    rename = {}
    normalized_names = {str(col).lower().replace(" ", "").replace("_", "") for col in out.columns}
    has_native_close = "close" in normalized_names or "c" in normalized_names
    for col in out.columns:
        low = str(col).lower().replace(" ", "").replace("_", "")
        if low in {"open", "o"}:
            rename[col] = "Open"
        elif low in {"high", "h"}:
            rename[col] = "High"
        elif low in {"low", "l"}:
            rename[col] = "Low"
        elif low in {"close", "c"}:
            rename[col] = "Close"
        elif low in {"adjclose", "adjustedclose"} and not has_native_close:
            rename[col] = "Close"
        elif low in {"volume", "vol", "v"}:
            rename[col] = "Volume"
    out = out.rename(columns=rename)
    if "Close" not in out.columns:
        raise ValueError("price_data must contain at least a Close column")
    for col in ("Open", "High", "Low"):
        if col not in out.columns:
            out[col] = out["Close"]
    if "Volume" not in out.columns:
        out["Volume"] = 0.0
    original_rows = len(out)
    out = out[["Open", "High", "Low", "Close", "Volume"]].apply(pd.to_numeric, errors="coerce")
    parsed_index = pd.to_datetime(out.index, errors="coerce")
    out = out.loc[~pd.isna(parsed_index)].copy()
    parsed_index = parsed_index[~pd.isna(parsed_index)]
    out.index = parsed_index
    out = out[~out.index.duplicated(keep="last")].sort_index()
    out = out.dropna(subset=["Close"])
    out = out[out["Close"] > 0]
    out["Volume"] = out["Volume"].where(out["Volume"] >= 0, np.nan).fillna(0.0)
    for col in ("Open", "High", "Low"):
        out[col] = out[col].where(out[col] > 0, out["Close"])
    logical = (
        (out["High"] >= out[["Open", "Close", "Low"]].max(axis=1))
        & (out["Low"] <= out[["Open", "Close", "High"]].min(axis=1))
    )
    out = out.loc[logical].copy()
    if out.empty:
        raise ValueError("price_data contains no valid OHLCV rows after validation")
    meta = dict(inherited_attrs.get("market_meta") or {})
    meta["droppedRows"] = int(meta.get("droppedRows") or 0) + max(0, original_rows - len(out))
    inherited_attrs["market_meta"] = meta
    out.attrs = inherited_attrs
    return out

def fetch_yahoo_history(ticker: str, period: str = "5y", interval: str = "1d") -> pd.DataFrame:
    """Fetch validated OHLCV with two yfinance retrieval paths and provenance metadata."""
    try:
        import yfinance as yf  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "Historical price data requires the optional 'yfinance' package. "
            "Install it with `pip install yfinance` or provide a price_data DataFrame."
        ) from exc

    symbol = str(ticker).strip().upper()
    if not symbol:
        raise ValueError("Ticker cannot be empty")
    kwargs: Dict[str, Any] = {"period": period, "interval": interval, "auto_adjust": False}
    attempts: List[str] = []
    last_exc: Optional[Exception] = None

    for attempt in range(2):
        try:
            try:
                from curl_cffi import requests as curl_requests  # type: ignore
                session = curl_requests.Session(impersonate="chrome")
                stock = yf.Ticker(symbol, session=session)
            except ImportError:
                stock = yf.Ticker(symbol)
            raw = stock.history(**kwargs)
            if raw is not None and not raw.empty:
                clean = standardize_ohlcv(raw)
                meta = {
                    "provider": "Yahoo Finance via yfinance",
                    "fetchPath": "Ticker.history",
                    "fetchedAt": _utc_now_iso(),
                    "attempt": attempt + 1,
                    "period": period,
                    "interval": interval,
                }
                clean.attrs["market_meta"] = {**(clean.attrs.get("market_meta") or {}), **meta}
                clean.attrs["market_meta"]["quality"] = assess_price_history(clean, interval)
                return clean
            attempts.append(f"Ticker.history attempt {attempt + 1}: empty response")
        except Exception as exc:
            last_exc = exc
            attempts.append(f"Ticker.history attempt {attempt + 1}: {exc}")
        if attempt == 0:
            import time as _time
            _time.sleep(0.25)

    try:
        raw = yf.download(symbol, period=period, interval=interval, auto_adjust=False, progress=False, threads=False)
        if raw is not None and not raw.empty:
            clean = standardize_ohlcv(raw)
            meta = {
                "provider": "Yahoo Finance via yfinance",
                "fetchPath": "yf.download",
                "fetchedAt": _utc_now_iso(),
                "attempt": 3,
                "period": period,
                "interval": interval,
                "fallbackFrom": attempts,
            }
            clean.attrs["market_meta"] = {**(clean.attrs.get("market_meta") or {}), **meta}
            clean.attrs["market_meta"]["quality"] = assess_price_history(clean, interval)
            return clean
        attempts.append("yf.download fallback: empty response")
    except Exception as exc:
        last_exc = exc
        attempts.append(f"yf.download fallback: {exc}")

    detail = "; ".join(attempts[-3:])
    raise ValueError(f"Unable to retrieve historical price data for {symbol}: {detail}") from last_exc
