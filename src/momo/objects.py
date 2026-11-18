from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .core.exceptions import MomoError, MomoValidationError
from .core.validation import _validate_ticker
from .core.units import find_nested_number
from .core.orchestration import sort_ranked_results


def _nested_number(value: Any, terms: Sequence[str]) -> Optional[float]:
    return find_nested_number(value, terms)


class _Asset:
    asset_type = ""

    def __init__(self, client: Any, ticker: str):
        self.client = client
        self.ticker = _validate_ticker(ticker)
        self._data: Optional[Dict[str, Any]] = None

    def get_data(self, dump_json: bool = False, dump_path: Optional[Path] = None) -> Dict[str, Any]:
        raise NotImplementedError

    def _market_data(self) -> Dict[str, Any]:
        try:
            import yfinance as yf
        except ImportError as exc:
            raise MomoError("Market quote data requires the optional yfinance dependency.") from exc
        ticker = yf.Ticker(self.ticker)
        try:
            info = ticker.get_info()
        except Exception as exc:
            raise MomoError(f"Unable to retrieve current market data for {self.ticker}.") from exc
        return info if isinstance(info, dict) else {}


class Stock(_Asset):
    """Fundamental stock analysis backed by the shared Momo client."""

    asset_type = "stock"

    def get_section(self, section: Optional[str] = None) -> Dict[str, Any]:
        if section is None:
            return self.get_data()
        return self.client.get_stock_section(self.ticker, section)

    def get_data(self, dump_json: bool = False, dump_path: Optional[Path] = None) -> Dict[str, Any]:
        data = self.client.get_stock_data(self.ticker, dump_json=False)
        try:
            info = self._market_data()
            data["marketData"] = {
                key: info.get(key)
                for key in ("currentPrice", "regularMarketPrice", "marketCap", "currency", "exchange", "quoteType")
                if info.get(key) is not None
            }
        except MomoError as exc:
            data.setdefault("errors", []).append({"section": "market_data", "error": str(exc)})
        self._data = data
        if dump_json:
            self.client.save_json(data, dump_path)
        return data

    def _section(self, name: str) -> Dict[str, Any]:
        return self.client.get_stock_section(self.ticker, name)

    def get_overview(self) -> Dict[str, Any]:
        return self._section("overview")

    def get_company(self) -> Dict[str, Any]:
        return self._section("company")

    def get_financials(self) -> Dict[str, Any]:
        return {
            "overview": self._section("financials"),
            "incomeStatement": self._section("income_statement"),
            "balanceSheet": self._section("balance_sheet"),
            "cashFlow": self._section("cash_flow"),
            "ratios": self._section("ratios"),
            "metrics": self._section("financial_metrics"),
        }

    def get_statistics(self) -> Dict[str, Any]:
        return self._section("statistics")

    def get_forecast(self) -> Dict[str, Any]:
        return self._section("forecast")

    def get_ratings(self) -> Dict[str, Any]:
        return self._section("ratings")

    def get_market_cap(self) -> Dict[str, Any]:
        return self._section("market_cap")

    def get_revenue(self) -> Dict[str, Any]:
        return self._section("revenue")

    def get_dividends(self) -> Dict[str, Any]:
        return self._section("dividends")

    def get_business_metrics(self) -> Dict[str, Any]:
        return self._section("business_metrics")

class ETF(_Asset):
    """First-class fund analysis; it does not expose corporate earnings metrics."""

    asset_type = "etf"

    def get_overview(self) -> Dict[str, Any]:
        return self.client.get_overview(self.ticker)

    def get_holdings(self) -> Dict[str, Any]:
        return self.client.get_holdings(self.ticker)

    def get_dividends(self) -> Dict[str, Any]:
        return self.client.get_dividends(self.ticker)

    def get_news(self) -> List[Dict[str, Any]]:
        return self.client.get_news(self.ticker)

    def get_data(self, dump_json: bool = False, dump_path: Optional[Path] = None) -> Dict[str, Any]:
        data = self.client.get_etf_data(self.ticker, dump_json=False)
        try:
            info = self._market_data()
            market_fields = {
                "expenseRatio": ("annualReportExpenseRatio", "netExpenseRatio"),
                "assetsUnderManagement": ("totalAssets",),
                "nav": ("navPrice",),
                "marketPrice": ("regularMarketPrice", "currentPrice"),
                "dividendYield": ("yield", "trailingAnnualDividendYield"),
                "fundName": ("longName", "shortName"),
                "inceptionDate": ("fundInceptionDate",),
            }
            market = {}
            for field, keys in market_fields.items():
                for key in keys:
                    if info.get(key) is not None:
                        market[field] = info[key]
                        break
            data["fundMetrics"] = market
        except MomoError as exc:
            data.setdefault("errors", []).append({"section": "fund_metrics", "error": str(exc)})
        self._data = data
        if dump_json:
            self.client.save_json(data, dump_path)
        return data


class _Batch:
    asset_class: type[_Asset]

    def __init__(self, client: Any, tickers: Sequence[str]):
        if not tickers:
            raise MomoValidationError("At least one ticker is required.")
        self.client = client
        self.tickers = [_validate_ticker(ticker) for ticker in tickers]
        self._results: Optional[List[Dict[str, Any]]] = None

    def get_data(self, dump_json: bool = False, dump_path: Optional[Path] = None) -> List[Dict[str, Any]]:
        results: Dict[str, Dict[str, Any]] = {}
        for ticker in self.tickers:
            path = Path(dump_path) / f"momo_{ticker.lower()}_{self.asset_class.asset_type}.json" if dump_path else None
            asset = self.asset_class(self.client, ticker)
            try:
                results[ticker] = asset.get_data(dump_json, path)
            except MomoError as exc:
                results[ticker] = {
                    "ticker": ticker,
                    "assetType": self.asset_class.asset_type,
                    "errors": [{"section": "all", "error": str(exc)}],
                }
        self._results = [results[ticker] for ticker in self.tickers]
        return self._results

    def compare(self) -> List[Dict[str, Any]]:
        return self._results if self._results is not None else self.get_data()

    def sort(self, metrics: Sequence[str], order: str = "descending") -> List[Dict[str, Any]]:
        direction = order.strip().lower()
        if direction not in {"ascending", "descending"}:
            raise MomoValidationError("order must be 'ascending' or 'descending'.")
        if not metrics:
            raise MomoValidationError("At least one metric is required for sorting.")
        results = self.compare()
        return sort_ranked_results(results, metrics, order=direction)

    def choice(self, strategy: str = "stable") -> Dict[str, Any]:
        results = self.compare()
        strategy = strategy.strip().lower()
        preferences = {
            "growth": ("revenuegrowth", "earningsgrowth", "epsgrowth"),
            "stable": ("totalassets", "assetsundermanagement", "dividendyield"),
            "dividend": ("dividendyield", "dividendpershare"),
            "value": ("pe", "expenseRatio"),
        }
        if strategy not in preferences:
            raise MomoValidationError("strategy must be one of: growth, stable, dividend, value.")
        ranked = [(item, _nested_number(item, preferences[strategy])) for item in results]
        available = [(item, score) for item, score in ranked if score is not None]
        if not available:
            raise MomoValidationError(f"No {strategy} metrics are available to rank these tickers.")
        if strategy == "value":
            positive = [(item, score) for item, score in available if score > 0]
            if not positive:
                raise MomoValidationError("No positive valuation or expense metric is available.")
            return min(positive, key=lambda pair: pair[1])[0]
        return max(available, key=lambda pair: pair[1])[0]


class Stocks(_Batch):
    asset_class = Stock


class ETFs(_Batch):
    asset_class = ETF
