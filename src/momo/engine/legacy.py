from __future__ import annotations
import warnings
from typing import Any, Dict, Optional
import pandas as pd
from .indicators import _support_resistance, compute_indicators
from .market_data import fetch_yahoo_history, standardize_ohlcv
from .technical import DEFAULT_TIMEFRAMES, analyze_technical_timeframe

warnings.warn(
    "momo.engine.legacy.StockAnalyzer is deprecated; use "
    "momo.engine.ProfessionalStockAnalyzer or Momo.analyze_stock instead.",
    DeprecationWarning,
    stacklevel=2,
)

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
