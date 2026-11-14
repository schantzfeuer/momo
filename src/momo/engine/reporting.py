"""Deterministic summary, risk, and report builders for stock analysis."""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence, Tuple

from .fundamental import (
    _confidence_label,
    _label_score,
    _weighted_average,
)
from .market_data import _py


def catalysts_and_risks(
    fundamental: Mapping[str, Any],
    technical: Mapping[str, Any],
) -> Tuple[List[str], List[str]]:
    catalysts: List[str] = []
    risks: List[str] = []
    quality = fundamental.get("quality") or {}
    consensus = fundamental.get("consensus") or {}
    business = fundamental.get("businessContext") or {}

    quality_score = quality.get("overall")
    if quality_score is not None:
        if quality_score >= 70:
            catalysts.append(f"Business quality is strong ({quality_score}/100).")
        elif quality_score < 45:
            risks.append(f"Business quality is weak ({quality_score}/100).")

    upside = consensus.get("upsideToBasePct")
    if upside is not None:
        if upside >= 15:
            catalysts.append(f"Fundamental fair value implies {upside:.1f}% upside to the base estimate.")
        elif upside <= -15:
            risks.append(f"Fundamental valuation implies {abs(upside):.1f}% downside to the base estimate.")

    if consensus.get("methodDispersionPct") is not None and consensus["methodDispersionPct"] > 40:
        risks.append("Valuation methods are widely dispersed; treat fair value as especially uncertain.")
    if business.get("weightedSegmentGrowthPct") is not None and business["weightedSegmentGrowthPct"] > 0:
        catalysts.append(
            f"Weighted segment growth is approximately {business['weightedSegmentGrowthPct']:.1f}%."
        )

    timeframes = technical.get("timeframes") or {}
    bearish = [key for key, value in timeframes.items() if (value.get("technicalScore") or 50) < 40]
    bullish = [key for key, value in timeframes.items() if (value.get("technicalScore") or 50) > 65]
    if bearish:
        risks.append("Technical momentum is weak on: " + ", ".join(bearish) + ".")
    if bullish:
        catalysts.append("Technical trend is constructive on: " + ", ".join(bullish) + ".")
    return catalysts, risks


def build_executive_summary(
    blended: Mapping[str, Any],
    fundamental: Mapping[str, Any],
    technical: Mapping[str, Any],
) -> Dict[str, Any]:
    if blended:
        weighted = []
        confidence_rows = []
        for key, item in blended.items():
            horizon_weight = {"1m": 0.15, "3m": 0.20, "6m": 0.25, "12m": 0.40}.get(key, 0.25)
            confidence = max(0.15, float(item.get("confidence") or 0.0) / 100.0)
            weighted.append((item.get("compositeScore"), horizon_weight * confidence))
            confidence_rows.append((item.get("confidence"), horizon_weight))
        overall = _weighted_average(weighted) or 50.0
        overall_confidence = _weighted_average(confidence_rows)
    else:
        overall = (
            (fundamental.get("attractiveness") or {}).get("score")
            or technical.get("aggregateScore")
            or 50.0
        )
        overall_confidence = (
            (fundamental.get("consensus") or {}).get("confidence")
            or technical.get("averageConfidence")
        )
    consensus = fundamental.get("consensus") or {}
    return {
        "overallScore": round(overall, 2),
        "stance": _label_score(overall),
        "confidence": _py(overall_confidence, 2),
        "confidenceLabel": _confidence_label(overall_confidence),
        "currentPrice": fundamental.get("currentPrice")
        or next((item.get("currentPrice") for item in (technical.get("timeframes") or {}).values()), None),
        "fundamentalFairValue": consensus.get("base"),
        "fundamentalValuation": consensus.get("valuation"),
        "fundamentalQuality": (fundamental.get("quality") or {}).get("label"),
        "technicalAggregateScore": technical.get("aggregateScore"),
        "interpretation": (
            "Score combines business quality, valuation gap, trend, momentum, volume and volatility. "
            "Low-confidence inputs are automatically given less influence; short horizons remain "
            "technical-heavy and long horizons fundamental-heavy."
        ),
    }


def build_professional_report(
    ticker: str,
    summary: Mapping[str, Any],
    blended: Mapping[str, Any],
    fundamental: Mapping[str, Any],
    technical: Mapping[str, Any],
    catalysts: Sequence[str],
    risks: Sequence[str],
) -> Dict[str, Any]:
    consensus = fundamental.get("consensus") or {}
    quality = fundamental.get("quality") or {}
    business = fundamental.get("businessContext") or {}
    current, base = summary.get("currentPrice"), consensus.get("base")
    valuation_text = "Fundamental fair value is not yet available."
    if current is not None and base is not None:
        low, high = consensus.get("low"), consensus.get("high")
        upside = consensus.get("upsideToBasePct")
        valuation_text = (
            f"Price of {current:.2f} versus fundamental fair value of {base:.2f} "
            f"(range {f'{low:.2f}' if low is not None else 'n/a'}-"
            f"{f'{high:.2f}' if high is not None else 'n/a'}) results in a "
            f"{f'{upside:+.1f}%' if upside is not None else 'n/a'} gap. "
            f"Valuation status: {consensus.get('valuation')}."
        )

    thesis: List[str] = []
    if quality:
        thesis.append(
            f"Business quality {quality.get('label')} ({quality.get('overall')}/100): "
            f"profitability {quality.get('profitability')}/100, growth {quality.get('growth')}/100, "
            f"financial health {quality.get('financialHealth')}/100."
        )
    if business.get("weightedSegmentGrowthPct") is not None:
        thesis.append(
            f"Latest weighted segment growth is approximately "
            f"{business['weightedSegmentGrowthPct']:.1f}%, with largest-segment concentration "
            f"around {business.get('largestSegmentConcentrationPct', 0):.1f}%."
        )
    if consensus.get("methodDispersionPct") is not None:
        thesis.append(
            f"Valuation dispersion across methods is {consensus['methodDispersionPct']:.1f}%, "
            "so fair value should be interpreted as a range, not a point estimate."
        )

    outlook: Dict[str, str] = {}
    for key, item in blended.items():
        fair_value = item.get("fairValue") or {}
        gap = item.get("expectedGapPct")
        outlook[key] = (
            f"{item.get('label')}: {item.get('stance')} (score {item.get('compositeScore')}/100), "
            f"technical trend {item.get('technicalTrend') or 'n/a'}, blended fair-value range "
            f"{fair_value.get('low')}-{fair_value.get('high')} with base {fair_value.get('base')} "
            f"({f'{gap:+.1f}%' if gap is not None else 'n/a'} vs current)."
        )

    timeframes = technical.get("timeframes") or {}
    tactical = timeframes.get("3m") or timeframes.get("1m") or next(iter(timeframes.values()), {})
    levels = tactical.get("levels") or {}
    methods = fundamental.get("methods") or {}
    method_names = list(methods.keys())
    return {
        "headline": f"{ticker}: {summary.get('stance')} multi-horizon setup "
        f"(score {summary.get('overallScore')}/100).",
        "investmentCase": thesis,
        "valuationView": valuation_text,
        "timeframeOutlook": outlook,
        "tacticalLevels": {
            "supports": levels.get("supports") or [],
            "resistances": levels.get("resistances") or [],
            "note": "Levels are technical reference zones, not guaranteed execution prices.",
        },
        "catalysts": list(catalysts),
        "keyRisks": list(risks),
        "dataQuality": {
            "fundamentalMethodsUsed": method_names,
            "fundamentalMethodCount": len(method_names),
            "fundamentalConfidence": consensus.get("confidence"),
            "fundamentalInputQuality": fundamental.get("inputQuality") or {},
            "technicalAvailable": technical.get("available"),
            "technicalDataPoints": technical.get("dataPoints"),
            "technicalAverageConfidence": technical.get("averageConfidence"),
            "priceDataQuality": technical.get("priceDataQuality"),
            "timeframesAnalyzed": list(blended.keys()),
        },
        "professionalInterpretation": (
            "Use the 1m/3m view for timing and risk management, 6m for transition between momentum "
            "and valuation, and 12m for the fundamental thesis. A high-quality business can still "
            "have poor expected returns when the entry valuation is demanding; conversely, attractive "
            "valuation without technical confirmation can remain weak for an extended period."
        ),
    }
