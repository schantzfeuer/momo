"""
MOMO Technical & Valuation Engine
=================================

Stock analysis engine for the MOMO project. This module deliberately separates:
1) technical state / price projection,
2) intrinsic/fundamental fair value,
3) blended fair value per horizon,
4) professional deterministic report.

No indicator requires the ``ta`` package. Indicator calculations use
pandas/numpy so core dependencies remain lightweight. OHLCV retrieval from
Yahoo Finance is optionally supported through ``yfinance``; for offline or
testing workflows, provide ``price_data`` as an OHLCV DataFrame.

Important note: projections are scenario analysis based on trend, volatility,
and valuation, not guaranteed price predictions or calibrated probabilities.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from math import exp, isfinite, sqrt
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

__version__ = "2.1.0"


# =============================================================================
# Helpers
# =============================================================================


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


def _to_number(value: Any, percent_as_fraction: bool = False) -> Optional[float]:
    """Parse StockAnalysis values such as '$4.51T', '10.81%', and 'n/a'."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, np.number)):
        number = float(value)
        return number if isfinite(number) else None
    text = str(value).strip().replace(",", "")
    if not text or text.lower() in {"n/a", "na", "none", "null", "-", "--"}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    is_percent = text.endswith("%")
    text = text.replace("$", "").replace("€", "").replace("£", "").replace("%", "").strip()
    multiplier = 1.0
    if text and text[-1].upper() in {"K", "M", "B", "T"}:
        multiplier = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}[text[-1].upper()]
        text = text[:-1]
    try:
        number = float(text) * multiplier
    except ValueError:
        return None
    if negative:
        number = -number
    if is_percent and percent_as_fraction:
        number /= 100.0
    return number


def _first_number(*values: Any, percent_as_fraction: bool = False) -> Optional[float]:
    for value in values:
        parsed = _to_number(value, percent_as_fraction=percent_as_fraction)
        if parsed is not None:
            return parsed
    return None


def _series_last(series: pd.Series) -> Optional[float]:
    clean = series.dropna()
    return _py(clean.iloc[-1]) if not clean.empty else None


def _cagr(values: Sequence[float], periods: int) -> Optional[float]:
    if len(values) < 2 or periods <= 0:
        return None
    start, end = values[0], values[-1]
    if start is None or end is None or start <= 0 or end <= 0:
        return None
    return (end / start) ** (1.0 / periods) - 1.0


def _weighted_average(items: Sequence[Tuple[Optional[float], float]]) -> Optional[float]:
    valid = [(float(v), float(w)) for v, w in items if v is not None and w > 0 and isfinite(float(v))]
    if not valid:
        return None
    denom = sum(w for _, w in valid)
    return sum(v * w for v, w in valid) / denom if denom else None


def _score_linear(value: Optional[float], bad: float, good: float, higher_is_better: bool = True) -> float:
    if value is None:
        return 50.0
    if good == bad:
        return 50.0
    score = (value - bad) / (good - bad) * 100.0
    if not higher_is_better:
        score = 100.0 - score
    return _clip(score, 0.0, 100.0)


def _label_score(score: float) -> str:
    if score >= 80:
        return "Very Attractive"
    if score >= 65:
        return "Attractive"
    if score >= 50:
        return "Neutral"
    if score >= 35:
        return "Cautious"
    return "Unattractive"


def _valuation_label(upside_pct: Optional[float]) -> str:
    if upside_pct is None:
        return "Insufficient Data"
    if upside_pct >= 25:
        return "Materially Undervalued"
    if upside_pct >= 10:
        return "Undervalued"
    if upside_pct > -10:
        return "Near Fair Value"
    if upside_pct > -25:
        return "Overvalued"
    return "Materially Overvalued"


# =============================================================================
# Timeframe model
# =============================================================================


@dataclass(frozen=True)
class TimeframeSpec:
    key: str
    label: str
    horizon_days: int
    lookback_days: int
    technical_weight: float
    fundamental_weight: float
    # Number of trading days combined into one technical bar.
    # This makes each horizon read a genuinely different market structure
    # instead of repeating the same daily indicator snapshot.
    bar_days: int = 1


DEFAULT_TIMEFRAMES: Dict[str, TimeframeSpec] = {
    "1m": TimeframeSpec("1m", "Short Term (1 month)", 21, 126, 0.80, 0.20, 1),
    "3m": TimeframeSpec("3m", "Swing / Tactical (3 months)", 63, 252, 0.65, 0.35, 2),
    "6m": TimeframeSpec("6m", "Medium Term (6 months)", 126, 504, 0.45, 0.55, 5),
    "12m": TimeframeSpec("12m", "Long Term (12 months)", 252, 756, 0.25, 0.75, 10),
}


def resolve_timeframes(timeframes: Optional[Sequence[Any]] = None) -> List[TimeframeSpec]:
    if timeframes is None:
        return list(DEFAULT_TIMEFRAMES.values())
    resolved: List[TimeframeSpec] = []
    for item in timeframes:
        if isinstance(item, TimeframeSpec):
            resolved.append(item)
        elif isinstance(item, str) and item in DEFAULT_TIMEFRAMES:
            resolved.append(DEFAULT_TIMEFRAMES[item])
        elif isinstance(item, Mapping):
            resolved.append(TimeframeSpec(**item))
        else:
            raise ValueError(f"Unknown timeframe: {item!r}")
    return resolved


# =============================================================================
# Price data + indicators
# =============================================================================


def standardize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(df, pd.DataFrame) or df.empty:
        raise ValueError("price_data must be a non-empty OHLCV DataFrame")
    out = df.copy()
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
    out = out[["Open", "High", "Low", "Close", "Volume"]].apply(pd.to_numeric, errors="coerce")
    out = out.dropna(subset=["Close"]).sort_index()
    if out.empty:
        raise ValueError("price_data contains no valid Close values")
    return out


def fetch_yahoo_history(ticker: str, period: str = "5y", interval: str = "1d") -> pd.DataFrame:
    """Fetch OHLCV through yfinance when available.

    The import is lazy so MOMO can still be used for parsing and fundamental
    analysis without installing yfinance.
    """
    try:
        import yfinance as yf  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "Historical price data requires the optional 'yfinance' package. "
            "Install it with `pip install yfinance` or provide a price_data DataFrame."
        ) from exc

    kwargs: Dict[str, Any] = {"period": period, "interval": interval, "auto_adjust": False}
    try:
        from curl_cffi import requests as curl_requests  # type: ignore
        session = curl_requests.Session(impersonate="chrome")
        stock = yf.Ticker(ticker, session=session)
    except ImportError:
        stock = yf.Ticker(ticker)
    df = stock.history(**kwargs)
    if df is None or df.empty:
        raise ValueError(f"Unable to retrieve historical price data for {ticker}")
    return standardize_ohlcv(df)


def _rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50.0)


def _atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    prev_close = df["Close"].shift(1)
    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - prev_close).abs(),
        (df["Low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()


def compute_indicators(price_data: pd.DataFrame) -> pd.DataFrame:
    df = standardize_ohlcv(price_data)
    close = df["Close"]

    for window in (10, 20, 50, 100, 200):
        df[f"SMA{window}"] = close.rolling(window, min_periods=max(2, min(window, 20))).mean()
    df["EMA12"] = close.ewm(span=12, adjust=False).mean()
    df["EMA26"] = close.ewm(span=26, adjust=False).mean()
    df["MACD"] = df["EMA12"] - df["EMA26"]
    df["MACD_signal"] = df["MACD"].ewm(span=9, adjust=False).mean()
    df["MACD_hist"] = df["MACD"] - df["MACD_signal"]
    df["RSI"] = _rsi(close, 14)
    df["RSI_MA"] = df["RSI"].rolling(9, min_periods=2).mean()

    low14 = df["Low"].rolling(14, min_periods=5).min()
    high14 = df["High"].rolling(14, min_periods=5).max()
    denom = (high14 - low14).replace(0, np.nan)
    df["Stoch_%K"] = 100 * (close - low14) / denom
    df["Stoch_%D"] = df["Stoch_%K"].rolling(3, min_periods=2).mean()

    df["BB_middle"] = close.rolling(20, min_periods=5).mean()
    bb_std = close.rolling(20, min_periods=5).std(ddof=0)
    df["BB_upper"] = df["BB_middle"] + 2 * bb_std
    df["BB_lower"] = df["BB_middle"] - 2 * bb_std
    df["ATR"] = _atr(df, 14)

    typical = (df["High"] + df["Low"] + df["Close"]) / 3.0
    volume = df["Volume"].fillna(0.0)
    pv = typical * volume
    vol20 = volume.rolling(20, min_periods=1).sum()
    df["VWAP"] = pv.rolling(20, min_periods=1).sum() / vol20.replace(0, np.nan)
    vol60 = volume.rolling(60, min_periods=1).sum()
    df["VWAP60"] = pv.rolling(60, min_periods=1).sum() / vol60.replace(0, np.nan)

    direction = np.sign(close.diff()).fillna(0.0)
    df["OBV"] = (direction * volume).cumsum()
    df["OBV_MA"] = df["OBV"].rolling(20, min_periods=2).mean()

    high9 = df["High"].rolling(9, min_periods=2).max()
    low9 = df["Low"].rolling(9, min_periods=2).min()
    high26 = df["High"].rolling(26, min_periods=2).max()
    low26 = df["Low"].rolling(26, min_periods=2).min()
    high52 = df["High"].rolling(52, min_periods=2).max()
    low52 = df["Low"].rolling(52, min_periods=2).min()
    df["Tenkan_sen"] = (high9 + low9) / 2.0
    df["Kijun_sen"] = (high26 + low26) / 2.0
    df["Senkou_span_a"] = (df["Tenkan_sen"] + df["Kijun_sen"]) / 2.0
    df["Senkou_span_b"] = (high52 + low52) / 2.0

    df["Return"] = close.pct_change()
    df["LogReturn"] = np.log(close / close.shift(1))
    df["Vol20"] = df["LogReturn"].rolling(20, min_periods=10).std(ddof=0) * sqrt(252)
    return df




def _compress_ohlcv(price_data: pd.DataFrame, bar_days: int) -> pd.DataFrame:
    """Combine OHLCV into N-trading-day bars anchored to the latest bar.

    Grouping by observation count rather than calendar time is intentional so
    holiday/weekend gaps do not affect backtests. The last bar ends at the
    latest timestamp.
    """
    df = standardize_ohlcv(price_data)
    bar_days = max(1, int(bar_days or 1))
    if bar_days == 1 or len(df) <= 1:
        return df

    n = len(df)
    # Bucket 0 is the latest bar; negative buckets move into the past.
    bucket = np.floor((np.arange(n, dtype=float) - (n - 1)) / bar_days).astype(int)
    work = df.copy()
    work["__bucket"] = bucket
    rows = []
    indices = []
    for _, group in work.groupby("__bucket", sort=True):
        rows.append({
            "Open": float(group["Open"].iloc[0]),
            "High": float(group["High"].max()),
            "Low": float(group["Low"].min()),
            "Close": float(group["Close"].iloc[-1]),
            "Volume": float(group["Volume"].sum()),
        })
        indices.append(group.index[-1])
    return pd.DataFrame(rows, index=pd.Index(indices, name=df.index.name))


def _bounded_tanh(value: float, scale: float) -> float:
    if scale <= 0 or not isfinite(value):
        return 0.0
    return float(np.tanh(value / scale))


def _regression_projection(close: pd.Series, horizon_days: int) -> Dict[str, Optional[float]]:
    clean = close.dropna()
    if len(clean) < 20:
        return {"target": None, "annualizedTrend": None, "r2": None}
    y = np.log(clean.to_numpy(dtype=float))
    x = np.arange(len(y), dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    fitted = intercept + slope * x
    ss_res = float(np.sum((y - fitted) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    # Shrink extrapolation when goodness-of-fit is low to avoid explosive projections.
    strength = _clip(0.20 + 0.80 * max(r2, 0.0), 0.20, 1.0)
    target = float(np.exp(y[-1] + slope * horizon_days * strength))
    annualized = float(np.exp(slope * 252) - 1.0)
    return {"target": target, "annualizedTrend": annualized, "r2": r2}


def _support_resistance(df: pd.DataFrame, lookback: int) -> Dict[str, List[float]]:
    data = df.tail(max(20, lookback))
    price = float(data["Close"].iloc[-1])
    atr = _series_last(data["ATR"]) or price * 0.02
    candidates = set()
    windows = [20, 50, min(100, len(data)), min(200, len(data))]
    for window in windows:
        if window < 2:
            continue
        tail = data.tail(window)
        candidates.add(float(tail["Low"].min()))
        candidates.add(float(tail["High"].max()))
        candidates.add(float(tail["Close"].quantile(0.25)))
        candidates.add(float(tail["Close"].quantile(0.75)))
    for col in ("SMA20", "SMA50", "SMA100", "SMA200", "VWAP", "VWAP60"):
        value = _series_last(data[col]) if col in data else None
        if value is not None:
            candidates.add(float(value))
    # Deduplicate levels that are too close (< 0.35 ATR).
    levels: List[float] = []
    for level in sorted(candidates):
        if level <= 0:
            continue
        if not levels or abs(level - levels[-1]) >= 0.35 * atr:
            levels.append(level)
    supports = sorted([v for v in levels if v < price], reverse=True)[:4]
    resistances = sorted([v for v in levels if v > price])[:4]
    return {
        "supports": [round(v, 4) for v in supports],
        "resistances": [round(v, 4) for v in resistances],
    }


def analyze_technical_timeframe(price_or_indicators: pd.DataFrame, spec: TimeframeSpec) -> Dict[str, Any]:
    """Perform technical analysis that is genuinely specific to each horizon.

    The previous version sliced the same indicator DataFrame, making RSI,
    MACD, SMA, and volatility identical across timeframes. Each horizon now:
      1. selects a different raw lookback,
      2. aggregates OHLCV to a different bar size,
      3. recomputes all indicators on those bars,
      4. uses continuous distance/slope scoring rather than a binary checklist.

    As a result, 1M, 3M, 6M, and 12M can differ even with the same end date.
    """
    raw = standardize_ohlcv(price_or_indicators)
    raw = raw.tail(min(len(raw), max(int(spec.lookback_days), 60)))
    bars = _compress_ohlcv(raw, spec.bar_days)
    df = compute_indicators(bars)

    last = df.iloc[-1]
    price = float(last["Close"])
    atr = _series_last(df["ATR"]) or price * 0.02
    rsi = _series_last(df["RSI"]) or 50.0
    macd = _series_last(df["MACD"]) or 0.0
    macd_signal = _series_last(df["MACD_signal"]) or 0.0
    obv = _series_last(df["OBV"])
    obv_ma = _series_last(df["OBV_MA"])
    sma20 = _series_last(df["SMA20"])
    sma50 = _series_last(df["SMA50"])
    sma200 = _series_last(df["SMA200"])
    vwap = _series_last(df["VWAP"])
    vwap60 = _series_last(df["VWAP60"])
    tenkan = _series_last(df["Tenkan_sen"])
    kijun = _series_last(df["Kijun_sen"])
    span_a = _series_last(df["Senkou_span_a"])
    span_b = _series_last(df["Senkou_span_b"])

    # ------------------------------------------------------------------
    # Continuous score. Binary comparisons can easily push every horizon to
    # 100 during a rally. Distance from the trend anchor and current momentum
    # determine the contribution size.
    # ------------------------------------------------------------------
    score = 50.0
    if sma20:
        score += 7.0 * _bounded_tanh(price / sma20 - 1.0, 0.035)
    if sma50:
        score += 8.0 * _bounded_tanh(price / sma50 - 1.0, 0.060)
    if sma200:
        score += 9.0 * _bounded_tanh(price / sma200 - 1.0, 0.120)
    if sma20 and sma50:
        score += 5.0 * _bounded_tanh(sma20 / sma50 - 1.0, 0.035)
    if sma50 and sma200:
        score += 6.0 * _bounded_tanh(sma50 / sma200 - 1.0, 0.070)

    macd_spread = macd - macd_signal
    score += 6.0 * _bounded_tanh(macd_spread, max(atr * 0.35, price * 0.0025))

    # RSI is centered around 50; extreme overbought readings are reduced so
    # strong momentum is not treated as asymmetrical risk-free upside.
    score += 6.0 * _bounded_tanh(rsi - 50.0, 15.0)
    if rsi > 78:
        score -= min(4.0, (rsi - 78.0) * 0.35)
    elif rsi < 22:
        score += min(2.0, (22.0 - rsi) * 0.20)

    if obv is not None and obv_ma is not None:
        obv_scale = float(df["OBV"].diff().dropna().std(ddof=0)) if df["OBV"].notna().sum() > 5 else 0.0
        if obv_scale > 0:
            score += 4.0 * _bounded_tanh(obv - obv_ma, obv_scale * 3.0)
    if vwap:
        score += 4.0 * _bounded_tanh(price / vwap - 1.0, 0.035)

    if span_a is not None and span_b is not None:
        cloud_mid = (span_a + span_b) / 2.0
        if cloud_mid:
            score += 5.0 * _bounded_tanh(price / cloud_mid - 1.0, 0.060)
    if tenkan and kijun:
        score += 3.0 * _bounded_tanh(tenkan / kijun - 1.0, 0.025)

    # Avoid artificial 0/100 saturation; technical score is a strength
    # estimate, not an event probability.
    score = _clip(score, 5.0, 95.0)

    regression = _regression_projection(df["Close"], max(1, int(round(spec.horizon_days / max(spec.bar_days, 1)))))

    # Volatility must be adjusted for sampling frequency. The Vol20 value from
    # compute_indicators assumes daily data, so it is recomputed here.
    vol_window = min(max(10, 20), max(10, len(df)))
    log_ret = df["LogReturn"].dropna()
    if len(log_ret) >= 5:
        sample = log_ret.tail(vol_window)
        annual_vol = float(sample.std(ddof=0) * sqrt(252.0 / max(spec.bar_days, 1)))
    else:
        annual_vol = 0.25

    horizon_sigma = annual_vol * sqrt(spec.horizon_days / 252.0)
    trend_target = regression["target"] or price

    bear = trend_target * exp(-0.75 * horizon_sigma)
    bull = trend_target * exp(0.75 * horizon_sigma)
    levels = _support_resistance(df, len(df))
    nearest_support = levels["supports"][0] if levels["supports"] else price - 1.5 * atr
    nearest_resistance = levels["resistances"][0] if levels["resistances"] else price + 1.5 * atr

    # Per-horizon equilibrium: longer sampling naturally gives more weight to
    # slow anchors because SMA20/50/200 now means 20/50/200 bars rather than
    # the same daily observations for every timeframe.
    equilibrium = _weighted_average([
        (sma20, 0.22), (sma50, 0.24), (sma200, 0.16),
        (vwap, 0.10), (vwap60, 0.08), (trend_target, 0.20),
    ]) or price

    momentum_tilt = (score - 50.0) / 50.0
    technical_base = 0.55 * trend_target + 0.45 * equilibrium
    technical_base *= 1.0 + momentum_tilt * min(0.025, 0.005 + spec.horizon_days / 252.0 * 0.02)

    trend = "Sideways"
    if score >= 76:
        trend = "Strong Uptrend"
    elif score >= 60:
        trend = "Uptrend"
    elif score <= 24:
        trend = "Strong Downtrend"
    elif score <= 40:
        trend = "Downtrend"

    confidence = 40.0
    confidence += min(20.0, len(raw) / max(spec.lookback_days, 1) * 20.0)
    confidence += min(15.0, len(df) / 60.0 * 15.0)
    confidence += (regression.get("r2") or 0.0) * 15.0
    confidence += min(10.0, abs(score - 50.0) / 50.0 * 10.0)
    confidence = _clip(confidence, 0.0, 100.0)

    return {
        "timeframe": spec.key,
        "label": spec.label,
        "horizonTradingDays": spec.horizon_days,
        "lookbackTradingDays": len(raw),
        "barSizeTradingDays": int(spec.bar_days),
        "barsAnalyzed": len(df),
        "currentPrice": round(price, 4),
        "technicalScore": round(score, 2),
        "trend": trend,
        "confidence": round(confidence, 2),
        "indicators": {
            "sma20": _py(sma20, 4), "sma50": _py(sma50, 4), "sma200": _py(sma200, 4),
            "rsi14": _py(rsi, 2), "macd": _py(macd, 4), "macdSignal": _py(macd_signal, 4),
            "vwap20": _py(vwap, 4), "vwap60": _py(vwap60, 4), "atr14": _py(atr, 4),
            "annualizedVolatility": _py(annual_vol, 4),
            "ichimoku": {"tenkan": _py(tenkan, 4), "kijun": _py(kijun, 4), "spanA": _py(span_a, 4), "spanB": _py(span_b, 4)},
        },
        "sampling": {
            "barSizeTradingDays": int(spec.bar_days),
            "barsAnalyzed": len(df),
            "meaning": f"SMA/RSI/MACD recomputed on {spec.bar_days}-trading-day bars",
        },
        "regression": {k: _py(v, 6 if k == "r2" else 4) for k, v in regression.items()},
        "levels": levels,
        "technicalReference": {
            "equilibrium": round(equilibrium, 4),
            "base": round(technical_base, 4),
            "low": round(min(bear, nearest_support), 4),
            "high": round(max(bull, nearest_resistance), 4),
        },
        "projection": {
            "bear": round(bear, 4),
            "base": round(technical_base, 4),
            "bull": round(bull, 4),
            "baseReturnPct": round((technical_base / price - 1.0) * 100.0, 2),
        },
    }


# =============================================================================
# Fundamental valuation
# =============================================================================


class FundamentalValuationEngine:
    def __init__(self, stock_data: Mapping[str, Any]):
        self.data = stock_data or {}
        self.current_price = _first_number((self.data.get("quote") or {}).get("price"))
        self.statistics = (((self.data.get("analysis") or {}).get("statistics") or {}).get("sections") or {})
        self.forecast = ((self.data.get("analysis") or {}).get("forecast") or {})
        self.financials = self.data.get("financials") or {}

    def _stat_values(self, section: str) -> Mapping[str, Any]:
        return ((self.statistics.get(section) or {}).get("values") or {})

    def _rows(self, key: str) -> List[Dict[str, Any]]:
        obj = self.financials.get(key) or {}
        return [row for row in (obj.get("rows") or []) if isinstance(row, dict)]

    def _annual_rows(self, key: str) -> List[Dict[str, Any]]:
        rows = [r for r in self._rows(key) if r.get("datekey") != "TTM"]
        def sort_key(r: Mapping[str, Any]) -> int:
            try:
                return int(r.get("fiscalYear") or 0)
            except Exception:
                return 0
        return sorted(rows, key=sort_key)

    def _ttm(self, key: str) -> Dict[str, Any]:
        rows = self._rows(key)
        for row in rows:
            if row.get("datekey") == "TTM":
                return row
        return rows[0] if rows else {}

    def extract_metrics(self) -> Dict[str, Any]:
        ratios = self._stat_values("ratios")
        eff = self._stat_values("financialEfficiency")
        pos = self._stat_values("financialPosition")
        margins = self._stat_values("margins")
        divs = self._stat_values("dividends")
        scores = self._stat_values("scores")
        analysts = self._stat_values("analystForecasts")
        income = self._ttm("incomeStatement")
        cash = self._ttm("cashFlow")
        balance = self._ttm("balanceSheet")

        est_stats = ((self.forecast.get("estimates") or {}).get("stats") or {}).get("annual") or {}
        revenue_forecast = _first_number((est_stats.get("revenueNext") or {}).get("growth"))
        eps_forecast = _first_number((est_stats.get("epsNext") or {}).get("growth"))
        if revenue_forecast is None:
            revenue_forecast = _first_number(analysts.get("revenue3y"))
        if eps_forecast is None:
            eps_forecast = _first_number(analysts.get("eps3y"))

        annual_income = self._annual_rows("incomeStatement")
        annual_cash = self._annual_rows("cashFlow")
        rev_vals = [(r.get("revenue")) for r in annual_income[-5:] if _first_number(r.get("revenue"))]
        fcf_vals = [(r.get("fcf")) for r in annual_cash[-5:] if _first_number(r.get("fcf"))]
        rev_cagr = _cagr([float(v) for v in rev_vals], len(rev_vals) - 1) if len(rev_vals) >= 2 else None
        fcf_cagr = _cagr([float(v) for v in fcf_vals], len(fcf_vals) - 1) if len(fcf_vals) >= 2 else None

        return {
            "price": self.current_price,
            "revenue": _first_number(income.get("revenue"), self._stat_values("incomeStatement").get("revenue")),
            "eps": _first_number(income.get("epsdil"), income.get("epsBasic"), self._stat_values("incomeStatement").get("eps")),
            "fcf": _first_number(cash.get("fcf"), self._stat_values("cashFlow").get("fcf")),
            "unleveredFcf": _first_number(cash.get("unleveredFCF"), cash.get("fcf")),
            "fcfPerShare": _first_number(cash.get("fcfps"), self._stat_values("cashFlow").get("fcfps")),
            "shares": _first_number(balance.get("sharesOutTotalCommon"), income.get("sharesDiluted"), (self.data.get("fundamentals") or {}).get("sharesOutstanding")),
            "netCash": _first_number(balance.get("netcash"), 0.0),
            "pe": _first_number(ratios.get("pe"), (self.data.get("fundamentals") or {}).get("peRatio")),
            "forwardPe": _first_number(ratios.get("peForward"), (self.data.get("fundamentals") or {}).get("forwardPE")),
            "pFcf": _first_number(ratios.get("pfcf")),
            "fcfYield": _first_number(divs.get("fcfYield")),
            "grossMargin": _first_number(margins.get("grossMargin")),
            "operatingMargin": _first_number(margins.get("operatingMargin")),
            "profitMargin": _first_number(margins.get("profitMargin")),
            "fcfMargin": _first_number(margins.get("fcfMargin")),
            "roe": _first_number(eff.get("roe")),
            "roic": _first_number(eff.get("roic")),
            "wacc": _first_number(eff.get("wacc")),
            "currentRatio": _first_number(pos.get("currentRatio")),
            "debtEbitda": _first_number(pos.get("debtEbitda")),
            "zScore": _first_number(scores.get("zScore")),
            "fScore": _first_number(scores.get("fScore")),
            "dividendYield": _first_number(divs.get("dividendYield")),
            "buybackYield": _first_number(divs.get("buybackYield")),
            "shareholderYield": _first_number(divs.get("totalReturn")),
            "revenueForecastGrowthPct": revenue_forecast,
            "epsForecastGrowthPct": eps_forecast,
            "historicalRevenueCagr": rev_cagr,
            "historicalFcfCagr": fcf_cagr,
            "analystTarget": _first_number((self.forecast.get("priceTargets") or {}).get("avg"), analysts.get("priceTarget")),
            "analystTargetLow": _first_number((self.forecast.get("priceTargets") or {}).get("low")),
            "analystTargetHigh": _first_number((self.forecast.get("priceTargets") or {}).get("high")),
            "analystCount": _first_number((self.forecast.get("priceTargets") or {}).get("numPriceTargets"), analysts.get("analystCount")),
            "analystConsensus": (self.forecast.get("consensus") or {}).get("consensus") or analysts.get("analystRatings"),
            "lynchFairValue": _first_number(self._stat_values("fairValue").get("lynchFairValue")),
            "grahamNumber": _first_number(self._stat_values("fairValue").get("grahamNumber")),
        }

    def quality_score(self, metrics: Mapping[str, Any]) -> Dict[str, Any]:
        growth = np.mean([
            _score_linear(metrics.get("revenueForecastGrowthPct"), 0, 15),
            _score_linear(metrics.get("epsForecastGrowthPct"), 0, 20),
            _score_linear((metrics.get("historicalRevenueCagr") or 0) * 100, 0, 12),
        ])
        profitability = np.mean([
            _score_linear(metrics.get("grossMargin"), 20, 60),
            _score_linear(metrics.get("operatingMargin"), 10, 35),
            _score_linear(metrics.get("fcfMargin"), 8, 30),
            _score_linear(metrics.get("roic"), 10, 40),
        ])
        health = np.mean([
            _score_linear(metrics.get("currentRatio"), 0.7, 1.5),
            _score_linear(metrics.get("debtEbitda"), 0.0, 3.0, higher_is_better=False),
            _score_linear(metrics.get("zScore"), 1.8, 5.0),
            _score_linear(metrics.get("fScore"), 3, 8),
        ])
        shareholder = np.mean([
            _score_linear(metrics.get("shareholderYield"), 0, 5),
            _score_linear(metrics.get("buybackYield"), 0, 4),
        ])
        overall = float(0.30 * growth + 0.35 * profitability + 0.25 * health + 0.10 * shareholder)
        return {
            "overall": round(overall, 2),
            "label": _label_score(overall),
            "growth": round(float(growth), 2),
            "profitability": round(float(profitability), 2),
            "financialHealth": round(float(health), 2),
            "shareholderReturn": round(float(shareholder), 2),
        }

    def business_context(self) -> Dict[str, Any]:
        """Summarize the business mix from StockAnalysis metrics endpoints.

        Uses the latest quarterly points for segment/geography/margin/opex.
        This does not directly enter the intrinsic-value formula, but enriches
        quality/risk analysis dan membantu membaca sumber pertumbuhan.
        """
        business = self.data.get("businessMetrics") or {}

        def latest_groups(key: str) -> List[Dict[str, Any]]:
            obj = business.get(key) or {}
            rows: List[Dict[str, Any]] = []
            for group in obj.get("quarterly") or []:
                values = group.get("values") or []
                if not values:
                    continue
                point = values[0] if isinstance(values[0], dict) else {}
                rows.append({
                    "name": group.get("name"),
                    "date": point.get("x"),
                    "value": _py(_first_number(point.get("y")), 4),
                    "growthPct": _py(_first_number(point.get("growth")), 2),
                    "change": _py(_first_number(point.get("change")), 4),
                })
            return rows

        segments = latest_groups("revenueBySegment")
        geographies = latest_groups("revenueByGeography")
        margins = latest_groups("grossMarginByType")
        opex = latest_groups("operatingExpenseBreakdown")

        seg_total = sum(float(x["value"]) for x in segments if x.get("value") is not None)
        for row in segments:
            row["mixPct"] = round(float(row["value"]) / seg_total * 100, 2) if seg_total and row.get("value") is not None else None
        geo_total = sum(float(x["value"]) for x in geographies if x.get("value") is not None)
        for row in geographies:
            row["mixPct"] = round(float(row["value"]) / geo_total * 100, 2) if geo_total and row.get("value") is not None else None

        concentration = max((x.get("mixPct") or 0 for x in segments), default=0)
        weighted_growth = _weighted_average([
            (x.get("growthPct"), x.get("mixPct") or 0) for x in segments
        ])
        return {
            "latestSegmentMix": segments,
            "latestGeographicMix": geographies,
            "latestGrossMargins": margins,
            "latestOperatingExpenseGrowth": opex,
            "largestSegmentConcentrationPct": round(concentration, 2),
            "weightedSegmentGrowthPct": _py(weighted_growth, 2),
        }

    def _historical_ratio_values(self, field: str) -> List[float]:
        vals: List[float] = []
        for row in self._annual_rows("ratios")[-8:]:
            val = _first_number(row.get(field))
            if val is not None and val > 0:
                vals.append(val)
        return vals

    def _forward_eps(self, metrics: Mapping[str, Any]) -> Optional[float]:
        stats = ((self.forecast.get("estimates") or {}).get("stats") or {}).get("annual") or {}
        eps_next = _first_number((stats.get("epsNext") or {}).get("this"))
        if eps_next is not None:
            return eps_next
        eps = metrics.get("eps")
        growth = metrics.get("epsForecastGrowthPct")
        if eps is not None and growth is not None:
            return eps * (1 + growth / 100.0)
        return eps

    def dcf_value(self, metrics: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        fcf = metrics.get("unleveredFcf") or metrics.get("fcf")
        shares = metrics.get("shares")
        if not fcf or not shares or fcf <= 0 or shares <= 0:
            return None
        wacc = (metrics.get("wacc") or 9.5) / 100.0
        wacc = _clip(wacc, 0.07, 0.16)
        rev_g = (metrics.get("revenueForecastGrowthPct") or 6.0) / 100.0
        eps_g = (metrics.get("epsForecastGrowthPct") or rev_g * 100) / 100.0
        hist_fcf_g = metrics.get("historicalFcfCagr")
        if hist_fcf_g is None or not isfinite(hist_fcf_g):
            hist_fcf_g = rev_g
        base_growth = _clip(0.40 * rev_g + 0.35 * eps_g + 0.25 * hist_fcf_g, -0.03, 0.20)
        terminal_growth = min(0.03, max(0.015, base_growth * 0.20 + 0.012))
        if terminal_growth >= wacc - 0.02:
            terminal_growth = max(0.01, wacc - 0.025)

        def scenario(growth: float, discount: float, terminal: float) -> float:
            cash = float(fcf)
            pv = 0.0
            for year in range(1, 6):
                # Growth fades toward a mature rate through the explicit period.
                fade = year / 5.0
                g = growth * (1 - 0.55 * fade) + (terminal + 0.01) * (0.55 * fade)
                cash *= 1 + g
                pv += cash / ((1 + discount) ** year)
            terminal_value = cash * (1 + terminal) / max(discount - terminal, 0.02)
            enterprise = pv + terminal_value / ((1 + discount) ** 5)
            equity = enterprise + float(metrics.get("netCash") or 0.0)
            return max(0.0, equity / float(shares))

        low = scenario(base_growth * 0.60, min(0.18, wacc + 0.015), max(0.01, terminal_growth - 0.005))
        base = scenario(base_growth, wacc, terminal_growth)
        high = scenario(min(0.22, base_growth * 1.25 + 0.01), max(0.065, wacc - 0.01), min(0.035, terminal_growth + 0.005))
        return {
            "low": round(low, 2), "base": round(base, 2), "high": round(high, 2),
            "weight": 0.35, "confidence": 70,
            "assumptions": {
                "startingFcf": round(float(fcf), 2), "waccPct": round(wacc * 100, 2),
                "initialGrowthPct": round(base_growth * 100, 2), "terminalGrowthPct": round(terminal_growth * 100, 2),
                "forecastYears": 5,
            },
            "note": "FCF DCF proxy; uses unlevered FCF when available and adds net cash.",
        }

    def earnings_multiple_value(self, metrics: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        eps = self._forward_eps(metrics)
        if eps is None or eps <= 0:
            return None
        hist = self._historical_ratio_values("pe")
        current_forward = metrics.get("forwardPe")
        if hist:
            low_m = float(np.quantile(hist, 0.25))
            base_m = float(np.median(hist))
            high_m = float(np.quantile(hist, 0.75))
        elif current_forward:
            low_m, base_m, high_m = current_forward * 0.8, current_forward, current_forward * 1.15
        else:
            low_m, base_m, high_m = 15.0, 20.0, 25.0
        quality = self.quality_score(metrics)["overall"]
        growth = metrics.get("epsForecastGrowthPct") or 5.0
        adjustment = _clip(0.90 + (quality - 50) / 250.0 + growth / 500.0, 0.80, 1.20)
        base_m *= adjustment
        low_m = min(low_m, base_m * 0.85)
        high_m = max(high_m, base_m * 1.15)
        return {
            "low": round(eps * low_m, 2), "base": round(eps * base_m, 2), "high": round(eps * high_m, 2),
            "weight": 0.30, "confidence": 78,
            "assumptions": {"forwardEps": round(eps, 4), "targetPeLow": round(low_m, 2), "targetPeBase": round(base_m, 2), "targetPeHigh": round(high_m, 2)},
        }

    def fcf_multiple_value(self, metrics: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        fcfps = metrics.get("fcfPerShare")
        if not fcfps or fcfps <= 0:
            return None
        hist = self._historical_ratio_values("pfcf")
        if hist:
            low_m = float(np.quantile(hist, 0.25))
            base_m = float(np.median(hist))
            high_m = float(np.quantile(hist, 0.75))
        elif metrics.get("pFcf"):
            base_m = float(metrics["pFcf"])
            low_m, high_m = base_m * 0.8, base_m * 1.15
        else:
            low_m, base_m, high_m = 15.0, 20.0, 25.0
        return {
            "low": round(fcfps * low_m, 2), "base": round(fcfps * base_m, 2), "high": round(fcfps * high_m, 2),
            "weight": 0.20, "confidence": 72,
            "assumptions": {"fcfPerShare": round(fcfps, 4), "multipleLow": round(low_m, 2), "multipleBase": round(base_m, 2), "multipleHigh": round(high_m, 2)},
        }

    def analyst_value(self, metrics: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        base = metrics.get("analystTarget")
        if not base:
            return None
        low = metrics.get("analystTargetLow") or base * 0.85
        high = metrics.get("analystTargetHigh") or base * 1.15
        count = metrics.get("analystCount") or 0
        confidence = _clip(55 + min(count, 40) / 40 * 30, 55, 85)
        return {
            "low": round(low, 2), "base": round(base, 2), "high": round(high, 2),
            "weight": 0.15, "confidence": round(confidence, 1),
            "assumptions": {"analystCount": int(count) if count else None, "consensus": metrics.get("analystConsensus")},
        }

    def evaluate(self) -> Dict[str, Any]:
        metrics = self.extract_metrics()
        quality = self.quality_score(metrics)
        methods: Dict[str, Dict[str, Any]] = {}
        for name, method in (
            ("dcf", self.dcf_value),
            ("earningsMultiple", self.earnings_multiple_value),
            ("fcfMultiple", self.fcf_multiple_value),
            ("analystConsensus", self.analyst_value),
        ):
            result = method(metrics)
            if result:
                methods[name] = result

        bases = [(m.get("base"), m.get("weight", 0)) for m in methods.values()]
        lows = [(m.get("low"), m.get("weight", 0)) for m in methods.values()]
        highs = [(m.get("high"), m.get("weight", 0)) for m in methods.values()]
        base = _weighted_average(bases)
        low = _weighted_average(lows)
        high = _weighted_average(highs)
        price = self.current_price
        upside = ((base / price) - 1) * 100 if base is not None and price else None
        valid_bases = [m["base"] for m in methods.values() if m.get("base")]
        dispersion = float(np.std(valid_bases) / np.mean(valid_bases)) if len(valid_bases) >= 2 and np.mean(valid_bases) else 0.30
        completeness = min(1.0, len(methods) / 4.0)
        confidence = _clip(85 - dispersion * 110, 35, 90) * (0.65 + 0.35 * completeness)

        # Attractiveness combines business quality with margin-to-fair-value.
        valuation_score = 50.0 if upside is None else _clip(50 + upside * 1.6, 0, 100)
        attractiveness = 0.58 * quality["overall"] + 0.42 * valuation_score

        business_context = self.business_context()

        references = {
            "lynchFairValue": metrics.get("lynchFairValue"),
            "grahamNumber": metrics.get("grahamNumber"),
            "note": "Lynch/Graham are shown as reference-only and excluded from consensus weighting for modern growth companies.",
        }

        return {
            "currentPrice": _py(price, 4),
            "metrics": {k: _py(v, 6) for k, v in metrics.items()},
            "quality": quality,
            "businessContext": business_context,
            "methods": methods,
            "referenceFormulas": references,
            "consensus": {
                "low": _py(low, 2), "base": _py(base, 2), "high": _py(high, 2),
                "upsideToBasePct": _py(upside, 2),
                "valuation": _valuation_label(upside),
                "confidence": round(confidence, 2),
                "methodDispersionPct": round(dispersion * 100, 2),
            },
            "attractiveness": {"score": round(attractiveness, 2), "label": _label_score(attractiveness)},
        }


# =============================================================================
# Professional integrated analyzer
# =============================================================================


class ProfessionalStockAnalyzer:
    """Integrated multi-timeframe technical + fundamental analyzer."""

    def __init__(
        self,
        ticker: str,
        price_data: Optional[pd.DataFrame] = None,
        fundamental_data: Optional[Mapping[str, Any]] = None,
        period: str = "5y",
        interval: str = "1d",
        auto_fetch: bool = True,
    ):
        self.ticker = str(ticker).strip().upper()
        self.fundamental_data = fundamental_data or {}
        self.price_error: Optional[str] = None
        if price_data is None and auto_fetch:
            try:
                price_data = fetch_yahoo_history(self.ticker, period=period, interval=interval)
            except Exception as exc:
                # Fundamental analysis remains useful when the historical price
                # provider is unavailable. The error is recorded in data quality.
                self.price_error = str(exc)
                price_data = None
        self.price_data = standardize_ohlcv(price_data) if price_data is not None else None
        self.indicators = compute_indicators(self.price_data) if self.price_data is not None else None

    def _technical(self, specs: Sequence[TimeframeSpec]) -> Dict[str, Any]:
        if self.indicators is None:
            return {"available": False, "reason": self.price_error or "Historical OHLCV is unavailable", "timeframes": {}}
        outputs = {spec.key: analyze_technical_timeframe(self.price_data, spec) for spec in specs}
        scores = [v["technicalScore"] for v in outputs.values()]
        return {
            "available": True,
            "dataPoints": len(self.indicators),
            "from": _py(self.indicators.index[0]),
            "to": _py(self.indicators.index[-1]),
            "aggregateScore": round(float(np.mean(scores)), 2) if scores else None,
            "timeframes": outputs,
        }

    def _fundamental(self) -> Dict[str, Any]:
        if not self.fundamental_data:
            return {"available": False, "reason": "Fundamental data is unavailable"}
        result = FundamentalValuationEngine(self.fundamental_data).evaluate()
        result["available"] = True
        return result

    def _blend_timeframes(self, specs: Sequence[TimeframeSpec], technical: Mapping[str, Any], fundamental: Mapping[str, Any]) -> Dict[str, Any]:
        output: Dict[str, Any] = {}
        f_cons = fundamental.get("consensus") or {}
        f_base, f_low, f_high = f_cons.get("base"), f_cons.get("low"), f_cons.get("high")
        f_attr = ((fundamental.get("attractiveness") or {}).get("score"))
        current = fundamental.get("currentPrice")
        if current is None and technical.get("available"):
            tf_values = technical.get("timeframes") or {}
            first = next(iter(tf_values.values()), {})
            current = first.get("currentPrice")

        for spec in specs:
            t = (technical.get("timeframes") or {}).get(spec.key) or {}
            t_proj = t.get("projection") or {}
            t_base, t_low, t_high = t_proj.get("base"), t_proj.get("bear"), t_proj.get("bull")
            tw, fw = spec.technical_weight, spec.fundamental_weight

            # Re-normalize weights when one side is unavailable.
            if t_base is None and f_base is not None:
                tw, fw = 0.0, 1.0
            elif f_base is None and t_base is not None:
                tw, fw = 1.0, 0.0
            total_w = tw + fw
            if total_w <= 0:
                continue
            tw, fw = tw / total_w, fw / total_w

            base = _weighted_average([(t_base, tw), (f_base, fw)])
            low = _weighted_average([(t_low, tw), (f_low, fw)])
            high = _weighted_average([(t_high, tw), (f_high, fw)])
            upside = ((base / current) - 1) * 100 if base is not None and current else None
            t_score = t.get("technicalScore")
            composite = _weighted_average([(t_score, tw), (f_attr, fw)]) or 50.0
            confidence = _weighted_average([
                (t.get("confidence"), tw),
                (f_cons.get("confidence"), fw),
            ])

            output[spec.key] = {
                "label": spec.label,
                "horizonTradingDays": spec.horizon_days,
                "weights": {"technical": round(tw, 2), "fundamental": round(fw, 2)},
                "currentPrice": _py(current, 4),
                "fairValue": {"low": _py(low, 2), "base": _py(base, 2), "high": _py(high, 2)},
                "expectedGapPct": _py(upside, 2),
                "valuation": _valuation_label(upside),
                "compositeScore": round(composite, 2),
                "stance": _label_score(composite),
                "confidence": _py(confidence, 2),
                "technicalTrend": t.get("trend"),
                "technicalScore": t_score,
            }
        return output

    def _catalysts_and_risks(self, fundamental: Mapping[str, Any], technical: Mapping[str, Any]) -> Tuple[List[str], List[str]]:
        catalysts: List[str] = []
        risks: List[str] = []
        metrics = fundamental.get("metrics") or {}
        consensus = fundamental.get("consensus") or {}
        quality = fundamental.get("quality") or {}
        business = fundamental.get("businessContext") or {}

        if (metrics.get("revenueForecastGrowthPct") or 0) >= 10:
            catalysts.append(f"Revenue forecast remains double-digit ({metrics['revenueForecastGrowthPct']:.1f}%).")
        if (metrics.get("epsForecastGrowthPct") or 0) > (metrics.get("revenueForecastGrowthPct") or 0) + 2:
            catalysts.append("EPS growth is projected above revenue growth, indicating potential operating leverage.")
        if (metrics.get("buybackYield") or 0) >= 2:
            catalysts.append(f"Buyback yield is meaningful at about {metrics['buybackYield']:.1f}%.")
        if (metrics.get("roic") or 0) >= 20:
            catalysts.append(f"Capital efficiency is strong with ROIC around {metrics['roic']:.1f}%.")
        if (consensus.get("upsideToBasePct") or 0) >= 10:
            catalysts.append("Fundamental consensus fair value indicates a double-digit valuation gap.")
        if (business.get("weightedSegmentGrowthPct") or 0) >= 10:
            catalysts.append(f"Latest weighted segment growth is broad and strong at about {business['weightedSegmentGrowthPct']:.1f}%.")
        for margin in business.get("latestGrossMargins") or []:
            if margin.get("name") == "Services" and (margin.get("value") or 0) >= 60:
                catalysts.append(f"Services gross margin remains structurally high at about {margin['value']:.1f}%.")
                break

        if (metrics.get("pe") or 0) >= 30:
            risks.append(f"Valuation is demanding with trailing P/E around {metrics['pe']:.1f}x.")
        if metrics.get("fcfYield") is not None and metrics["fcfYield"] < 3:
            risks.append(f"FCF yield is relatively thin at about {metrics['fcfYield']:.1f}%.")
        if metrics.get("currentRatio") is not None and metrics["currentRatio"] < 1:
            risks.append("Current ratio is below 1.0, reducing short-term balance-sheet cushion.")
        if (consensus.get("methodDispersionPct") or 0) > 25:
            risks.append("Valuation methods show wide dispersion; fair-value uncertainty is elevated.")
        if quality.get("growth") is not None and quality["growth"] < 45:
            risks.append("Growth score is below neutral and could limit multiple expansion.")
        if (business.get("largestSegmentConcentrationPct") or 0) >= 45:
            risks.append(f"Revenue concentration is high: the largest segment contributes about {business['largestSegmentConcentrationPct']:.1f}% of the latest segment mix.")
        for expense in business.get("latestOperatingExpenseGrowth") or []:
            if expense.get("name") == "Research and Development" and (expense.get("growthPct") or 0) >= 25:
                risks.append(f"R&D expense growth is elevated at about {expense['growthPct']:.1f}%, which can pressure near-term operating leverage if monetization lags.")
                break

        tf = technical.get("timeframes") or {}
        bearish = [key for key, value in tf.items() if (value.get("technicalScore") or 50) < 40]
        if bearish:
            risks.append("Technical momentum is weak on: " + ", ".join(bearish) + ".")
        bullish = [key for key, value in tf.items() if (value.get("technicalScore") or 50) > 65]
        if bullish:
            catalysts.append("Technical trend is constructive on: " + ", ".join(bullish) + ".")

        return catalysts, risks

    def _executive_summary(self, blended: Mapping[str, Any], fundamental: Mapping[str, Any], technical: Mapping[str, Any]) -> Dict[str, Any]:
        if blended:
            weighted = []
            for key, item in blended.items():
                horizon_weight = {"1m": 0.15, "3m": 0.20, "6m": 0.25, "12m": 0.40}.get(key, 0.25)
                weighted.append((item.get("compositeScore"), horizon_weight))
            overall = _weighted_average(weighted) or 50.0
        else:
            overall = ((fundamental.get("attractiveness") or {}).get("score")) or technical.get("aggregateScore") or 50.0
        f_cons = fundamental.get("consensus") or {}
        return {
            "overallScore": round(overall, 2),
            "stance": _label_score(overall),
            "currentPrice": fundamental.get("currentPrice") or next((x.get("currentPrice") for x in (technical.get("timeframes") or {}).values()), None),
            "fundamentalFairValue": f_cons.get("base"),
            "fundamentalValuation": f_cons.get("valuation"),
            "fundamentalQuality": (fundamental.get("quality") or {}).get("label"),
            "technicalAggregateScore": technical.get("aggregateScore"),
            "interpretation": (
                "Score combines business quality, valuation gap, trend, momentum, volume and volatility. "
                "Short horizons are technical-heavy; long horizons are fundamental-heavy."
            ),
        }

    def _professional_report(
        self,
        summary: Mapping[str, Any],
        blended: Mapping[str, Any],
        fundamental: Mapping[str, Any],
        technical: Mapping[str, Any],
        catalysts: Sequence[str],
        risks: Sequence[str],
    ) -> Dict[str, Any]:
        f_cons = fundamental.get("consensus") or {}
        quality = fundamental.get("quality") or {}
        business = fundamental.get("businessContext") or {}
        current = summary.get("currentPrice")
        base = f_cons.get("base")
        low = f_cons.get("low")
        high = f_cons.get("high")
        valuation_text = "Fundamental fair value is not yet available."
        if current is not None and base is not None:
            valuation_text = (
                f"Price of {current:.2f} versus fundamental fair value of {base:.2f} "
                f"(range {low:.2f}-{high:.2f}) results in a {f_cons.get('upsideToBasePct', 0):+.1f}% gap. "
                f"Valuation status: {f_cons.get('valuation')}."
            )

        thesis: List[str] = []
        if quality:
            thesis.append(
                f"Business quality {quality.get('label')} ({quality.get('overall')}/100): "
                f"profitability {quality.get('profitability')}/100, growth {quality.get('growth')}/100, "
                f"financial health {quality.get('financialHealth')}/100."
            )
        if business.get("weightedSegmentGrowthPct") is not None:
            thesis.append(
                f"Latest weighted segment growth is approximately {business['weightedSegmentGrowthPct']:.1f}%, "
                f"with largest-segment concentration around {business.get('largestSegmentConcentrationPct', 0):.1f}%."
            )
        if f_cons.get("methodDispersionPct") is not None:
            thesis.append(
                f"Valuation dispersion across methods is {f_cons['methodDispersionPct']:.1f}%, so fair value should be interpreted as a range, not a point estimate."
            )

        outlook: Dict[str, str] = {}
        for key, item in blended.items():
            fv = item.get("fairValue") or {}
            gap = item.get("expectedGapPct")
            gap_text = f"{gap:+.1f}%" if gap is not None else "n/a"
            outlook[key] = (
                f"{item.get('label')}: {item.get('stance')} (score {item.get('compositeScore')}/100), "
                f"technical trend {item.get('technicalTrend') or 'n/a'}, blended fair-value range "
                f"{fv.get('low')}-{fv.get('high')} with base {fv.get('base')} ({gap_text} vs current)."
            )

        levels = {}
        tf = technical.get("timeframes") or {}
        tactical = tf.get("3m") or tf.get("1m") or next(iter(tf.values()), {})
        if tactical:
            levels = tactical.get("levels") or {}

        methods = fundamental.get("methods") or {}
        available_methods = list(methods.keys())
        data_quality = {
            "fundamentalMethodsUsed": available_methods,
            "fundamentalMethodCount": len(available_methods),
            "fundamentalConfidence": f_cons.get("confidence"),
            "technicalAvailable": technical.get("available"),
            "technicalDataPoints": technical.get("dataPoints"),
            "timeframesAnalyzed": list(blended.keys()),
        }

        return {
            "headline": f"{self.ticker}: {summary.get('stance')} multi-horizon setup (score {summary.get('overallScore')}/100).",
            "investmentCase": thesis,
            "valuationView": valuation_text,
            "timeframeOutlook": outlook,
            "tacticalLevels": {
                "supports": levels.get("supports") or [],
                "resistances": levels.get("resistances") or [],
                "note": "Levels are technical reference zones, not guaranteed execution prices.",
            },
            "catalysts": list(catalysts),
            "keyRisks": list(risks),
            "dataQuality": data_quality,
            "professionalInterpretation": (
                "Use the 1m/3m view for timing and risk management, 6m for transition between momentum and valuation, "
                "and 12m for the fundamental thesis. A high-quality business can still have poor expected returns when "
                "the entry valuation is demanding; conversely, attractive valuation without technical confirmation can remain weak for an extended period."
            ),
        }

    def analyze(self, timeframes: Optional[Sequence[Any]] = None) -> Dict[str, Any]:
        specs = resolve_timeframes(timeframes)
        technical = self._technical(specs)
        fundamental = self._fundamental()
        blended = self._blend_timeframes(specs, technical, fundamental)
        catalysts, risks = self._catalysts_and_risks(fundamental, technical)
        summary = self._executive_summary(blended, fundamental, technical)
        professional_report = self._professional_report(summary, blended, fundamental, technical, catalysts, risks)
        technical_fair_value = {
            key: (value.get("technicalReference") or {})
            for key, value in (technical.get("timeframes") or {}).items()
        }
        return {
            "ticker": self.ticker,
            "engineVersion": __version__,
            "executiveSummary": summary,
            "professionalReport": professional_report,
            "timeframes": blended,
            "fairValue": {
                "fundamental": fundamental.get("consensus") if fundamental.get("available") else None,
                "technicalReferenceByTimeframe": technical_fair_value,
                "blendedByTimeframe": {key: value.get("fairValue") for key, value in blended.items()},
            },
            "fundamental": fundamental,
            "technical": technical,
            "catalysts": catalysts,
            "risks": risks,
            "methodology": {
                "timeframes": [asdict(spec) for spec in specs],
                "fundamental": "Weighted DCF proxy, forward earnings multiple, FCF multiple and analyst consensus.",
                "technical": "Trend/momentum/volume/Ichimoku score plus log-price regression and realized-volatility scenario envelope.",
                "blending": "Technical weight decreases and fundamental weight increases as investment horizon lengthens.",
            },
            "disclaimer": "Analytical model output, not personalized investment advice or a guaranteed price forecast.",
        }


# =============================================================================
# Backward-compatible StockAnalyzer
# =============================================================================


class StockAnalyzer:
    """Compatibility layer for the legacy technical.py API and new engine.

    ``analyze_all()`` still returns ``(result, dataframe)`` and legacy keys
    remain available, but indicator calculations no longer depend on
    package ``ta``.
    """

    def __init__(self, ticker: str, period: str = "ytd", price_data: Optional[pd.DataFrame] = None):
        self.ticker = str(ticker).strip().upper()
        self.period = period
        self.df = standardize_ohlcv(price_data) if price_data is not None else fetch_yahoo_history(self.ticker, period=period)
        self.df = compute_indicators(self.df)
        self.result: Dict[str, Any] = {}

    def calculate_moving_averages(self):
        return None

    def calculate_momentum_indicators(self):
        return None

    def calculate_volatility_indicators(self):
        return None

    def calculate_volume_indicators(self):
        return None

    def calculate_ichimoku_cloud(self):
        return None

    def calculate_fibonacci_levels(self):
        recent = self.df.tail(min(50, len(self.df)))
        low, high = float(recent["Close"].min()), float(recent["Close"].max())
        delta = high - low
        self.result["Fibonacci Levels"] = {
            "0%": high, "23.6%": high - delta * 0.236, "38.2%": high - delta * 0.382,
            "50%": high - delta * 0.5, "61.8%": high - delta * 0.618, "100%": low,
        }

    def analyze_trend(self):
        t = analyze_technical_timeframe(self.df, DEFAULT_TIMEFRAMES["3m"])
        self.result["Trend"] = t["trend"]
        self.result["Current Price"] = t["currentPrice"]

    def analyze_ma_status(self):
        last = self.df.iloc[-1]
        p, ma20, ma50, ma200 = last["Close"], last["SMA20"], last["SMA50"], last["SMA200"]
        if p > ma20 > ma50 > ma200:
            status = "Bullish (Price > MA20 > MA50 > MA200)"
        elif p < ma20 < ma50 < ma200:
            status = "Bearish (Price < MA20 < MA50 < MA200)"
        elif p > ma20 > ma50:
            status = "Bullish Crossover (Price > MA20 > MA50)"
        elif p < ma20 < ma50:
            status = "Bearish Crossover (Price < MA20 < MA50)"
        else:
            status = "Mixed Signals (Watch for confirmation)"
        self.result["MA Status"] = status

    def analyze_macd(self):
        last = self.df.iloc[-1]
        self.result["MACD Signal"] = "Bullish (MACD > Signal)" if last["MACD"] > last["MACD_signal"] else "Bearish (MACD < Signal)"

    def analyze_rsi(self):
        last = self.df.iloc[-1]
        rsi = float(last["RSI"])
        if rsi > 70:
            signal = "Overbought (RSI > 70)"
        elif rsi < 30:
            signal = "Oversold (RSI < 30)"
        elif rsi > last["RSI_MA"]:
            signal = "Bullish (RSI > MA)"
        else:
            signal = "Bearish (RSI < MA)"
        self.result["RSI (14)"] = round(rsi, 2)
        self.result["RSI Signal"] = signal

    def analyze_stochastic(self):
        last = self.df.iloc[-1]
        k, d = last["Stoch_%K"], last["Stoch_%D"]
        if k > 80:
            signal = "Overbought (%K > 80)"
        elif k < 20:
            signal = "Oversold (%K < 20)"
        else:
            signal = "Bullish Cross (%K > %D)" if k > d else "Bearish Cross (%K < %D)"
        self.result["Stochastic Signal"] = signal

    def analyze_volume(self):
        last = self.df.iloc[-1]
        self.result["Volume Signal"] = "Bullish (OBV > MA)" if last["OBV"] > last["OBV_MA"] else "Bearish (OBV < MA)"

    def analyze_vwap(self):
        last = self.df.iloc[-1]
        if pd.isna(last["VWAP"]):
            signal = "Unavailable (zero/missing volume)"
        else:
            signal = "Bullish (Price > VWAP)" if last["Close"] > last["VWAP"] else "Bearish (Price < VWAP)"
        self.result["VWAP Signal"] = signal

    def analyze_ichimoku(self):
        last = self.df.iloc[-1]
        price = last["Close"]
        high, low = max(last["Senkou_span_a"], last["Senkou_span_b"]), min(last["Senkou_span_a"], last["Senkou_span_b"])
        if price > high and last["Tenkan_sen"] > last["Kijun_sen"]:
            signal = "Strong Bullish (Price above cloud, Tenkan > Kijun)"
        elif price > high:
            signal = "Bullish (Price above cloud)"
        elif price < low and last["Tenkan_sen"] < last["Kijun_sen"]:
            signal = "Strong Bearish (Price below cloud, Tenkan < Kijun)"
        elif price < low:
            signal = "Bearish (Price below cloud)"
        else:
            signal = "Neutral (Price in cloud)"
        self.result["Ichimoku Signal"] = signal

    def calculate_support_resistance(self):
        levels = _support_resistance(self.df, 63)
        self.result["Support Level"] = levels["supports"][0] if levels["supports"] else None
        self.result["Resistance Level"] = levels["resistances"][0] if levels["resistances"] else None
        self.result["Key Support Levels"] = levels["supports"][:3]
        self.result["Key Resistance Levels"] = levels["resistances"][:3]

    def calculate_risk_reward(self):
        t = analyze_technical_timeframe(self.df, DEFAULT_TIMEFRAMES["3m"])
        price = t["currentPrice"]
        bear, base, bull = t["projection"]["bear"], t["projection"]["base"], t["projection"]["bull"]
        atr = t["indicators"]["atr14"] or price * 0.02
        supports = t["levels"]["supports"]
        stop = (supports[0] - 0.5 * atr) if supports else price - 2 * atr
        self.result["Potential Upside"] = f"{round((base / price - 1) * 100, 2)}% to {round(base, 2)}"
        self.result["Potential Downside"] = f"-{round((price / max(stop, 1e-9) - 1) * 100, 2)}% to {round(stop, 2)}"
        self.result["Entry Zone"] = f"{round(max(stop, price - atr), 2)} - {round(price, 2)}"
        self.result["Target 1"] = round(base, 2)
        self.result["Target 2"] = round(bull, 2)
        self.result["Stop Loss"] = f"< {round(stop, 2)}"

    def summarize_indicators(self):
        t = analyze_technical_timeframe(self.df, DEFAULT_TIMEFRAMES["3m"])
        self.result["Indicators Summary"] = {
            "Trend Confirmation": "Confirmed" if abs(t["technicalScore"] - 50) >= 15 else "Unconfirmed",
            "Volume Confirmation": self.result.get("Volume Signal"),
            "Volatility": "High" if (t["indicators"]["annualizedVolatility"] or 0) > 0.35 else "Medium" if (t["indicators"]["annualizedVolatility"] or 0) > 0.20 else "Low",
            "Technical Score": t["technicalScore"],
        }
        self.result["Ticker"] = self.ticker

    def analyze_all(self):
        self.calculate_fibonacci_levels()
        self.analyze_trend()
        self.analyze_ma_status()
        self.analyze_macd()
        self.analyze_rsi()
        self.analyze_stochastic()
        self.analyze_volume()
        self.analyze_vwap()
        self.analyze_ichimoku()  # Fixed legacy bug: now actually included in the result.
        self.calculate_support_resistance()
        self.calculate_risk_reward()
        self.summarize_indicators()
        self.result["Multi Timeframe"] = {
            key: analyze_technical_timeframe(self.df, spec)
            for key, spec in DEFAULT_TIMEFRAMES.items()
        }
        return self.result, self.df


__all__ = [
    "TimeframeSpec", "DEFAULT_TIMEFRAMES", "resolve_timeframes",
    "standardize_ohlcv", "fetch_yahoo_history", "compute_indicators",
    "analyze_technical_timeframe", "FundamentalValuationEngine",
    "ProfessionalStockAnalyzer", "StockAnalyzer",
]
