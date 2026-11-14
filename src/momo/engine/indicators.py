from __future__ import annotations
from math import isfinite, sqrt
from typing import Dict, List, Optional
import numpy as np
import pandas as pd
from .market_data import _clip, _series_last, standardize_ohlcv

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
        # Long moving averages should not appear "ready" after only ~20 bars.
        # Requiring 60% of the intended window prevents false long-term signals.
        min_periods = max(5, int(round(window * 0.60)))
        df[f"SMA{window}"] = close.rolling(window, min_periods=min_periods).mean()
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
