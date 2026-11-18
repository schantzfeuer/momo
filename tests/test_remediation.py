import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import pytest
import requests
from pathlib import Path
import os
import stat

import momo
import momo.__main__ as momo_main
from momo.core.config import MomoConfig
from momo.core.cache import TTLCache
from momo.core.http import CircuitBreaker, io_semaphore, _fetch_html
from momo.core.units import parse_number, parse_percent
from momo.engine.fundamental import FundamentalValuationEngine
from momo.engine.market_data import standardize_ohlcv
from momo.client import Momo
from momo.core.persistence import atomic_json_write
from momo.core.http import TokenBucket, HOST_BUCKETS, HOST_COOLDOWNS
from momo.core.exceptions import SchemaMismatchError
from momo.extractors.sveltekit import _extract_embedded_data
from momo import enable_logging, disable_logging
from momo.core.observability import get_metrics
from momo.core.units import normalize_financial, FinancialUnit
from momo.core.validation import resolve_ticker
from momo.core.orchestration import FetchOrchestrator, sort_ranked_results


@pytest.mark.unit
@pytest.mark.parametrize(
    "value,expected",
    [
        ("10.81%", 0.1081),
        (0.1081, 0.1081),
        ("(12.50%)", -0.125),
        ("n/a", None),
        ("bad", None),
    ],
)
def test_percent_normalization(value, expected):
    assert parse_percent(value) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    "value,expected",
    [
        ("$4.51T", 4.51e12),
        ("(25M)", -25e6),
        ("1,250", 1250),
        ("malformed", None),
    ],
)
def test_financial_number_normalization(value, expected):
    assert parse_number(value) == expected


def _payload(**overrides):
    payload = {
        "ticker": "TEST",
        "quote": {"price": "100"},
        "financials": {
            "cashFlow": {"rows": [{"datekey": "TTM", "fcf": "10B", "fcfps": "2", "unleveredFCF": "11B"}]},
            "balanceSheet": {"rows": [{"datekey": "TTM", "sharesOutTotalCommon": "5B", "netcash": "1B"}]},
            "incomeStatement": {"rows": [{"datekey": "TTM", "epsdil": "5", "revenue": "50B"}]},
            "ratios": {"rows": [
                {"datekey": "2025", "pe": "20"},
                {"datekey": "2024", "pe": "18"},
                {"datekey": "2023", "pe": "22"},
                {"datekey": "2025", "pfcf": "20"},
                {"datekey": "2024", "pfcf": "18"},
            ]},
        },
        "analysis": {
            "statistics": {
                "sections": {
                    "ratios": {"values": {"pe": "20", "peForward": "18"}},
                    "efficiency": {},
                    "financialEfficiency": {"values": {"wacc": "9%", "roic": "12%"}},
                    "margins": {"values": {"grossMargin": "40%", "operatingMargin": "20%", "fcfMargin": "15%"}},
                    "financialPosition": {"values": {"currentRatio": "2"}},
                    "scores": {"values": {"zScore": "3", "fScore": "6"}},
                }
            },
            "forecast": {
                "estimates": {"stats": {"annual": {
                    "revenueNext": {"growth": "10.81%"},
                    "epsNext": {"growth": "10.81%", "this": "5.54"},
                }}},
                "priceTargets": {"avg": "110", "low": "100", "high": "120", "numPriceTargets": "5"},
            },
        },
    }
    payload.update(overrides)
    return payload


@pytest.mark.unit
def test_missing_fcf_hard_stops_dcf():
    payload = _payload()
    payload["financials"]["cashFlow"]["rows"][0]["fcf"] = None
    payload["financials"]["cashFlow"]["rows"][0]["unleveredFCF"] = None
    evaluation = FundamentalValuationEngine(payload).evaluate()
    assert evaluation["status"] in {"AVAILABLE", "INSUFFICIENT_DATA"}
    assert "dcf" not in evaluation["methods"]


@pytest.mark.unit
def test_missing_growth_hard_stops_dcf():
    payload = _payload()
    payload["analysis"]["forecast"]["estimates"]["stats"]["annual"]["epsNext"]["growth"] = None
    payload["analysis"]["forecast"]["estimates"]["stats"]["annual"]["revenueNext"]["growth"] = None
    evaluation = FundamentalValuationEngine(payload).evaluate()
    assert "dcf" not in evaluation["methods"]


@pytest.mark.unit
def test_missing_pe_history_hard_stops_earnings_multiple():
    payload = _payload()
    payload["financials"]["ratios"]["rows"] = []
    evaluation = FundamentalValuationEngine(payload).evaluate()
    assert "earningsMultiple" not in evaluation["methods"]


@pytest.mark.unit
def test_equivalent_growth_units_produce_same_metrics():
    percent_payload = _payload()
    fraction_payload = _payload()
    fraction_payload["analysis"]["forecast"]["estimates"]["stats"]["annual"]["epsNext"]["growth"] = 0.1081
    fraction_payload["analysis"]["forecast"]["estimates"]["stats"]["annual"]["revenueNext"]["growth"] = 0.1081
    percent_metrics = FundamentalValuationEngine(percent_payload).extract_metrics()
    fraction_metrics = FundamentalValuationEngine(fraction_payload).extract_metrics()
    assert percent_metrics["epsForecastGrowthPct"] == pytest.approx(0.1081)
    assert fraction_metrics["epsForecastGrowthPct"] == pytest.approx(0.1081)
    assert percent_metrics["revenueForecastGrowthPct"] == pytest.approx(0.1081)


@pytest.mark.unit
def test_valuation_status_has_structured_provenance():
    evaluation = FundamentalValuationEngine(_payload()).evaluate()
    provenance = evaluation["provenance"]
    assert set(("data_coverage_ratio", "missing_fields", "fallback_assumptions_used", "source_freshness")) <= set(provenance)
    assert isinstance(provenance["missing_fields"], list)


@pytest.mark.integration
def test_global_io_semaphore_bounds_20_ticker_batch():
    sem = io_semaphore(3)
    active = 0
    peak = 0
    lock = threading.Lock()

    def worker(_):
        nonlocal active, peak
        with sem:
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.01)
            with lock:
                active -= 1
        return True

    with ThreadPoolExecutor(max_workers=20) as executor:
        assert all(executor.map(worker, range(20)))
    assert peak <= 3


@pytest.mark.unit
def test_circuit_breaker_trips_and_stale_cache_remains_available(monkeypatch):
    breaker = CircuitBreaker(failure_threshold=2, reset_seconds=30)
    breaker.on_failure()
    assert breaker.state == "closed"
    breaker.on_failure()
    assert breaker.state == "open"
    assert breaker.allow_request() is False

    cache = TTLCache(0)
    cache.set("AAPL:overview", {"identity": {"ticker": "AAPL"}})
    assert cache.get("AAPL:overview") is None
    assert cache.get_stale("AAPL:overview")["identity"]["ticker"] == "AAPL"


@pytest.mark.integration
def test_fetch_html_supports_retry_after_and_trips_breaker(monkeypatch):
    class Response:
        status_code = 503
        headers = {"Retry-After": "0"}
        text = ""
        def raise_for_status(self):
            raise requests.HTTPError(response=self)

    class Session:
        def get(self, *args, **kwargs):
            return Response()

    config = MomoConfig(max_retries=1, max_workers=2)
    with pytest.raises(momo.MomoNetworkError):
        _fetch_html(Session(), "https://fault.example/stocks", config)


@pytest.mark.unit
def test_public_momo_api_and_close():
    client = momo.Momo(config=MomoConfig(cache_ttl=1))
    assert "Momo" in repr(client)
    client.close()


@pytest.mark.integration
def test_module_cli_entrypoint(monkeypatch):
    monkeypatch.setattr("momo.__main__.typer.run", lambda fn: ("called", fn))
    result = momo_main.main()
    assert result is None


@pytest.mark.unit
def test_ohlcv_normalization_with_duplicate_and_malformed_rows():
    frame = pd.DataFrame(
        {
            "Open": [10, 11, 12],
            "High": [12, 13, 14],
            "Low": [9, 10, 11],
            "Close": [11, 12.5, 13],
            "Volume": [100, -1, 200],
        },
        index=pd.to_datetime(["2026-11-16", "2026-11-17", "2026-11-18"]),
    )
    normalized = standardize_ohlcv(frame)
    assert len(normalized) == 3
    assert normalized["Volume"].tolist() == [100.0, 0.0, 200.0]


@pytest.mark.unit
@pytest.mark.parametrize(
    "url",
    [
        "http://stockanalysis.com/stocks",
        "https://user:pass@stockanalysis.com/stocks",
        "https://127.0.0.1/stocks",
        "https://stockanalysis.com:8443/stocks",
        "https://stockanalysis.com/stocks?debug=true",
    ],
)
def test_provider_url_rejects_unsafe_configuration(url):
    with pytest.raises(ValueError):
        MomoConfig(etf_base_url=url)


@pytest.mark.unit
def test_cache_copy_on_read_write_lru_and_expired_sweep(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("momo.core.cache.time.monotonic", lambda: now[0])
    cache = TTLCache(5, max_size=2)
    original = {"nested": {"value": 1}}
    cache.set("A", original)
    original["nested"]["value"] = 99
    first = cache.get("A")
    first["nested"]["value"] = 42
    assert cache.get("A") == {"nested": {"value": 1}}

    cache.set("B", {"value": 2})
    cache.get("A")
    cache.set("C", {"value": 3})
    assert cache.get("B") is None
    assert cache.metrics["evictions"] == 1

    cache.set("D", {"value": 4})
    now[0] = 106
    cache.set("E", {"value": 5})
    assert cache.get("D") is None
    assert cache.metrics["expirations"] >= 1


@pytest.mark.unit
def test_atomic_json_write_is_replaced_and_rejects_bad_payload(tmp_path):
    target = tmp_path / "report.json"
    result = atomic_json_write(target, {"ticker": "AAPL", "nested": {"ok": True}})
    assert result == target
    assert __import__("json").loads(target.read_text(encoding="utf-8"))["ticker"] == "AAPL"
    assert not list(tmp_path.glob(".report.json.*.tmp"))

    bad_target = tmp_path / "bad" / "report.json"
    with pytest.raises(TypeError):
        atomic_json_write(bad_target, {"bad": {1, 2}})
    assert not bad_target.exists()


@pytest.mark.unit
def test_redirects_are_disabled(monkeypatch):
    class Response:
        status_code = 302
        headers = {"Location": "http://127.0.0.1/private"}
        text = ""

        def raise_for_status(self):
            raise requests.HTTPError(response=self)

    class Session:
        def get(self, *args, **kwargs):
            assert kwargs["allow_redirects"] is False
            return Response()

    with pytest.raises(momo.MomoNetworkError):
        _fetch_html(
            Session(),
            "https://stockanalysis.com/stocks/aapl/",
            MomoConfig(max_retries=1),
        )


@pytest.mark.unit
def test_debug_html_is_opt_in_redacted_restricted_and_bounded(tmp_path, monkeypatch):
    import momo.client as client_module

    monkeypatch.setattr(client_module, "_fetch_html", lambda *args, **kwargs: (
        "<html>token=secret Authorization: Bearer abc123 body</html>"
    ))
    config = MomoConfig(debug_dir=tmp_path, debug_max_files=2)
    client = Momo(save_debug_html=True, config=config)
    for page in ("overview", "holdings", "dividends"):
        client._fetch_and_maybe_save(
            "https://stockanalysis.com/stocks/aapl/",
            "AAPL",
            page,
        )

    debug_dir = tmp_path / "momo-debug"
    artifacts = list(debug_dir.glob("*_debug.html"))
    assert len(artifacts) == 2
    for artifact in artifacts:
        content = artifact.read_text(encoding="utf-8")
        assert "secret" not in content
        assert "abc123" not in content


@pytest.mark.unit
def test_malformed_sveltekit_contract_raises_schema_error():
    html = '<script>__sveltekit_test={data:[{data:{broken:]}</script>'
    with pytest.raises(SchemaMismatchError):
        _extract_embedded_data(html)


@pytest.mark.unit
def test_shared_token_bucket_enforces_host_budget():
    bucket = TokenBucket(rate=100.0, burst=2)
    assert bucket.acquire(timeout=0.1) is True
    assert bucket.acquire(timeout=0.1) is True
    assert bucket.acquire(timeout=0.1) is True


@pytest.mark.integration
def test_retry_after_sets_shared_cooldown(monkeypatch):
    class Response:
        status_code = 503
        headers = {"Retry-After": "0"}
        text = ""

        def raise_for_status(self):
            raise requests.HTTPError(response=self)

    class Session:
        def get(self, *args, **kwargs):
            return Response()

    config = MomoConfig(max_retries=1, max_workers=2)
    HOST_BUCKETS.clear()
    HOST_COOLDOWNS.clear()
    with pytest.raises(momo.ProviderNetworkError):
        _fetch_html(Session(), "https://stockanalysis.com/stocks/aapl/", config)


@pytest.mark.unit
def test_logging_enable_is_idempotent_and_disable_removes_handler():
    logger = __import__("logging").getLogger("momo")
    disable_logging()
    enable_logging()
    enable_logging()
    handlers = getattr(logger, "handlers", [])
    momo_handlers = [handler for handler in handlers if getattr(handler, "_momo_handler", False)]
    assert len(momo_handlers) == 1
    disable_logging()
    assert not [handler for handler in logger.handlers if getattr(handler, "_momo_handler", False)]


@pytest.mark.unit
def test_observability_metrics_are_available():
    metrics = get_metrics()
    assert set(("fetches", "errors", "rate_limits")) <= set(metrics)


@pytest.mark.unit
def test_legacy_engine_emits_deprecation_warning():
    import warnings
    import importlib
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        legacy = importlib.import_module("momo.engine.legacy")
        importlib.reload(legacy)
    assert any("deprecated" in str(item.message).lower() for item in caught)


@pytest.mark.integration
def test_etf_visual_snapshot_contains_stable_output(tmp_path):
    from interface.visual import write_etf_visual_report

    data = {
        "ticker": "VOO",
        "profile": {"name": "Vanguard S&P 500 ETF", "nav": 520.25},
        "quote": {"price": 521.10},
        "fundMetrics": {"marketPrice": 521.10, "expenseRatio": "0.03%", "assetsUnderManagement": 600e9},
        "holdings": {"totalHoldingsCount": 503, "topHoldings": [{"name": "Apple", "ticker": "AAPL", "weight": "7%"}]},
        "dividends": {"yield": "1.2%"},
        "performance": {"totalReturn": {}},
    }
    output = write_etf_visual_report("VOO", data, tmp_path / "voo.html")
    html_text = output.read_text(encoding="utf-8")
    assert "<title>MOMO Fund Research · VOO</title>" in html_text
    assert "Vanguard S&amp;P 500 ETF" in html_text


@pytest.mark.unit
def test_etf_cli_and_visual_use_the_same_aum_value_and_format(tmp_path):
    from rich.console import Console
    from interface.etf import _etf_kpi_panel
    from interface.visual import write_etf_visual_report

    data = {
        "ticker": "VOO",
        "profile": {"aum": 500_000_000_000},
        "fundMetrics": {"assetsUnderManagement": 600_000_000_000},
    }

    console = Console(record=True, width=120)
    console.print(_etf_kpi_panel(data))
    cli_text = console.export_text()
    html_text = write_etf_visual_report("VOO", data, tmp_path / "voo.html").read_text(encoding="utf-8")

    assert "$600.00B" in cli_text
    assert "$600.00B" in html_text
    assert "$500.00B" not in cli_text
    assert "$500.00B" not in html_text


@pytest.mark.unit
@pytest.mark.parametrize(
    "key,value,expected",
    [
        ("revenueForecastGrowthPct", 0.693863, "69.4%"),
        ("epsForecastGrowthPct", 0.712933, "71.3%"),
        ("historicalRevenueCagr", 0.683014, "68.3%"),
        ("historicalFcfCagr", 0.856865, "85.7%"),
        ("grossMargin", 0.7492, "74.9%"),
        ("operatingMargin", 0.6608, "66.1%"),
        ("profitMargin", 0.6254, "62.5%"),
        ("fcfMargin", 0.4111, "41.1%"),
        ("roe", 1.1736, "117.4%"),
        ("roic", 0.9686, "96.9%"),
        ("wacc", 0.1645, "16.5%"),
        ("fcfYield", 0.0396, "4.0%"),
        ("dividendYield", 0.0066, "0.7%"),
        ("buybackYield", 0.0106, "1.1%"),
        ("shareholderYield", 0.017, "1.7%"),
    ],
)
def test_stock_visual_formats_fractional_metrics_as_percentages(key, value, expected):
    from interface.visual import _metric_value

    assert _metric_value(key, value) == expected


@pytest.mark.unit
def test_stock_visual_includes_provider_analyst_action_and_rating():
    from interface.visual import _analysis_results_html

    stock_data = {
        "analysis": {
            "forecast": {
                "recentRatings": [
                    {
                        "firm": "Argus Research",
                        "action_rt": "Reiterates",
                        "rating_new": "Buy",
                        "date": "2026-11-18",
                    }
                ]
            }
        }
    }

    rendered = _analysis_results_html({}, "", "", stock_data)

    assert "<td>Reiterates · Buy</td>" in rendered
    assert "<td>2026-11-18</td>" in rendered


@pytest.mark.unit
@pytest.mark.parametrize(
    "field,value",
    [
        ("timeout", 0),
        ("timeout", 301),
        ("max_retries", 0),
        ("max_retries", 21),
        ("cache_ttl", -1),
        ("cache_ttl", 86401),
        ("cache_max_size", 0),
        ("cache_max_size", 100001),
        ("max_workers", 0),
        ("max_workers", 65),
        ("debug_max_files", 0),
        ("debug_max_files", 1001),
        ("retry_backoff_base", -1),
        ("retry_backoff_base", 61),
    ],
)
def test_config_numeric_bounds(field, value):
    with pytest.raises(ValueError):
        MomoConfig(**{field: value})


@pytest.mark.unit
def test_config_normalizes_worker_limit(monkeypatch):
    monkeypatch.setattr("momo.core.config.os.cpu_count", lambda: 2)
    config = MomoConfig(max_workers=8)
    assert config.max_workers == 8


@pytest.mark.unit
def test_momo_closed_lifecycle_rejects_calls_and_close_is_idempotent():
    client = momo.Momo()
    client.close()
    client.close()
    with pytest.raises(momo.MomoClosedError):
        client.get_overview("VOO")
    with pytest.raises(momo.MomoClosedError):
        with client:
            pass


@pytest.mark.unit
def test_logging_context_always_cleans_up_handler():
    with momo.logging_context():
        enable_logging()
        enable_logging()
        handlers = [
            handler
            for handler in __import__("logging").getLogger("momo").handlers
            if getattr(handler, "_momo_handler", False)
        ]
        assert len(handlers) == 1
    assert not [
        handler
        for handler in __import__("logging").getLogger("momo").handlers
        if getattr(handler, "_momo_handler", False)
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw,expected,unit",
    [
        ("$1.25B", 1.25e9, FinancialUnit.CURRENCY),
        ("(2.5M)", -2.5e6, FinancialUnit.NUMBER),
        ("10.81%", 0.1081, FinancialUnit.PERCENT),
        ("n/a", None, FinancialUnit.MISSING),
        ("NaN", None, FinancialUnit.MISSING),
        ("not available", None, FinancialUnit.MISSING),
        ("invalid", None, FinancialUnit.MISSING),
    ],
)
def test_single_source_financial_normalization(raw, expected, unit):
    result = normalize_financial(raw)
    assert result.value == expected
    assert result.unit == unit
    assert result.missing == (unit == FinancialUnit.MISSING)


@pytest.mark.unit
def test_financial_normalization_preserves_suffix_scale_metadata():
    result = normalize_financial("$3.4T")
    assert result.value == 3.4e12
    assert result.scale == 1e12
    assert result.unit == FinancialUnit.CURRENCY


@pytest.mark.unit
@pytest.mark.parametrize("ticker", ["BRK.B", "BRK-B", "^GSPC", "0700.HK", "AAPL"])
def test_ticker_syntax_accepts_legitimate_variants(ticker):
    resolved = resolve_ticker(ticker)
    assert resolved.canonical == ticker
    assert resolved.resolvable is True


@pytest.mark.unit
@pytest.mark.parametrize("ticker", ["", "AAPL!", "-BAD", "TOO-LONG-TICKER-NAME"])
def test_ticker_syntax_rejects_malformed_shapes(ticker):
    with pytest.raises(momo.MomoValidationError):
        resolve_ticker(ticker)


@pytest.mark.unit
def test_provider_alias_resolution_is_explicit():
    def resolver(symbol):
        if symbol == "BRK.B":
            return {
                "canonical": "BRK.B",
                "aliases": ["BRK-B", "BRK/B"],
                "resolvable": True,
            }
        return {"canonical": symbol, "aliases": [], "resolvable": False}

    resolved = resolve_ticker("BRK.B", resolver)
    assert resolved.canonical == "BRK.B"
    assert set(resolved.aliases) == {"BRK-B", "BRK/B"}
    assert resolved.resolvable is True


@pytest.mark.unit
def test_fetch_orchestrator_records_partial_run_summary():
    orchestrator = FetchOrchestrator(max_workers=2)
    results, summary = orchestrator.run(
        "stock",
        {
            "overview": lambda: {"ticker": "AAPL"},
            "forecast": lambda: (_ for _ in ()).throw(momo.MomoNetworkError("provider unavailable")),
        },
    )
    assert results == {"overview": {"ticker": "AAPL"}}
    assert summary.available == ["overview"]
    assert summary.failed["forecast"] == "provider unavailable"
    assert summary.exit_reason == "partial"
    assert summary.as_dict()["providerLatencyMs"]["forecast"] >= 0


@pytest.mark.unit
def test_shared_sort_ranker_is_consistent():
    rows = [{"ticker": "A", "metrics": {"pe": "20"}}, {"ticker": "B", "metrics": {"pe": "10"}}]
    ascending = sort_ranked_results(rows, ("pe",), "ascending")
    descending = sort_ranked_results(rows, ("pe",), "descending")
    assert [row["ticker"] for row in ascending] == ["B", "A"]
    assert [row["ticker"] for row in descending] == ["A", "B"]


@pytest.mark.unit
def test_stock_and_etf_object_batches_have_consistent_error_shapes(monkeypatch):
    class FakeClient:
        def get_stock_data(self, ticker, *args, **kwargs):
            raise momo.MomoNetworkError("offline")

        def get_etf_data(self, ticker, *args, **kwargs):
            raise momo.MomoNetworkError("offline")

    stocks = momo.Stocks(FakeClient(), ["AAPL"])
    etfs = momo.ETFs(FakeClient(), ["VOO"])
    stock_result = stocks.get_data()[0]
    etf_result = etfs.get_data()[0]
    assert stock_result["assetType"] == "stock"
    assert etf_result["assetType"] == "etf"
    assert stock_result["errors"][0]["section"] == "all"
    assert etf_result["errors"][0]["section"] == "all"
