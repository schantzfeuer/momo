from __future__ import annotations
from dataclasses import asdict
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
import pandas as pd
from .. import __version__
from .fundamental import FundamentalValuationEngine, _confidence_label, _label_score, _valuation_label, _weighted_average
from .indicators import compute_indicators
from .market_data import _clip, _py, fetch_yahoo_history, standardize_ohlcv
from .reporting import build_executive_summary, build_professional_report, catalysts_and_risks
from .technical import TimeframeSpec, analyze_technical_timeframe, resolve_timeframes

class ProfessionalStockAnalyzer:
    """Integrated multi-timeframe technical + fundamental analyzer."""

    def __init__(
        self,
        ticker: str,
        price_data: Optional[pd.DataFrame] = None,
        fundamental_data: Optional[Mapping[str, Any]] = None,
        period: str = "10y",
        interval: str = "1d",
        auto_fetch: bool = True,
    ):
        self.ticker = str(ticker).strip().upper()
        self.fundamental_data = fundamental_data or {}
        self.price_error: Optional[str] = None
        if price_data is None and auto_fetch:
            try:
                price_data = fetch_yahoo_history(self.ticker, period=period, interval=interval)
            except Exception:
                self.price_error = "Historical price data could not be retrieved."
                price_data = None
        self.price_data = standardize_ohlcv(price_data) if price_data is not None else None
        self.indicators = compute_indicators(self.price_data) if self.price_data is not None else None

    def _technical(self, specs: Sequence[TimeframeSpec]) -> Dict[str, Any]:
        if self.indicators is None:
            return {"available": False, "reason": self.price_error or "Historical OHLCV is unavailable", "timeframes": {}}
        outputs = {spec.key: analyze_technical_timeframe(self.price_data, spec) for spec in specs}
        aggregate = _weighted_average([
            (v.get("technicalScore"), max(5.0, float(v.get("confidence") or 0.0)))
            for v in outputs.values()
        ])
        avg_conf = _weighted_average([(v.get("confidence"), 1.0) for v in outputs.values()])
        price_meta = dict((getattr(self.price_data, "attrs", {}) or {}).get("market_meta") or {})
        return {
            "available": True,
            "dataPoints": len(self.indicators),
            "from": _py(self.indicators.index[0]),
            "to": _py(self.indicators.index[-1]),
            "aggregateScore": round(float(aggregate), 2) if aggregate is not None else None,
            "averageConfidence": _py(avg_conf, 2),
            "priceDataQuality": price_meta.get("quality") or None,
            "timeframes": outputs,
        }

    def _fundamental(self) -> Dict[str, Any]:
        if not self.fundamental_data:
            return {"available": False, "reason": "Fundamental data is unavailable"}
        result = FundamentalValuationEngine(self.fundamental_data).evaluate()
        result["available"] = True
        return result

    def _blend_timeframes(self, specs: Sequence[TimeframeSpec], technical: Mapping[str, Any], fundamental: Mapping[str, Any]) -> Dict[str, Any]:
        output: Dict[str, Any] = {}
        f_cons = fundamental.get("consensus") or {}
        f_base, f_low, f_high = f_cons.get("base"), f_cons.get("low"), f_cons.get("high")
        f_attr = ((fundamental.get("attractiveness") or {}).get("score"))
        f_reliability = _clip(float(f_cons.get("confidence") or 0.0) / 100.0, 0.0, 1.0)
        current = fundamental.get("currentPrice")
        if current is None and technical.get("available"):
            tf_values = technical.get("timeframes") or {}
            first = next(iter(tf_values.values()), {})
            current = first.get("currentPrice")

        for spec in specs:
            t = (technical.get("timeframes") or {}).get(spec.key) or {}
            t_proj = t.get("projection") or {}
            t_base, t_low, t_high = t_proj.get("base"), t_proj.get("bear"), t_proj.get("bull")
            requested_tw, requested_fw = spec.technical_weight, spec.fundamental_weight
            t_reliability = _clip(float(t.get("confidence") or 0.0) / 100.0, 0.0, 1.0)
            tw = requested_tw * t_reliability
            fw = requested_fw * f_reliability

            if t_base is None and f_base is not None:
                tw, fw = 0.0, max(fw, 0.01)
            elif f_base is None and t_base is not None:
                tw, fw = max(tw, 0.01), 0.0
            total_w = tw + fw
            if total_w <= 0:
                continue
            tw, fw = tw / total_w, fw / total_w

            base = _weighted_average([(t_base, tw), (f_base, fw)])
            low = _weighted_average([(t_low, tw), (f_low, fw)])
            high = _weighted_average([(t_high, tw), (f_high, fw)])
            upside = ((base / current) - 1) * 100 if base is not None and current else None
            t_score = t.get("technicalScore")
            composite = _weighted_average([(t_score, tw), (f_attr, fw)]) or 50.0
            confidence = _weighted_average([(t.get("confidence"), tw), (f_cons.get("confidence"), fw)])

            output[spec.key] = {
                "label": spec.label,
                "horizonTradingDays": spec.horizon_days,
                "requestedWeights": {"technical": round(requested_tw, 2), "fundamental": round(requested_fw, 2)},
                "weights": {"technical": round(tw, 3), "fundamental": round(fw, 3)},
                "reliability": {"technical": round(t_reliability, 3), "fundamental": round(f_reliability, 3)},
                "currentPrice": _py(current, 4),
                "fairValue": {"low": _py(low, 2), "base": _py(base, 2), "high": _py(high, 2)},
                "expectedGapPct": _py(upside, 2),
                "valuation": _valuation_label(upside),
                "compositeScore": round(composite, 2),
                "stance": _label_score(composite),
                "confidence": _py(confidence, 2),
                "confidenceLabel": _confidence_label(confidence),
                "technicalTrend": t.get("trend"),
                "technicalScore": t_score,
            }
        return output




    def analyze(self, timeframes: Optional[Sequence[Any]] = None) -> Dict[str, Any]:
        specs = resolve_timeframes(timeframes)
        technical = self._technical(specs)
        fundamental = self._fundamental()
        blended = self._blend_timeframes(specs, technical, fundamental)
        catalysts, risks = catalysts_and_risks(fundamental, technical)
        summary = build_executive_summary(blended, fundamental, technical)
        professional_report = build_professional_report(self.ticker, summary, blended, fundamental, technical, catalysts, risks)
        technical_fair_value = {
            key: (value.get("technicalReference") or {})
            for key, value in (technical.get("timeframes") or {}).items()
        }
        warnings: List[str] = []
        f_input = fundamental.get("inputQuality") or {}
        if f_input.get("metricCoveragePct") is not None and float(f_input.get("metricCoveragePct") or 0.0) < 60.0:
            warnings.append("Fundamental metric coverage is limited; valuation confidence is reduced.")
        if f_input.get("genericFallbackMethods"):
            warnings.append("One or more valuation methods use generic fallback assumptions and receive reduced weight.")
        price_quality = technical.get("priceDataQuality") or {}
        warnings.extend(price_quality.get("warnings") or [])
        if not technical.get("available"):
            warnings.append("Historical price data is unavailable; technical analysis is excluded.")
        if not fundamental.get("available"):
            warnings.append("Fundamental data is unavailable; valuation analysis is excluded.")
        analysis_quality = {
            "overallConfidence": summary.get("confidence"),
            "confidenceLabel": summary.get("confidenceLabel"),
            "fundamentalConfidence": (fundamental.get("consensus") or {}).get("confidence"),
            "technicalConfidence": technical.get("averageConfidence"),
            "fundamentalMetricCoveragePct": f_input.get("metricCoveragePct"),
            "priceHistory": price_quality or None,
            "warnings": list(dict.fromkeys(str(x) for x in warnings if x)),
        }
        return {
            "ticker": self.ticker,
            "engineVersion": __version__,
            "executiveSummary": summary,
            "professionalReport": professional_report,
            "analysisQuality": analysis_quality,
            "timeframes": blended,
            "fairValue": {
                "fundamental": fundamental.get("consensus") if fundamental.get("available") else None,
                "technicalReferenceByTimeframe": technical_fair_value,
                "blendedByTimeframe": {key: value.get("fairValue") for key, value in blended.items()},
            },
            "fundamental": fundamental,
            "technical": technical,
            "catalysts": catalysts,
            "risks": risks,
            "methodology": {
                "timeframes": [asdict(spec) for spec in specs],
                "fundamental": "Confidence-adjusted DCF proxy, forward earnings multiple, FCF multiple and analyst consensus. Methods using fallback assumptions receive less weight.",
                "technical": "Trend/momentum/volume/Ichimoku score plus log-price regression and realized-volatility scenario envelope, with history/indicator coverage confidence.",
                "blending": "Technical weight decreases and fundamental weight increases as investment horizon lengthens; both are further scaled by measured input confidence.",
            },
            "disclaimer": "Analytical model output, not personalized investment advice or a guaranteed price forecast.",
        }


# =============================================================================
