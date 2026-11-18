from __future__ import annotations
from dataclasses import dataclass
from math import exp, isfinite, sqrt
from typing import Any, Dict, List, Mapping, Optional, Sequence
import pandas as pd
from .fundamental import _confidence_label, _weighted_average
from .indicators import _bounded_tanh, _compress_ohlcv, _regression_projection, _support_resistance, compute_indicators
from .market_data import _clip, _py, _series_last, standardize_ohlcv

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
    "1m": TimeframeSpec("1m", "Short Term (1 month)", 21, 252, 0.80, 0.20, 1),
    "3m": TimeframeSpec("3m", "Swing / Tactical (3 months)", 63, 400, 0.65, 0.35, 2),
    "6m": TimeframeSpec("6m", "Medium Term (6 months)", 126, 1000, 0.45, 0.55, 5),
    "12m": TimeframeSpec("12m", "Long Term (12 months)", 252, 2000, 0.25, 0.75, 10),
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
    stoch_k = _series_last(df["Stoch_%K"])
    stoch_d = _series_last(df["Stoch_%D"])
    bb_upper = _series_last(df["BB_upper"])
    bb_middle = _series_last(df["BB_middle"])
    bb_lower = _series_last(df["BB_lower"])
    adx = _series_last(df["ADX"])
    plus_di = _series_last(df["PLUS_DI"])
    minus_di = _series_last(df["MINUS_DI"])

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
    if stoch_k is not None:
        score += 3.0 * _bounded_tanh(stoch_k - 50.0, 25.0)
    if bb_upper is not None and bb_middle is not None and bb_lower is not None:
        band_half_width = max((bb_upper - bb_lower) / 2.0, atr * 0.25)
        score += 3.0 * _bounded_tanh((price - bb_middle) / band_half_width, 1.0)
    if adx is not None and plus_di is not None and minus_di is not None:
        trend_strength = min(adx / 40.0, 1.0)
        score += 5.0 * _bounded_tanh(plus_di - minus_di, 20.0) * trend_strength

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
    previous_log_ret = log_ret.iloc[:-1]
    previous_annual_vol = (
        float(
            previous_log_ret.tail(vol_window).std(ddof=0)
            * sqrt(252.0 / max(spec.bar_days, 1))
        )
        if len(previous_log_ret) >= 5
        else None
    )

    previous = df.iloc[-2] if len(df) > 1 else None

    def indicator_value(row: Optional[pd.Series], name: str) -> Optional[float]:
        if row is None or name not in row:
            return None
        value = row[name]
        if pd.isna(value):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if isfinite(number) else None

    def crossed_above(left: str, right: str) -> Optional[bool]:
        previous_left = indicator_value(previous, left)
        previous_right = indicator_value(previous, right)
        current_left = indicator_value(last, left)
        current_right = indicator_value(last, right)
        if None in (previous_left, previous_right, current_left, current_right):
            return None
        return previous_left <= previous_right and current_left > current_right

    def crossed_below(left: str, right: str) -> Optional[bool]:
        previous_left = indicator_value(previous, left)
        previous_right = indicator_value(previous, right)
        current_left = indicator_value(last, left)
        current_right = indicator_value(last, right)
        if None in (previous_left, previous_right, current_left, current_right):
            return None
        return previous_left >= previous_right and current_left < current_right

    def crossed_threshold(
        name: str,
        threshold: float,
        *,
        upward: bool,
    ) -> Optional[bool]:
        previous_value = indicator_value(previous, name)
        current_value = indicator_value(last, name)
        if previous_value is None or current_value is None:
            return None
        if upward:
            return previous_value < threshold and current_value >= threshold
        return previous_value >= threshold and current_value < threshold

    def changed_direction(name: str, *, increasing: bool) -> Optional[bool]:
        previous_value = indicator_value(previous, name)
        current_value = indicator_value(last, name)
        if previous_value is None or current_value is None:
            return None
        return current_value > previous_value if increasing else current_value < previous_value

    def value_changed_direction(
        current_value: Optional[float],
        previous_value: Optional[float],
        *,
        increasing: bool,
    ) -> Optional[bool]:
        if current_value is None or previous_value is None:
            return None
        return current_value > previous_value if increasing else current_value < previous_value

    technical_signals = {
        "goldenCross": crossed_above("SMA50", "SMA200"),
        "deathCross": crossed_below("SMA50", "SMA200"),
        "sma20BullishCross": crossed_above("SMA20", "SMA50"),
        "sma20BearishCross": crossed_below("SMA20", "SMA50"),
        "rsiRecoveryAbove30": crossed_threshold("RSI", 30.0, upward=True),
        "rsiCrossAbove50": crossed_threshold("RSI", 50.0, upward=True),
        "rsiCrossBelow50": crossed_threshold("RSI", 50.0, upward=False),
        "macdBullishCross": crossed_above("MACD", "MACD_signal"),
        "macdBearishCross": crossed_below("MACD", "MACD_signal"),
        "macdHistogramIncreasing": changed_direction("MACD_hist", increasing=True),
        "macdHistogramDecreasing": changed_direction("MACD_hist", increasing=False),
        "macdZeroBullishCross": crossed_threshold("MACD", 0.0, upward=True),
        "macdZeroBearishCross": crossed_threshold("MACD", 0.0, upward=False),
        "stochasticBullishCross": crossed_above("Stoch_%K", "Stoch_%D"),
        "stochasticBearishCross": crossed_below("Stoch_%K", "Stoch_%D"),
        "directionalBullishCross": crossed_above("PLUS_DI", "MINUS_DI"),
        "directionalBearishCross": crossed_below("PLUS_DI", "MINUS_DI"),
        "atrExpansion": changed_direction("ATR", increasing=True),
        "atrContraction": changed_direction("ATR", increasing=False),
        "volatilityExpansion": value_changed_direction(
            annual_vol if len(log_ret) >= 5 else None,
            previous_annual_vol,
            increasing=True,
        ),
        "volatilityContraction": value_changed_direction(
            annual_vol if len(log_ret) >= 5 else None,
            previous_annual_vol,
            increasing=False,
        ),
    }

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

    indicator_values = [
        sma20, sma50, sma200, rsi, macd, macd_signal, vwap, tenkan, kijun, span_a, span_b,
        stoch_k, stoch_d, bb_upper, bb_middle, bb_lower, adx, plus_di, minus_di,
    ]
    indicator_coverage = sum(v is not None for v in indicator_values) / len(indicator_values)
    market_meta = dict((getattr(price_or_indicators, "attrs", {}) or {}).get("market_meta") or {})
    price_quality = market_meta.get("quality") or {}
    freshness_penalty = 12.0 if price_quality.get("stale") else 0.0
    confidence = 15.0
    confidence += min(25.0, len(raw) / max(spec.lookback_days, 1) * 25.0)
    confidence += min(20.0, len(df) / 80.0 * 20.0)
    confidence += min(15.0, max(0.0, float(regression.get("r2") or 0.0)) * 15.0)
    confidence += indicator_coverage * 15.0
    confidence += min(10.0, abs(score - 50.0) / 50.0 * 10.0)
    confidence -= freshness_penalty
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
        "confidenceLabel": _confidence_label(confidence),
        "dataQuality": {
            "indicatorCoveragePct": round(indicator_coverage * 100.0, 1),
            "priceHistory": price_quality or None,
            "warnings": list(price_quality.get("warnings") or []),
        },
        "indicators": {
            "sma20": _py(sma20, 4), "sma50": _py(sma50, 4), "sma200": _py(sma200, 4),
            "rsi14": _py(rsi, 2), "macd": _py(macd, 4), "macdSignal": _py(macd_signal, 4),
            "stochasticK": _py(stoch_k, 2), "stochasticD": _py(stoch_d, 2),
            "bollingerUpper": _py(bb_upper, 4), "bollingerMiddle": _py(bb_middle, 4),
            "bollingerLower": _py(bb_lower, 4),
            "adx14": _py(adx, 2), "plusDI14": _py(plus_di, 2), "minusDI14": _py(minus_di, 2),
            "vwap20": _py(vwap, 4), "vwap60": _py(vwap60, 4), "atr14": _py(atr, 4),
            "annualizedVolatility": _py(annual_vol, 4),
            "ichimoku": {"tenkan": _py(tenkan, 4), "kijun": _py(kijun, 4), "spanA": _py(span_a, 4), "spanB": _py(span_b, 4)},
        },
        "signals": technical_signals,
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
