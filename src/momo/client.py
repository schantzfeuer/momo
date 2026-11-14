from __future__ import annotations
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence
import requests as req
from .core.config import logger
from . import __version__
from .assemblers.etf import assemble_etf_data
from .assemblers.stock import assemble_stock_data
from .core.config import MomoConfig
from .core.exceptions import MomoError, MomoValidationError
from .core.http import _fetch_html
from .core.validation import _validate_ticker
from .extractors.normalizers import _dedupe_news
from .parsers.etf import parse_dividend_html, parse_holdings_html, parse_overview_html
from .parsers.stocks.metrics import parse_stock_metric_detail_html
from .parsers.stocks.routes import STOCK_SECTION_ROUTES, parse_stock_section_html

def enable_logging(level: int = logging.INFO) -> None:
    """Enable momo module logging output on the console (stderr).

    Useful when retrieved data does not match expectations: logs show which
    pages failed, when the HTML fallback was used, and similar details.

        import momo
        momo.enable_logging()
    """
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(level)

from .core.cache import TTLCache

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
        self._session = req.Session()
        self._session.headers.update(self.config.headers)
        self._cache = TTLCache(self.config.cache_ttl)

    # -- context manager (ensures the HTTP connection is closed cleanly) ---------

    def __enter__(self) -> "Momo":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def close(self) -> None:
        """Close the HTTP session and its connection pool."""
        self._session.close()

    def __repr__(self) -> str:
        return (
            f"Momo(etf_base_url={self.config.base_url!r}, "
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

    # -- fetch + parse each page ---------------------------------------------------

    def _fetch_and_maybe_save(self, url: str, ticker: str, page: str) -> str:
        html = _fetch_html(self._session, url, self.config)
        if self.save_debug_html:
            debug_dir = self.config.debug_dir or Path.cwd()
            debug_dir.mkdir(parents=True, exist_ok=True)
            debug_path = debug_dir / f"{ticker.lower()}_{page}_debug.html"
            debug_path.write_text(html, encoding="utf-8")
            logger.debug("HTML debug saved to: %s", debug_path)
        return html

    def get_overview(self, ticker: str) -> Dict[str, Any]:
        ticker = _validate_ticker(ticker)
        cache_key = f"{ticker}:overview"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        url = f"{self.config.base_url}/{ticker.lower()}/"
        html = self._fetch_and_maybe_save(url, ticker, "overview")
        data = parse_overview_html(html)
        self._cache_set(cache_key, data)
        return data

    def get_holdings(self, ticker: str) -> Dict[str, Any]:
        ticker = _validate_ticker(ticker)
        cache_key = f"{ticker}:holdings"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        url = f"{self.config.base_url}/{ticker.lower()}/holdings/"
        html = self._fetch_and_maybe_save(url, ticker, "holdings")
        data = parse_holdings_html(html)
        self._cache_set(cache_key, data)
        return data

    def get_dividends(self, ticker: str) -> Dict[str, Any]:
        ticker = _validate_ticker(ticker)
        cache_key = f"{ticker}:dividends"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        url = f"{self.config.base_url}/{ticker.lower()}/dividend/"
        html = self._fetch_and_maybe_save(url, ticker, "dividend")
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

        with ThreadPoolExecutor(max_workers=3) as executor:
            future_map = {executor.submit(fn, ticker): name for name, fn in fetchers.items()}
            for future in as_completed(future_map):
                name = future_map[future]
                section = "dividend" if name == "dividends" else name
                try:
                    results[name] = future.result()
                except MomoError as e:
                    logger.warning("Failed to fetch %s for %s: %s", section, ticker, e)
                    errors.append({"section": section, "error": str(e)})
                except Exception as e:  # Unexpected parsing bugs must not fail the entire batch.
                    logger.exception("Unexpected error while fetching %s for %s", section, ticker)
                    errors.append({"section": section, "error": f"Unexpected error: {e}"})

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
        html = self._fetch_and_maybe_save(url, ticker, f"stock_{section}")
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
        worker_count = max(1, min(len(requested), self.config.max_workers))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_map = {
                executor.submit(self.get_stock_section, ticker, section): section
                for section in requested
            }
            for future in as_completed(future_map):
                section = future_map[future]
                try:
                    results[section] = future.result()
                except MomoError as exc:
                    logger.warning("Failed to fetch stock section %s for %s: %s", section, ticker, exc)
                    errors.append({"section": section, "error": str(exc)})
                except Exception as exc:
                    logger.exception("Unexpected error in stock section %s for %s", section, ticker)
                    errors.append({"section": section, "error": f"Unexpected error: {exc}"})

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
            with ThreadPoolExecutor(
                max_workers=max(1, min(len(route_sections), self.config.max_workers))
            ) as executor:
                future_map = {
                    executor.submit(
                        self._get_stock_section_by_route,
                        ticker,
                        section,
                        route,
                        True,
                    ): section
                    for section, route in route_sections.items()
                }
                for future in as_completed(future_map):
                    section = future_map[future]
                    try:
                        results[section] = future.result()
                    except MomoError as exc:
                        logger.warning("Failed to fetch stock section %s for %s: %s", section, ticker, exc)
                        errors.append({"section": section, "error": str(exc)})
                    except Exception as exc:
                        logger.exception("Unexpected error in stock section %s for %s", section, ticker)
                        errors.append({"section": section, "error": f"Unexpected error: {exc}"})

        data = assemble_stock_data(ticker, results, errors or None)
        available = sorted(results.keys())
        data["_meta"]["sectionsRequested"] = requested
        data["_meta"]["sectionsAvailable"] = available
        data["_meta"]["sectionCoveragePct"] = round(len(available) / max(len(requested), 1) * 100.0, 1)
        data["_meta"]["sourceErrorCount"] = len(errors)
        if dump_json:
            self.save_json(data, dump_path)
        return data

    def analyze_stock_data(
        self,
        stock_data: Mapping[str, Any],
        price_data: Any = None,
        period: str = "5y",
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
        period: str = "5y",
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
        outer_workers = max(1, min(len(normalized), 4))
        with ThreadPoolExecutor(max_workers=outer_workers) as executor:
            future_map = {}
            for ticker in normalized:
                dump_path = (dump_folder / f"momo_{ticker.lower()}_stock.json") if dump_folder else None
                future = executor.submit(
                    self.get_stock_data, ticker, sections, dump_json, dump_path
                )
                future_map[future] = ticker
            for future in as_completed(future_map):
                ticker = future_map[future]
                try:
                    results[ticker] = future.result()
                except Exception as exc:
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
        with ThreadPoolExecutor(max_workers=min(len(tickers), 4)) as executor:
            future_map = {}
            for t in tickers:
                dump_path = (dump_folder / f"momo_{t.strip().lower()}_data.json") if dump_folder else None
                future_map[executor.submit(self.get_etf_data, t, dump_json, dump_path)] = t
            for future in as_completed(future_map):
                t = future_map[future]
                try:
                    results[t] = future.result()
                except Exception as e:
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
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.info("Data saved to %s", output_path)
        return output_path

    # Legacy alias retained so older code calling this private name continues
    # to work without changes.
    _dump_json_data = save_json
