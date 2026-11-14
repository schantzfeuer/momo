"""Optional analytical engine; import this package only for stock analysis."""
from .. import __version__
from .market_data import assess_price_history, fetch_yahoo_history, standardize_ohlcv
from .indicators import compute_indicators
from .technical import DEFAULT_TIMEFRAMES, TimeframeSpec, analyze_technical_timeframe, resolve_timeframes
from .fundamental import FundamentalValuationEngine
from .analyzer import ProfessionalStockAnalyzer
from .legacy import StockAnalyzer

__all__ = ["TimeframeSpec", "DEFAULT_TIMEFRAMES", "resolve_timeframes", "standardize_ohlcv", "assess_price_history", "fetch_yahoo_history", "compute_indicators", "analyze_technical_timeframe", "FundamentalValuationEngine", "ProfessionalStockAnalyzer", "StockAnalyzer"]
