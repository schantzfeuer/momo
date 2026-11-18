from __future__ import annotations
from math import isfinite, sqrt
from typing import Dict, List, Optional
import numpy as np
import pandas as pd
from .market_data import _clip, _series_last, standardize_ohlcv

try:
    import talib
except ImportError:
    talib = None

def compute_indicators(price_data: pd.DataFrame) -> pd.DataFrame:
    if talib is None:
        raise ImportError("Technical analysis requires TA-Lib; install the analysis extra.")
    df = standardize_ohlcv(price_data)
    close = df["Close"]

    close_values = close.to_numpy(dtype=float)
    high_values = df["High"].to_numpy(dtype=float)
    low_values = df["Low"].to_numpy(dtype=float)
    volume_values = df["Volume"].fillna(0.0).to_numpy(dtype=float)
    for window in (10, 20, 50, 100, 200):
        df[f"SMA{window}"] = talib.SMA(close_values, timeperiod=window)
    df["EMA12"] = talib.EMA(close_values, timeperiod=12)
    df["EMA26"] = talib.EMA(close_values, timeperiod=26)
    macd, signal, histogram = talib.MACD(close_values, fastperiod=12, slowperiod=26, signalperiod=9)
    df["MACD"], df["MACD_signal"], df["MACD_hist"] = macd, signal, histogram
    df["RSI"] = talib.RSI(close_values, timeperiod=14)
    df["ADX"] = talib.ADX(high_values, low_values, close_values, timeperiod=14)
    df["PLUS_DI"] = talib.PLUS_DI(high_values, low_values, close_values, timeperiod=14)
    df["MINUS_DI"] = talib.MINUS_DI(high_values, low_values, close_values, timeperiod=14)
    slow_k, slow_d = talib.STOCH(high_values, low_values, close_values, fastk_period=14,
                                 slowk_period=3, slowk_matype=0, slowd_period=3, slowd_matype=0)
    df["Stoch_%K"], df["Stoch_%D"] = slow_k, slow_d
    upper, middle, lower = talib.BBANDS(close_values, timeperiod=20, nbdevup=2, nbdevdn=2, matype=0)
    df["BB_upper"], df["BB_middle"], df["BB_lower"] = upper, middle, lower
    df["ATR"] = talib.ATR(high_values, low_values, close_values, timeperiod=14)
    df["OBV"] = talib.OBV(close_values, volume_values)
    df["RSI_MA"] = df["RSI"].rolling(9, min_periods=2).mean()

    typical = (df["High"] + df["Low"] + df["Close"]) / 3.0
    volume = df["Volume"].fillna(0.0)
    pv = typical * volume
    vol20 = volume.rolling(20, min_periods=1).sum()
    df["VWAP"] = pv.rolling(20, min_periods=1).sum() / vol20.replace(0, np.nan)
    vol60 = volume.rolling(60, min_periods=1).sum()
    df["VWAP60"] = pv.rolling(60, min_periods=1).sum() / vol60.replace(0, np.nan)

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
