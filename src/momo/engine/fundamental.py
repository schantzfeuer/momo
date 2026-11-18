from __future__ import annotations
from datetime import datetime, timezone
from math import isfinite, sqrt
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
import numpy as np
from .market_data import _clip, _py
from ..core.units import first_number, parse_number, parse_percent, normalize_financial

def _to_number(value: Any, percent_as_fraction: bool = False) -> Optional[float]:
    return normalize_financial(value, percent=percent_as_fraction).value

def _first_number(*values: Any, percent_as_fraction: bool = False) -> Optional[float]:
    for value in values:
        parsed = _to_number(value, percent_as_fraction=percent_as_fraction)
        if parsed is not None:
            return parsed
    return None

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

def _confidence_label(score: Optional[float]) -> str:
    if score is None:
        return "Unavailable"
    if score >= 80:
        return "High"
    if score >= 60:
        return "Moderate"
    if score >= 40:
        return "Low"
    return "Very Low"

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

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
        revenue_forecast = _first_number((est_stats.get("revenueNext") or {}).get("growth"), percent_as_fraction=True)
        eps_forecast = _first_number((est_stats.get("epsNext") or {}).get("growth"), percent_as_fraction=True)
        if revenue_forecast is None:
            revenue_forecast = _first_number(analysts.get("revenue3y"), percent_as_fraction=True)
        if eps_forecast is None:
            eps_forecast = _first_number(analysts.get("eps3y"), percent_as_fraction=True)

        annual_income = self._annual_rows("incomeStatement")
        annual_cash = self._annual_rows("cashFlow")
        rev_vals = [parse_number(r.get("revenue")) for r in annual_income[-5:] if parse_number(r.get("revenue")) is not None]
        fcf_vals = [parse_number(r.get("fcf")) for r in annual_cash[-5:] if parse_number(r.get("fcf")) is not None]
        rev_cagr = _cagr(rev_vals, len(rev_vals) - 1) if len(rev_vals) >= 2 else None
        fcf_cagr = _cagr(fcf_vals, len(fcf_vals) - 1) if len(fcf_vals) >= 2 else None
        eps = _first_number(income.get("epsdil"), income.get("epsBasic"), self._stat_values("incomeStatement").get("eps"))
        lynch_growth_pct = eps_forecast if eps_forecast is not None else _first_number(analysts.get("eps3y"), percent_as_fraction=True)
        book_value_per_share = _first_number(self._stat_values("balanceSheet").get("bvps"))
        lynch_fair_value = (
            eps * lynch_growth_pct * 100.0
            if eps is not None and eps > 0 and lynch_growth_pct is not None and lynch_growth_pct > 0
            else None
        )
        graham_number = (
            sqrt(22.5 * eps * book_value_per_share)
            if eps is not None and eps > 0 and book_value_per_share is not None and book_value_per_share > 0
            else None
        )

        return {
            "price": self.current_price,
            "revenue": _first_number(income.get("revenue"), self._stat_values("incomeStatement").get("revenue")),
            "eps": eps,
            "bookValuePerShare": book_value_per_share,
            "fcf": _first_number(cash.get("fcf"), self._stat_values("cashFlow").get("fcf")),
            "unleveredFcf": _first_number(cash.get("unleveredFCF"), cash.get("fcf")),
            "fcfPerShare": _first_number(cash.get("fcfps"), self._stat_values("cashFlow").get("fcfps")),
            "shares": _first_number(balance.get("sharesOutTotalCommon"), income.get("sharesDiluted"), (self.data.get("fundamentals") or {}).get("sharesOutstanding")),
            "netCash": _first_number(balance.get("netcash"), 0.0),
            "pe": _first_number(ratios.get("pe"), (self.data.get("fundamentals") or {}).get("peRatio")),
            "forwardPe": _first_number(ratios.get("peForward"), (self.data.get("fundamentals") or {}).get("forwardPE")),
            "pFcf": _first_number(ratios.get("pfcf")),
            "fcfYield": _first_number(divs.get("fcfYield")),
            "grossMargin": _first_number(margins.get("grossMargin"), percent_as_fraction=True),
            "operatingMargin": _first_number(margins.get("operatingMargin"), percent_as_fraction=True),
            "profitMargin": _first_number(margins.get("profitMargin"), percent_as_fraction=True),
            "fcfMargin": _first_number(margins.get("fcfMargin"), percent_as_fraction=True),
            "roe": _first_number(eff.get("roe"), percent_as_fraction=True),
            "roic": _first_number(eff.get("roic"), percent_as_fraction=True),
            "wacc": _first_number(eff.get("wacc"), percent_as_fraction=True),
            "currentRatio": _first_number(pos.get("currentRatio")),
            "debtEbitda": _first_number(pos.get("debtEbitda")),
            "zScore": _first_number(scores.get("zScore")),
            "fScore": _first_number(scores.get("fScore")),
            "dividendYield": _first_number(divs.get("dividendYield"), percent_as_fraction=True),
            "buybackYield": _first_number(divs.get("buybackYield"), percent_as_fraction=True),
            "shareholderYield": _first_number(divs.get("totalReturn"), percent_as_fraction=True),
            "revenueForecastGrowthPct": revenue_forecast,
            "epsForecastGrowthPct": eps_forecast,
            "historicalRevenueCagr": rev_cagr,
            "historicalFcfCagr": fcf_cagr,
            "analystTarget": _first_number((self.forecast.get("priceTargets") or {}).get("avg"), analysts.get("priceTarget")),
            "analystTargetLow": _first_number((self.forecast.get("priceTargets") or {}).get("low")),
            "analystTargetHigh": _first_number((self.forecast.get("priceTargets") or {}).get("high")),
            "analystCount": _first_number((self.forecast.get("priceTargets") or {}).get("numPriceTargets"), analysts.get("analystCount")),
            "analystConsensus": (self.forecast.get("consensus") or {}).get("consensus") or analysts.get("analystRatings"),
            "lynchFairValue": lynch_fair_value,
            "grahamNumber": graham_number,
        }

    def quality_score(self, metrics: Mapping[str, Any]) -> Dict[str, Any]:
        growth = np.mean([
            _score_linear((metrics.get("revenueForecastGrowthPct") or 0) * 100, 0, 15),
            _score_linear((metrics.get("epsForecastGrowthPct") or 0) * 100, 0, 20),
            _score_linear((metrics.get("historicalRevenueCagr") or 0) * 100, 0, 12),
        ])
        profitability = np.mean([
            _score_linear((metrics.get("grossMargin") or 0) * 100, 20, 60),
            _score_linear((metrics.get("operatingMargin") or 0) * 100, 10, 35),
            _score_linear((metrics.get("fcfMargin") or 0) * 100, 8, 30),
            _score_linear((metrics.get("roic") or 0) * 100, 10, 40),
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
        quality/risk analysis and helps identify the sources of growth.
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
                    "growthPct": _py((_first_number(point.get("growth"), percent_as_fraction=True) or 0) * 100, 2),
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
        wacc_raw = metrics.get("wacc")
        rev_raw = metrics.get("revenueForecastGrowthPct")
        eps_raw = metrics.get("epsForecastGrowthPct")
        hist_fcf_g = metrics.get("historicalFcfCagr")
        required_missing = [
            name for name, value in (
                ("fcf", fcf), ("shares", shares), ("wacc", wacc_raw),
                ("revenue_growth", rev_raw), ("eps_growth", eps_raw),
            ) if value is None
        ]
        if required_missing:
            return None
        flags: List[str] = []
        wacc = wacc_raw
        wacc = _clip(wacc, 0.07, 0.16)
        rev_g = rev_raw
        eps_g = eps_raw
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
        confidence = 50.0
        confidence += 9.0 if wacc_raw is not None else 0.0
        confidence += 9.0 if rev_raw is not None else 0.0
        confidence += 9.0 if eps_raw is not None else 0.0
        confidence += 8.0 if metrics.get("historicalFcfCagr") is not None else 0.0
        confidence += 5.0 if metrics.get("netCash") is not None else 0.0
        confidence += 5.0 if metrics.get("unleveredFcf") is not None else 0.0
        confidence = _clip(confidence, 35.0, 92.0)
        return {
            "low": round(low, 2), "base": round(base, 2), "high": round(high, 2),
            "baseWeight": 0.35, "weight": 0.35, "confidence": round(confidence, 1),
            "assumptionFlags": flags,
            "usesGenericFallback": bool(flags),
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
        stats = ((self.forecast.get("estimates") or {}).get("stats") or {}).get("annual") or {}
        forecast_eps = _first_number((stats.get("epsNext") or {}).get("this"))
        hist = self._historical_ratio_values("pe")
        if not hist:
            return None
        flags: List[str] = []
        low_m = float(np.quantile(hist, 0.25))
        base_m = float(np.median(hist))
        high_m = float(np.quantile(hist, 0.75))
        multiple_quality = 20.0
        if forecast_eps is None:
            return None
        quality = self.quality_score(metrics)["overall"]
        growth = (metrics.get("epsForecastGrowthPct") or 0.0) * 100.0
        adjustment = _clip(0.90 + (quality - 50) / 250.0 + growth / 500.0, 0.80, 1.20)
        base_m *= adjustment
        low_m = min(low_m, base_m * 0.85)
        high_m = max(high_m, base_m * 1.15)
        confidence = _clip(55.0 + multiple_quality + (12.0 if forecast_eps is not None else 0.0), 40.0, 90.0)
        return {
            "low": round(eps * low_m, 2), "base": round(eps * base_m, 2), "high": round(eps * high_m, 2),
            "baseWeight": 0.30, "weight": 0.30, "confidence": round(confidence, 1),
            "assumptionFlags": flags, "usesGenericFallback": any("generic" in x for x in flags),
            "assumptions": {"forwardEps": round(eps, 4), "targetPeLow": round(low_m, 2), "targetPeBase": round(base_m, 2), "targetPeHigh": round(high_m, 2)},
        }

    def fcf_multiple_value(self, metrics: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        fcfps = metrics.get("fcfPerShare")
        if not fcfps or fcfps <= 0:
            return None
        hist = self._historical_ratio_values("pfcf")
        if not hist:
            return None
        flags: List[str] = []
        low_m = float(np.quantile(hist, 0.25))
        base_m = float(np.median(hist))
        high_m = float(np.quantile(hist, 0.75))
        confidence = 80.0
        return {
            "low": round(fcfps * low_m, 2), "base": round(fcfps * base_m, 2), "high": round(fcfps * high_m, 2),
            "baseWeight": 0.20, "weight": 0.20, "confidence": round(confidence, 1),
            "assumptionFlags": flags, "usesGenericFallback": any("generic" in x for x in flags),
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
            "baseWeight": 0.15, "weight": 0.15, "confidence": round(confidence, 1),
            "assumptionFlags": [], "usesGenericFallback": False,
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

        effective_raw = {
            name: float(method.get("baseWeight") or method.get("weight") or 0.0) * max(0.15, float(method.get("confidence") or 0.0) / 100.0)
            for name, method in methods.items()
        }
        weight_total = sum(effective_raw.values()) or 1.0
        for name, method in methods.items():
            normalized = effective_raw[name] / weight_total
            method["effectiveWeight"] = round(normalized, 6)
            method["weight"] = round(normalized, 6)

        bases = [(m.get("base"), m.get("weight", 0)) for m in methods.values()]
        lows = [(m.get("low"), m.get("weight", 0)) for m in methods.values()]
        highs = [(m.get("high"), m.get("weight", 0)) for m in methods.values()]
        base = _weighted_average(bases)
        low = _weighted_average(lows)
        high = _weighted_average(highs)
        price = self.current_price or metrics.get("price")
        upside = ((base / price) - 1) * 100 if base is not None and price else None
        valid_bases = [m["base"] for m in methods.values() if m.get("base")]
        dispersion = float(np.std(valid_bases) / np.mean(valid_bases)) if len(valid_bases) >= 2 and np.mean(valid_bases) else 0.30
        method_completeness = min(1.0, len(methods) / 4.0)
        core_metric_keys = (
            "revenue", "eps", "fcf", "shares", "grossMargin", "operatingMargin", "roic",
            "currentRatio", "debtEbitda", "pe", "forwardPe", "fcfYield",
            "revenueForecastGrowthPct", "epsForecastGrowthPct", "wacc", "netCash",
        )
        metric_present = sum(metrics.get(k) is not None for k in core_metric_keys)
        metric_coverage = metric_present / len(core_metric_keys)
        method_conf = _weighted_average([(m.get("confidence"), m.get("weight", 0)) for m in methods.values()]) or 35.0
        dispersion_factor = max(0.55, 1.0 - min(dispersion, 0.60) * 0.75)
        confidence = method_conf * (0.62 + 0.38 * method_completeness) * (0.68 + 0.32 * metric_coverage) * dispersion_factor
        confidence = _clip(confidence, 20.0, 92.0)

        valuation_score = 50.0 if upside is None else _clip(50 + upside * 1.6, 0, 100)
        raw_attractiveness = 0.58 * quality["overall"] + 0.42 * valuation_score
        reliability = 0.50 + 0.50 * confidence / 100.0
        attractiveness = 50.0 + (raw_attractiveness - 50.0) * reliability

        business_context = self.business_context()
        generic_methods = [name for name, method in methods.items() if method.get("usesGenericFallback")]
        assumption_flags = [
            f"{name}: {flag}"
            for name, method in methods.items()
            for flag in (method.get("assumptionFlags") or [])
        ]
        missing_fields = sorted(
            key for key in core_metric_keys
            if metrics.get(key) is None
        )
        insufficient_reasons = []
        if metric_coverage < 0.60:
            insufficient_reasons.append(
                f"Metric coverage {metric_coverage:.1%} is below the required 60% threshold."
            )
        if len(methods) < 2:
            insufficient_reasons.append(
                "At least two independent valuation methods with complete required inputs are required."
            )
        insufficient_reasons.extend(
            f"{name}: generic or fallback assumptions are not allowed for a decisive valuation."
            for name in generic_methods
        )
        valuation_status = "INSUFFICIENT_DATA" if insufficient_reasons else "AVAILABLE"
        if valuation_status == "INSUFFICIENT_DATA":
            base = low = high = upside = None
            confidence = 0.0
            attractiveness = 50.0
        references = {
            "lynchFairValue": metrics.get("lynchFairValue"),
            "grahamNumber": metrics.get("grahamNumber"),
            "note": "Lynch/Graham are shown as reference-only and excluded from consensus weighting for modern growth companies.",
        }

        return {
            "available": valuation_status == "AVAILABLE",
            "status": valuation_status,
            "insufficientDataReasons": insufficient_reasons,
            "provenance": {
                "data_coverage_ratio": round(metric_coverage, 6),
                "missing_fields": missing_fields,
                "fallback_assumptions_used": sorted(
                    dict.fromkeys(
                        item
                        for name, method in methods.items()
                        for item in (method.get("assumptionFlags") or [])
                        if method.get("usesGenericFallback")
                    )
                ),
                "source_freshness": self.data.get("generatedAt"),
            },
            "currentPrice": _py(price, 4),
            "metrics": {k: _py(v, 6) for k, v in metrics.items()},
            "quality": quality,
            "businessContext": business_context,
            "methods": methods,
            "referenceFormulas": references,
            "inputQuality": {
                "metricCoveragePct": round(metric_coverage * 100.0, 1),
                "coreMetricsAvailable": metric_present,
                "coreMetricsExpected": len(core_metric_keys),
                "valuationMethodCount": len(methods),
                "weightedMethodConfidence": round(method_conf, 2),
                "genericFallbackMethods": generic_methods,
                "assumptionFlags": assumption_flags,
            },
            "consensus": {
                "low": _py(low, 2), "base": _py(base, 2), "high": _py(high, 2),
                "upsideToBasePct": _py(upside, 2),
                "valuation": "Insufficient Data" if valuation_status == "INSUFFICIENT_DATA" else _valuation_label(upside),
                "confidence": round(confidence, 2),
                "confidenceLabel": _confidence_label(confidence),
                "methodDispersionPct": round(dispersion * 100, 2),
            },
            "attractiveness": {
                "score": round(attractiveness, 2),
                "rawScore": round(raw_attractiveness, 2),
                "label": "Insufficient Data" if valuation_status == "INSUFFICIENT_DATA" else _label_score(attractiveness),
            },
        }
