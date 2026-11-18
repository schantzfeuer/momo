from __future__ import annotations
import logging
import copy
import re
import time
from urllib.parse import urlsplit
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional, Sequence
import requests as req
from .core.config import logger
from . import __version__
from .assemblers.etf import assemble_etf_data
from .assemblers.stock import assemble_stock_data
from .core.config import MomoConfig
from enum import Enum
from .core.exceptions import MomoError, MomoValidationError, MomoClosedError
from .core.http import ManagedSession, _fetch_html
from .core.persistence import atomic_json_write
from .core.observability import StructuredFormatter, increment_metric
from .core.orchestration import FetchOrchestrator
from .core.validation import _validate_ticker, resolve_ticker
from .extractors.normalizers import _dedupe_news
from .parsers.etf import parse_dividend_html, parse_holdings_html, parse_overview_html
from .parsers.stocks.metrics import parse_stock_metric_detail_html
from .parsers.stocks.routes import STOCK_SECTION_ROUTES, parse_stock_section_html

if TYPE_CHECKING:
    from .objects import ETF, ETFs, Stock, Stocks

def enable_logging(level: int = logging.INFO) -> None:
    """Enable momo module logging output on the console (stderr).

    Useful when retrieved data does not match expectations: logs show which
    pages failed, when the HTML fallback was used, and similar details.

        import momo
        momo.enable_logging()
    """
    existing = getattr(logger, "_momo_console_handler", None)
    if existing is None:
        handler = logging.StreamHandler()
        handler.setFormatter(StructuredFormatter("[%(levelname)s] %(name)s: %(message)s"))
        handler._momo_handler = True
        logger.addHandler(handler)
        logger._momo_console_handler = handler
    logger.setLevel(level)


def disable_logging() -> None:
    handler = getattr(logger, "_momo_console_handler", None)
    if handler is not None:
        logger.removeHandler(handler)
        delattr(logger, "_momo_console_handler")

from .core.cache import TTLCache


class MomoLifecycle(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"

class Momo:
    """API for fetching comprehensive ETF data from
    stockanalysis.com.

        with Momo() as momo:
            overview  = momo.get_overview("VYM")
            holdings  = momo.get_holdings("VYM")
            dividends = momo.get_dividends("VYM")
            news      = momo.get_news("VYM")
            full      = momo.get_etf_data("VYM")           # combine everything
            table     = momo.compare(["VYM", "SCHD"])      # compare ETFs

    Customize through `MomoConfig`, or directly with keyword shorthand:

        momo = Momo(config=MomoConfig(timeout=30, max_retries=5))
        momo = Momo(timeout=30, max_retries=5)   # Equivalent, more concise
    """

    def __init__(
        self,
        save_debug_html: bool = False,
        config: Optional[MomoConfig] = None,
        **config_overrides: Any,
    ):
        if config is None:
            config = MomoConfig()
        if config_overrides:
            config = replace(config, **config_overrides)
        self.config = config
        self.save_debug_html = save_debug_html
        self._lifecycle = MomoLifecycle.OPEN
        self._session = ManagedSession(config)
        self._cache = TTLCache(self.config.cache_ttl, self.config.cache_max_size)
        self.run_summaries: List[Dict[str, Any]] = []
        self.provider_latencies_ms: Dict[str, float] = {}

    def __getattribute__(self, name: str) -> Any:
        if not name.startswith("_"):
            try:
                lifecycle = object.__getattribute__(self, "_lifecycle")
            except AttributeError:
                lifecycle = MomoLifecycle.OPEN
            if lifecycle == MomoLifecycle.CLOSED and name not in {"close", "__class__", "config"}:
                attribute = object.__getattribute__(self, name)
                if callable(attribute):
                    raise MomoClosedError("Momo has been closed and cannot perform operations.")
        return object.__getattribute__(self, name)

    # -- context manager (ensures the HTTP connection is closed cleanly) ---------

    def __enter__(self) -> "Momo":
        if self._lifecycle == MomoLifecycle.CLOSED:
            raise MomoClosedError("Momo has been closed and cannot be reopened.")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def close(self) -> None:
        """Close the HTTP session and its connection pool."""
        if self._lifecycle == MomoLifecycle.OPEN:
            self._session.close()
            self._lifecycle = MomoLifecycle.CLOSED

    def __repr__(self) -> str:
        return (
            f"Momo(etf_base_url={self.config.etf_base_url!r}, "
            f"stock_base_url={self.config.stock_base_url!r}, "
            f"use_cache={self.config.use_cache}, max_retries={self.config.max_retries})"
        )

    # -- simple in-memory cache (TTL) ---------------------------------------------

    def _cache_get(self, key: str) -> Optional[Dict[str, Any]]:
        if not self.config.use_cache:
            return None
        data = self._cache.get(key)
        if data is not None:
            logger.debug("Cache hit: %s", key)
        return data

    def _cache_set(self, key: str, data: Dict[str, Any]) -> None:
        if self.config.use_cache:
            self._cache.set(key, data)

    def clear_cache(self) -> None:
        """Clear this instance's entire in-memory cache."""
        self._cache.clear()

    def resolve_symbol(self, ticker: str):
        """Resolve syntax separately from provider canonicalization."""
        return resolve_ticker(ticker, self.config.ticker_resolver)

    def Stock(self, ticker: str) -> Stock:
        from .objects import Stock
        return Stock(self, ticker)

    def Stocks(self, *tickers: str) -> Stocks:
        from .objects import Stocks
        return Stocks(self, tickers)

    def ETF(self, ticker: str) -> ETF:
        from .objects import ETF
        return ETF(self, ticker)

    def ETFs(self, *tickers: str) -> ETFs:
        from .objects import ETFs
        return ETFs(self, tickers)

    def Technical(
        self,
        ticker: str,
        price_data: Any = None,
        period: str = "10y",
        interval: str = "1d",
        auto_fetch_price: bool = True,
        timeframes: Optional[Sequence[Any]] = None,
    ) -> Dict[str, Any]:
        """Analyze a stock or ETF using prices only, independently of fundamentals."""
        ticker = _validate_ticker(ticker)
        try:
            from .engine.market_data import fetch_yahoo_history, standardize_ohlcv
            from .engine.technical import analyze_technical_timeframe, resolve_timeframes
        except ImportError as exc:
            raise MomoError("Technical analysis requires the analysis dependencies, including TA-Lib.") from exc
        price_error = None
        if price_data is None and auto_fetch_price:
            try:
                price_data = fetch_yahoo_history(ticker, period=period, interval=interval)
            except Exception:
                price_error = "Historical price data could not be retrieved."
        if price_data is None:
            return {"ticker": ticker, "assetType": "technical", "available": False,
                    "reason": price_error or "Historical OHLCV is unavailable", "timeframes": {}}
        frame = standardize_ohlcv(price_data)
        if frame.empty:
            raise MomoValidationError("price_data contains no usable OHLCV rows.")
        specs = resolve_timeframes(timeframes)
        try:
            analyses = {spec.key: analyze_technical_timeframe(frame, spec) for spec in specs}
        except ImportError as exc:
            raise MomoError("Technical analysis requires TA-Lib; install the analysis extra.") from exc
        return {
            "ticker": ticker,
            "assetType": "technical",
            "available": True,
            "dataPoints": len(frame),
            "from": str(frame.index[0]),
            "to": str(frame.index[-1]),
            "timeframes": analyses,
        }

    # -- fetch + parse each page ---------------------------------------------------

    def _fetch_and_maybe_save(self, url: str, ticker: str, page: str, cache_key: Optional[str] = None) -> str:
        started = time.monotonic()
        try:
            html = _fetch_html(self._session, url, self.config)
        except MomoError:
            stale = self._cache.get_stale(cache_key) if cache_key else None
            if stale is not None:
                raise
            raise
        finally:
            elapsed = (time.monotonic() - started) * 1000.0
            provider = urlsplit(url).hostname or "unknown"
            self.provider_latencies_ms.setdefault(provider, elapsed)
            if elapsed > self.provider_latencies_ms[provider]:
                self.provider_latencies_ms[provider] = elapsed
        if self.save_debug_html:
            debug_dir = (self.config.debug_dir or Path.cwd()) / "momo-debug"
            debug_dir.mkdir(parents=True, exist_ok=True)
            try:
                debug_dir.chmod(0o700)
            except OSError:
                pass
            debug_path = debug_dir / f"{ticker.lower()}_{page}_debug.html"
            redacted = re.sub(
                r"(?i)\b((?:authorization|cookie|set-cookie|bearer|token|api[_-]?key)\b\s*[=:]\s*)([^\s<,&;]+)",
                r"\1[REDACTED]",
                html,
            )
            redacted = re.sub(
                r"(?i)(bearer\s+)[A-Za-z0-9._~-]+",
                r"\1[REDACTED]",
                redacted,
            )
            redacted = re.sub(
                r"(?i)\b(?:authorization|proxy-authorization)\s*:\s*[^\s<]+(?:\s+[A-Za-z0-9._~-]+)?",
                "[REDACTED]",
                redacted,
            )
            debug_path.write_text(redacted, encoding="utf-8")
            try:
                debug_path.chmod(0o600)
            except OSError:
                pass
            artifacts = sorted(debug_dir.glob("*_debug.html"), key=lambda item: item.stat().st_mtime)
            for old_file in artifacts[:max(0, len(artifacts) - self.config.debug_max_files)]:
                try:
                    old_file.unlink()
                except OSError:
                    pass
            logger.debug("HTML debug saved to: %s", debug_path)
        return html

    def get_overview(self, ticker: str) -> Dict[str, Any]:
        ticker = _validate_ticker(ticker)
        cache_key = f"{ticker}:overview"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        url = f"{self.config.etf_base_url.rstrip('/')}/{ticker.lower()}/"
        try:
            html = self._fetch_and_maybe_save(url, ticker, "overview", cache_key)
        except MomoError as exc:
            stale = self._cache.get_stale(cache_key)
            if stale is not None:
                fallback = copy.deepcopy(stale)
                fallback["_stale_fallback"] = True
                fallback["_stale_reason"] = str(exc)
                return fallback
            raise
        data = parse_overview_html(html)
        self._cache_set(cache_key, data)
        return data

    def get_holdings(self, ticker: str) -> Dict[str, Any]:
        ticker = _validate_ticker(ticker)
        cache_key = f"{ticker}:holdings"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        url = f"{self.config.etf_base_url.rstrip('/')}/{ticker.lower()}/holdings/"
        try:
            html = self._fetch_and_maybe_save(url, ticker, "holdings", cache_key)
        except MomoError as exc:
            stale = self._cache.get_stale(cache_key)
            if stale is not None:
                fallback = copy.deepcopy(stale)
                fallback["_stale_fallback"] = True
                fallback["_stale_reason"] = str(exc)
                return fallback
            raise
        data = parse_holdings_html(html)
        self._cache_set(cache_key, data)
        return data

    def get_dividends(self, ticker: str) -> Dict[str, Any]:
        ticker = _validate_ticker(ticker)
        cache_key = f"{ticker}:dividends"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        url = f"{self.config.etf_base_url.rstrip('/')}/{ticker.lower()}/dividend/"
        try:
            html = self._fetch_and_maybe_save(url, ticker, "dividend", cache_key)
        except MomoError as exc:
            stale = self._cache.get_stale(cache_key)
            if stale is not None:
                fallback = copy.deepcopy(stale)
                fallback["_stale_fallback"] = True
                fallback["_stale_reason"] = str(exc)
                return fallback
            raise
        data = parse_dividend_html(html)
        self._cache_set(cache_key, data)
        return data

    def get_news(
        self,
        ticker: str,
        overview: Optional[Dict[str, Any]] = None,
        holdings: Optional[Dict[str, Any]] = None,
        dividends: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Merge and deduplicate news from overview/holdings/dividend. If any
        source has not been fetched, its argument may be None and this function
        fetches it itself."""
        ticker = _validate_ticker(ticker)
        overview = overview if overview is not None else self.get_overview(ticker)
        holdings = holdings if holdings is not None else self.get_holdings(ticker)
        dividends = dividends if dividends is not None else self.get_dividends(ticker)
        return _dedupe_news(
            overview.get("news") or [],
            holdings.get("news") or [],
            dividends.get("news") or [],
        )

    # -- main function: comprehensive data for one ticker -------------------------

    def get_etf_data(
        self,
        ticker: str,
        dump_json: bool = False,
        dump_path: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """Build comprehensive ETF JSON with overview, performance, holdings,
        dividends, and combined news.

        All three pages are fetched in parallel to speed up the process. Partial
        data and an error list are returned when a page fails rather than failing
        the entire operation.

        If dump_json=True, data is written to a JSON file through `save_json`.
        """
        ticker = _validate_ticker(ticker)
        errors: List[Dict[str, str]] = []
        results: Dict[str, Dict[str, Any]] = {"overview": {}, "holdings": {}, "dividends": {}}
        fetchers = {
            "overview": self.get_overview,
            "holdings": self.get_holdings,
            "dividends": self.get_dividends,
        }
        orchestrator = FetchOrchestrator(self.config.max_workers)
        section_results, summary = orchestrator.run(
            "etf",
            {name: lambda fn=fn, ticker=ticker: fn(ticker) for name, fn in fetchers.items()},
        )
        results.update(section_results)
        for name, reason in summary.failed.items():
            section = "dividend" if name == "dividends" else name
            logger.warning("Failed to fetch %s for %s: %s", section, ticker, reason)
            errors.append({"section": section, "error": reason})
        summary_dict = summary.as_dict()
        summary_dict["ticker"] = ticker
        summary_dict["providerLatencyMs"] = dict(self.provider_latencies_ms)
        self.run_summaries.append(summary_dict)

        data = assemble_etf_data(
            ticker,
            overview=results["overview"],
            holdings=results["holdings"],
            dividends=results["dividends"],
            errors=errors or None,
        )

        if dump_json:
            self.save_json(data, dump_path)

        return data

    def get_data(
        self,
        ticker: str,
        asset_type: str = "etf",
        sections: Optional[Sequence[str]] = None,
        dump_json: bool = False,
        dump_path: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """Unified entry point for ETFs and stocks.

        `asset_type` must be `"etf"` or `"stock"`. For stocks, `sections`
        can limit the endpoints that are fetched.
        """
        kind = asset_type.strip().lower()
        if kind == "etf":
            if sections is not None:
                raise MomoValidationError("The sections parameter only applies to asset_type='stock'.")
            return self.get_etf_data(ticker, dump_json=dump_json, dump_path=dump_path)
        if kind == "stock":
            return self.get_stock_data(
                ticker, sections=sections, dump_json=dump_json, dump_path=dump_path
            )
        raise MomoValidationError("asset_type must be 'etf' or 'stock'.")

    # -- STOCK API -------------------------------------------------------------------

    def get_stock_section(self, ticker: str, section: str) -> Dict[str, Any]:
        """Fetch and parse one stock section using `STOCK_SECTION_ROUTES`."""
        ticker = _validate_ticker(ticker)
        section = section.strip().lower()
        route = STOCK_SECTION_ROUTES.get(section)
        if route is None:
            raise MomoValidationError(
                f"Unknown stock section: {section!r}. Choices: {', '.join(STOCK_SECTION_ROUTES)}"
            )
        return self._get_stock_section_by_route(ticker, section, route)

    def _get_stock_section_by_route(
        self,
        ticker: str,
        section: str,
        route: str,
        metric_detail: bool = False,
    ) -> Dict[str, Any]:
        cache_key = f"stock:{ticker}:{section}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        url = f"{self.config.stock_base_url.rstrip('/')}/{ticker.lower()}/" + route
        try:
            html = self._fetch_and_maybe_save(url, ticker, f"stock_{section}", cache_key)
        except MomoError as exc:
            stale = self._cache.get_stale(cache_key)
            if stale is not None:
                fallback = copy.deepcopy(stale)
                trust = dict(fallback.get("trust") or {})
                trust["staleFallback"] = True
                trust["upstreamError"] = str(exc)
                fallback["trust"] = trust
                fallback["_stale_fallback"] = True
                return fallback
            raise
        data = (
            parse_stock_metric_detail_html(html)
            if metric_detail
            else parse_stock_section_html(section, html)
        )
        data.setdefault("_section", section)
        if metric_detail:
            data["_metric_detail"] = True
        self._cache_set(cache_key, data)
        return data

    def get_stock_overview(self, ticker: str) -> Dict[str, Any]:
        return self.get_stock_section(ticker, "overview")

    def get_stock_company(self, ticker: str) -> Dict[str, Any]:
        return self.get_stock_section(ticker, "company")

    def get_stock_financials(self, ticker: str) -> Dict[str, Any]:
        return self.get_stock_section(ticker, "financials")

    def get_stock_statistics(self, ticker: str) -> Dict[str, Any]:
        return self.get_stock_section(ticker, "statistics")

    def get_stock_forecast(self, ticker: str) -> Dict[str, Any]:
        return self.get_stock_section(ticker, "forecast")

    def get_stock_ratings(self, ticker: str) -> Dict[str, Any]:
        return self.get_stock_section(ticker, "ratings")

    def get_stock_market_cap(self, ticker: str) -> Dict[str, Any]:
        return self.get_stock_section(ticker, "market_cap")

    def get_stock_revenue(self, ticker: str) -> Dict[str, Any]:
        return self.get_stock_section(ticker, "revenue")

    def get_stock_dividends(self, ticker: str) -> Dict[str, Any]:
        return self.get_stock_section(ticker, "dividends")

    def get_stock_business_metrics(self, ticker: str) -> Dict[str, Any]:
        return self.get_stock_section(ticker, "business_metrics")

    def get_stock_data(
        self,
        ticker: str,
        sections: Optional[Sequence[str]] = None,
        dump_json: bool = False,
        dump_path: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """Fetch comprehensive stock data in parallel.

        By default, fetches every standard endpoint and discovers the available
        metric detail pages from the stock's metrics navigation.
        Use `sections=[...]` for a lightweight mode.
        """
        ticker = _validate_ticker(ticker)
        discover_metric_pages = sections is None
        metric_detail_sections = {
            section
            for section, route in STOCK_SECTION_ROUTES.items()
            if route.startswith("metrics/") and section != "business_metrics"
        }
        requested = (
            [section for section in STOCK_SECTION_ROUTES if section not in metric_detail_sections]
            if discover_metric_pages
            else list(dict.fromkeys(sections))
        )
        if not requested:
            return assemble_stock_data(ticker, {})
        unknown = [section for section in requested if section not in STOCK_SECTION_ROUTES]
        if unknown:
            raise MomoValidationError(
                f"Unknown stock section: {', '.join(unknown)}. "
                f"Choices: {', '.join(STOCK_SECTION_ROUTES)}"
            )

        results: Dict[str, Dict[str, Any]] = {}
        errors: List[Dict[str, str]] = []
        orchestrator = FetchOrchestrator(self.config.max_workers)
        section_results, summary = orchestrator.run(
            "stock",
            {
                section: lambda section=section: self.get_stock_section(ticker, section)
                for section in requested
            },
        )
        results.update(section_results)
        for section, reason in summary.failed.items():
            logger.warning("Failed to fetch stock section %s for %s: %s", section, ticker, reason)
            errors.append({"section": section, "error": reason})

        if discover_metric_pages:
            metrics = results.get("business_metrics") or {}
            metric_pages = metrics.get("metricPages") or []
            route_sections: Dict[str, str] = {}
            for page in metric_pages:
                route = page.get("route")
                if not isinstance(route, str) or not route.startswith("metrics/"):
                    continue
                section = next(
                    (
                        name
                        for name, known_route in STOCK_SECTION_ROUTES.items()
                        if known_route == route
                    ),
                    route.rstrip("/").rsplit("/", 1)[-1].replace("-", "_"),
                )
                route_sections.setdefault(section, route)

            requested.extend(route_sections)
            metric_results, metric_summary = orchestrator.run(
                "stock",
                {
                    section: lambda section=section, route=route: self._get_stock_section_by_route(
                        ticker, section, route, True
                    )
                    for section, route in route_sections.items()
                },
            )
            results.update(metric_results)
            summary.attempted.extend(metric_summary.attempted)
            summary.available.extend(metric_summary.available)
            summary.failed.update(metric_summary.failed)
            summary.provider_latency_ms.update(metric_summary.provider_latency_ms)
            for section, reason in metric_summary.failed.items():
                logger.warning("Failed to fetch stock section %s for %s: %s", section, ticker, reason)
                errors.append({"section": section, "error": reason})

        data = assemble_stock_data(ticker, results, errors or None)
        available = sorted(results.keys())
        data["_meta"]["sectionsRequested"] = requested
        data["_meta"]["sectionsAvailable"] = available
        data["_meta"]["sectionCoveragePct"] = round(len(available) / max(len(requested), 1) * 100.0, 1)
        data["_meta"]["sourceErrorCount"] = len(errors)
        summary_dict = summary.as_dict()
        summary_dict["ticker"] = ticker
        summary_dict["exitReason"] = "partial" if errors else "ok"
        summary_dict["available"] = available
        summary_dict["attempted"] = requested
        summary_dict["providerLatencyMs"] = dict(self.provider_latencies_ms)
        self.run_summaries.append(summary_dict)
        if dump_json:
            self.save_json(data, dump_path)
        return data

    def analyze_stock_data(
        self,
        stock_data: Mapping[str, Any],
        price_data: Any = None,
        period: str = "10y",
        interval: str = "1d",
        auto_fetch_price: bool = True,
        timeframes: Optional[Sequence[Any]] = None,
    ) -> Dict[str, Any]:
        """Run the professional analysis engine on a Momo stock payload.

        Import ``technical`` is lazy so Momo parsers/fetchers remain usable
        without analytical dependencies (numpy/pandas/yfinance).
        ``price_data`` may be an OHLCV DataFrame; when omitted with
        ``auto_fetch_price=True``, the engine attempts to fetch historical prices.
        """
        if not isinstance(stock_data, Mapping):
            raise MomoValidationError("stock_data must be a mapping/dict returned by get_stock_data().")
        ticker = _validate_ticker(str(stock_data.get("ticker") or ""))
        try:
            from .engine.analyzer import ProfessionalStockAnalyzer
        except ImportError as exc:
            raise MomoError(
                "The stock analysis engine is unavailable. Ensure technical.py is "
                "in the project directory and numpy/pandas are installed."
            ) from exc

        analyzer = ProfessionalStockAnalyzer(
            ticker=ticker,
            price_data=price_data,
            fundamental_data=stock_data,
            period=period,
            interval=interval,
            auto_fetch=auto_fetch_price,
        )
        result = analyzer.analyze(timeframes=timeframes)
        meta = stock_data.get("_meta") or {}
        requested = meta.get("sectionsRequested") or []
        available = meta.get("sectionsAvailable") or []
        coverage = meta.get("sectionCoveragePct")
        if coverage is None and requested:
            coverage = round(len(available) / max(len(requested), 1) * 100.0, 1)
        result["dataContext"] = {
            "source": meta.get("source") or "stockanalysis.com",
            "generatedAt": stock_data.get("generatedAt"),
            "sectionsAvailable": available,
            "sectionsRequested": requested,
            "sectionCoveragePct": coverage,
            "sourceErrors": stock_data.get("errors") or [],
            "sourceErrorCount": len(stock_data.get("errors") or []),
            "parserVersion": __version__,
        }
        return result

    def analyze_stock(
        self,
        ticker: str,
        sections: Optional[Sequence[str]] = None,
        price_data: Any = None,
        period: str = "10y",
        interval: str = "1d",
        auto_fetch_price: bool = True,
        timeframes: Optional[Sequence[Any]] = None,
    ) -> Dict[str, Any]:
        """Fetch fundamental stock data, then run the technical and valuation engine."""
        stock_data = self.get_stock_data(ticker, sections=sections)
        return self.analyze_stock_data(
            stock_data,
            price_data=price_data,
            period=period,
            interval=interval,
            auto_fetch_price=auto_fetch_price,
            timeframes=timeframes,
        )

    def compare_stocks(
        self,
        tickers: Sequence[str],
        sections: Optional[Sequence[str]] = None,
        dump_json: bool = False,
        dump_folder: Optional[Path] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch multiple stocks in parallel using the `get_stock_data` schema."""
        if not tickers:
            return []
        normalized = [_validate_ticker(t) for t in tickers]
        results: Dict[str, Dict[str, Any]] = {}
        for ticker in normalized:
            dump_path = (dump_folder / f"momo_{ticker.lower()}_stock.json") if dump_folder else None
            try:
                results[ticker] = self.get_stock_data(
                    ticker, sections, dump_json, dump_path
                )
            except MomoError as exc:
                results[ticker] = {
                    "ticker": ticker,
                    "assetType": "stock",
                    "errors": [{"section": "all", "error": str(exc)}],
                }
        return [results[ticker] for ticker in normalized]

    # -- compare multiple tickers ---------------------------------------------------

    def compare(
        self,
        tickers: List[str],
        dump_json: bool = False,
        dump_folder: Optional[Path] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch comprehensive data for multiple tickers in parallel. Tickers
        that fail completely remain in the result with an error list, and the
        result order follows the supplied `tickers` order.
        """
        if not tickers:
            return []
        results: Dict[str, Dict[str, Any]] = {}
        for t in tickers:
            dump_path = (dump_folder / f"momo_{t.strip().lower()}_data.json") if dump_folder else None
            try:
                results[t] = self.get_etf_data(t, dump_json, dump_path)
            except MomoError as e:
                logger.error("Failed to fetch all data for %s: %s", t, e)
                results[t] = {
                    "ticker": t.strip().upper(),
                    "errors": [{"section": "all", "error": str(e)}],
                }
        return [results[t] for t in tickers if t in results]

    # -- JSON export -----------------------------------------------------------------

    def save_json(self, data: Dict[str, Any], dump_path: Optional[Path] = None) -> Path:
        """Save ETF/stock data to a JSON file and return its path."""
        output_path = Path(dump_path) if dump_path else Path(f"momo_{data.get('ticker', 'unknown').lower()}_data.json")
        result = atomic_json_write(output_path, data)
        logger.info("Data saved to %s", result)
        return result

    # Legacy alias retained so older code calling this private name continues
    # to work without changes.
    _dump_json_data = save_json
