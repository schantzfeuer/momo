from __future__ import annotations

import html
import math
import re
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from momo.core.units import parse_number
from .view_models import (
    assets_under_management as _assets_under_management,
    number as _number,
    format_price as _format_price,
    format_date as _format_date,
    prepare_prices as _prepare_prices,
)
from .rendering import escape as _escape, detail_block as _detail_block, table as _table


_COLORS = {
    "background": "#0b1020",
    "panel": "#111a2e",
    "grid": "#26334b",
    "text": "#e6edf7",
    "muted": "#8fa1ba",
    "up": "#2dd4a7",
    "down": "#ff7185",
    "sma20": "#fbbf24",
    "sma50": "#60a5fa",
    "sma200": "#c084fc",
    "band": "#7c8da8",
    "rsi": "#c084fc",
    "macd": "#60a5fa",
}


def _pandas_module() -> Any:
    try:
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError("Visual charts require pandas; install MOMO's analysis dependencies.") from exc
    return pd


def _series_path(values: Sequence[Optional[float]], xs: Sequence[float], y_for: Any) -> str:
    points = []
    for x, value in zip(xs, values):
        if value is not None:
            points.append(f"{x:.2f},{y_for(value):.2f}")
    return " ".join(points)


def _build_chart(frame: Any, levels: Mapping[str, Any]) -> str:
    pd = _pandas_module()
    width = 1360
    left, right = 82.0, 1320.0
    plot_width = right - left
    count = len(frame)
    step = plot_width / max(count, 1)
    xs = [left + (index + 0.5) * step for index in range(count)]
    high_low = [float(value) for value in frame["High"]] + [float(value) for value in frame["Low"]]
    overlays = ["BBUpper", "BBLower", "SMA20", "SMA50", "SMA200"]
    high_low.extend(float(value) for column in overlays for value in frame[column].dropna())
    floor = min(high_low)
    ceiling = max(high_low)
    padding = max((ceiling - floor) * 0.06, abs(ceiling) * 0.005, 0.01)
    floor -= padding
    ceiling += padding

    price_top, price_bottom = 30.0, 385.0
    volume_top, volume_bottom = 420.0, 520.0
    rsi_top, rsi_bottom = 555.0, 665.0
    macd_top, macd_bottom = 700.0, 810.0

    def price_y(value: float) -> float:
        return price_bottom - (value - floor) / (ceiling - floor) * (price_bottom - price_top)

    max_volume = max((float(value) for value in frame["Volume"] if _number(value) is not None), default=0.0) or 1.0
    rsi_y = lambda value: rsi_bottom - value / 100.0 * (rsi_bottom - rsi_top)
    macd_values = [float(value) for col in ["MACD", "MACDSignal", "MACDHist"] for value in frame[col].dropna()]
    macd_extent = max(max((abs(value) for value in macd_values), default=1.0), 0.000001) * 1.15
    macd_y = lambda value: macd_top + (macd_extent - value) / (2 * macd_extent) * (macd_bottom - macd_top)

    svg = [
        f'<svg viewBox="0 0 {width} 840" role="img" aria-label="Price, volume, RSI and MACD chart">',
        '<rect width="100%" height="100%" fill="#111a2e" rx="14"/>',
    ]

    for index in range(5):
        value = ceiling - (ceiling - floor) * index / 4
        y = price_y(value)
        svg.append(f'<line x1="{left}" y1="{y:.2f}" x2="{right}" y2="{y:.2f}" stroke="{_COLORS["grid"]}"/>')
        svg.append(f'<text x="{left - 12}" y="{y + 4:.2f}" text-anchor="end">{value:,.2f}</text>')

    upper_points = []
    lower_points = []
    for x, upper, lower in zip(xs, frame["BBUpper"], frame["BBLower"]):
        if _number(upper) is not None and _number(lower) is not None:
            upper_points.append(f"{x:.2f},{price_y(float(upper)):.2f}")
            lower_points.append(f"{x:.2f},{price_y(float(lower)):.2f}")
    if upper_points:
        svg.append(
            f'<polygon points="{" ".join(upper_points + list(reversed(lower_points)))}" '
            f'fill="{_COLORS["band"]}" opacity=".10"/>'
        )

    for column, color, width_px in [
        ("SMA20", _COLORS["sma20"], 2.0),
        ("SMA50", _COLORS["sma50"], 2.0),
        ("SMA200", _COLORS["sma200"], 2.4),
    ]:
        values = [_number(value) for value in frame[column]]
        path = _series_path(values, xs, price_y)
        svg.append(f'<polyline points="{path}" fill="none" stroke="{color}" stroke-width="{width_px}"/>')

    candle_width = max(1.2, min(7.0, step * 0.66))
    for index, (_, row) in enumerate(frame.iterrows()):
        x = xs[index]
        opening, high, low, close = (float(row[column]) for column in ["Open", "High", "Low", "Close"])
        color = _COLORS["up"] if close >= opening else _COLORS["down"]
        body_top = price_y(max(opening, close))
        body_bottom = price_y(min(opening, close))
        body_height = max(1.0, body_bottom - body_top)
        tooltip = html.escape(
            f"{_format_date(frame.index[index])} · O {_format_price(opening)} · "
            f"H {_format_price(high)} · L {_format_price(low)} · C {_format_price(close)}"
        )
        svg.append(
            f'<g><title>{tooltip}</title><line x1="{x:.2f}" y1="{price_y(high):.2f}" '
            f'x2="{x:.2f}" y2="{price_y(low):.2f}" stroke="{color}" stroke-width="1.2"/>'
            f'<rect x="{x - candle_width / 2:.2f}" y="{body_top:.2f}" width="{candle_width:.2f}" '
            f'height="{body_height:.2f}" rx=".5" fill="{color}"/></g>'
        )

    for label, color, dash in [
        ("Support", _COLORS["up"], "6 5"),
        ("Resistance", _COLORS["down"], "6 5"),
    ]:
        values = levels.get("supports" if label == "Support" else "resistances") or []
        value = _number(values[0]) if values else None
        if value is not None and floor <= value <= ceiling:
            y = price_y(value)
            svg.append(
                f'<line x1="{left}" y1="{y:.2f}" x2="{right}" y2="{y:.2f}" '
                f'stroke="{color}" stroke-width="1.4" stroke-dasharray="{dash}" opacity=".9"/>'
            )
            svg.append(
                f'<text x="{right - 6}" y="{y - 5:.2f}" text-anchor="end" fill="{color}">'
                f'{label} {_format_price(value)}</text>'
            )

    current_price = float(frame["Close"].iloc[-1])
    current_y = price_y(current_price)
    svg.append(f'<line x1="{left}" y1="{current_y:.2f}" x2="{right}" y2="{current_y:.2f}" stroke="#e6edf7" stroke-dasharray="3 5" opacity=".55"/>')
    svg.append(f'<text x="{right - 6}" y="{current_y - 6:.2f}" text-anchor="end" fill="#e6edf7">Last {_format_price(current_price)}</text>')

    for label, color, x in [
        ("BB", _COLORS["band"], left + 6),
        ("SMA 20", _COLORS["sma20"], left + 72),
        ("SMA 50", _COLORS["sma50"], left + 158),
        ("SMA 200", _COLORS["sma200"], left + 244),
    ]:
        svg.append(f'<line x1="{x}" y1="17" x2="{x + 18}" y2="17" stroke="{color}" stroke-width="2.5"/>')
        svg.append(f'<text x="{x + 23}" y="21" fill="{color}">{label}</text>')
    svg.append(f'<text x="{left}" y="{volume_top - 9}" fill="{_COLORS["muted"]}">VOLUME</text>')
    svg.append(f'<text x="{left}" y="{rsi_top - 9}" fill="{_COLORS["muted"]}">RSI · 14</text>')
    svg.append(f'<text x="{left}" y="{macd_top - 9}" fill="{_COLORS["muted"]}">MACD · 12 / 26 / 9</text>')

    for index, (_, row) in enumerate(frame.iterrows()):
        x = xs[index]
        volume = max(0.0, float(row["Volume"]))
        y = volume_bottom - volume / max_volume * (volume_bottom - volume_top)
        color = _COLORS["up"] if float(row["Close"]) >= float(row["Open"]) else _COLORS["down"]
        svg.append(f'<rect x="{x - candle_width / 2:.2f}" y="{y:.2f}" width="{candle_width:.2f}" height="{volume_bottom - y:.2f}" fill="{color}" opacity=".62"/>')

    for value in [30, 50, 70]:
        y = rsi_y(value)
        svg.append(f'<line x1="{left}" y1="{y:.2f}" x2="{right}" y2="{y:.2f}" stroke="{_COLORS["grid"]}" stroke-dasharray="4 5"/>')
        svg.append(f'<text x="{left - 12}" y="{y + 4:.2f}" text-anchor="end">{value}</text>')
    rsi_path = _series_path([_number(value) for value in frame["RSI"]], xs, rsi_y)
    svg.append(f'<polyline points="{rsi_path}" fill="none" stroke="{_COLORS["rsi"]}" stroke-width="2"/>')

    zero_y = macd_y(0.0)
    svg.append(f'<line x1="{left}" y1="{zero_y:.2f}" x2="{right}" y2="{zero_y:.2f}" stroke="{_COLORS["grid"]}"/>')
    for index, (_, row) in enumerate(frame.iterrows()):
        value = _number(row["MACDHist"])
        if value is None:
            continue
        x = xs[index]
        y = macd_y(value)
        color = _COLORS["up"] if value >= 0 else _COLORS["down"]
        svg.append(f'<rect x="{x - candle_width / 2:.2f}" y="{min(y, zero_y):.2f}" width="{candle_width:.2f}" height="{max(abs(y - zero_y), .6):.2f}" fill="{color}" opacity=".55"/>')
    svg.append(f'<polyline points="{_series_path([_number(value) for value in frame["MACD"]], xs, macd_y)}" fill="none" stroke="{_COLORS["macd"]}" stroke-width="1.8"/>')
    svg.append(f'<polyline points="{_series_path([_number(value) for value in frame["MACDSignal"]], xs, macd_y)}" fill="none" stroke="{_COLORS["sma20"]}" stroke-width="1.6"/>')

    for index in sorted(set([0, count // 4, count // 2, (count * 3) // 4, count - 1])):
        x = xs[index]
        date = html.escape(pd.Timestamp(frame.index[index]).strftime("%b %y"))
        svg.append(f'<text x="{x:.2f}" y="832" text-anchor="middle">{date}</text>')
    svg.append("</svg>")
    return "\n".join(svg)


def _money(value: Any, compact: bool = False) -> str:
    number = _number(value)
    if number is None:
        return "Not available"
    sign = "-" if number < 0 else ""
    number = abs(number)
    if compact:
        if number >= 1_000_000_000_000:
            return f"{sign}${number / 1_000_000_000_000:,.2f}T"
        if number >= 1_000_000_000:
            return f"{sign}${number / 1_000_000_000:,.2f}B"
        if number >= 1_000_000:
            return f"{sign}${number / 1_000_000:,.2f}M"
    return f"{sign}${number:,.2f}"


def _financial_value(value: Any, compact: bool = False) -> str:
    number = _number(value)
    if number is not None:
        return _money(number, compact=compact)
    text = str(value).strip()
    if re.fullmatch(r"-?[\d,]+(?:\.\d+)?[TMBK]", text, re.IGNORECASE):
        return f"${text}"
    return text or "Not available"


def _percent(value: Any, signed: bool = False, fraction: bool = False) -> str:
    number = _number(value)
    if number is None:
        return "Not available"
    if fraction:
        number *= 100
    sign = "+" if signed and number > 0 else ""
    rounded = Decimal(str(number)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return f"{sign}{rounded}%"


def _fund_percent(value: Any, fraction: bool = False, decimals: int = 1) -> str:
    if isinstance(value, str) and "%" in value:
        number = _number(value.replace("%", "").strip())
        if number is None:
            return "Not available"
        return f"{number:.{decimals}f}%"
    number = _number(value)
    if number is None:
        return "Not available"
    if fraction:
        number *= 100
    return f"{number:.{decimals}f}%"


def _metric_card(label: str, value: str, note: str = "") -> str:
    note_html = f'<div class="card-note">{_escape(note)}</div>' if note else ""
    return (
        '<article class="card insight-card">'
        f'<div class="label">{_escape(label)}</div>'
        f'<div class="value">{_escape(value)}</div>{note_html}</article>'
    )


def _bullet_list(items: Sequence[Any], empty: str) -> str:
    if not items:
        return f'<p class="muted">{_escape(empty)}</p>'
    return "<ul class=\"story-list\">" + "".join(
        f"<li>{_escape(item)}</li>" for item in items
    ) + "</ul>"


def _overview_html(analysis: Mapping[str, Any]) -> str:
    summary = analysis.get("executiveSummary") or {}
    report = analysis.get("professionalReport") or {}
    fair_value = (analysis.get("fairValue") or {}).get("fundamental") or {}
    gap = fair_value.get("upsideToBasePct")
    current_price = summary.get("currentPrice")
    if current_price is None:
        current_price = ((analysis.get("fundamental") or {}).get("metrics") or {}).get("price")
    gap_number = _number(gap)
    cards = [
        _metric_card("Overall view", summary.get("stance") or "Not available", "Model assessment, not a recommendation"),
        _metric_card("Overall score", f"{_format_price(summary.get('overallScore'))} / 100", "Higher scores indicate a stronger model reading"),
        _metric_card(
            "Analysis confidence",
            f"{_format_price(summary.get('confidence'))} / 100",
            summary.get("confidenceLabel") or "Confidence reflects input quality",
        ),
        _metric_card(
            "Fundamental value estimate",
            _money(fair_value.get("base")),
            f"Estimated range: {_money(fair_value.get('low'))} to {_money(fair_value.get('high'))}",
        ),
    ]
    headline = report.get("headline") or "Summary is not available."
    valuation_label = fair_value.get("valuation") or summary.get("fundamentalValuation")
    base_value = fair_value.get("base")
    if _number(current_price) is not None and _number(base_value) is not None and gap_number is not None:
        if gap_number < 0:
            gap_description = f"{_percent(abs(gap_number))} below the current price"
        elif gap_number > 0:
            gap_description = f"{_percent(gap_number)} above the current price"
        else:
            gap_description = "in line with the current price"
        valuation_view = (
            f"At {_money(current_price)}, the model's fundamental estimate is {_money(base_value)} "
            f"(range: {_money(fair_value.get('low'))} to {_money(fair_value.get('high'))}). "
            f"The base estimate is {gap_description}; the model describes this as "
            f"{valuation_label or 'a valuation result'}."
        )
    else:
        valuation_view = report.get("valuationView") or ""
    interpretation = report.get("professionalInterpretation") or summary.get("interpretation") or ""
    gap_label = _percent(gap, signed=True) if gap is not None else "Not available"
    gap_class = "positive" if (_number(gap) or 0) >= 0 else "negative"
    case = _bullet_list(report.get("investmentCase") or [], "No investment case details were returned.")
    catalysts = _bullet_list(analysis.get("catalysts") or report.get("catalysts") or [], "No catalysts were identified.")
    risks = _bullet_list(analysis.get("risks") or report.get("keyRisks") or [], "No key risks were identified.")
    return (
        '<section class="analysis-section" id="summary">'
        '<div class="section-kicker">The short version</div><h2>What the analysis says</h2>'
        f'<p class="section-lead">{_escape(headline)}</p>'
        f'<div class="grid insight-grid">{"".join(cards)}</div>'
        f'<p class="analysis-copy">{_escape(valuation_view)}</p>'
        f'<p class="analysis-copy muted">{_escape(interpretation)}</p>'
        '<p class="muted">The overall view blends business quality, valuation and technical trends. '
        "It can differ from the valuation label because the balance of those factors changes by time horizon.</p>"
        f'<p class="gap-callout">Estimated value gap: <strong class="{gap_class}">{_escape(gap_label)}</strong>'
        " versus the current price. This is a model estimate, not a price target.</p>"
        '<div class="story-grid">'
        f'<div class="story-card"><h3>Why the model reached this view</h3>{case}</div>'
        f'<div class="story-card"><h3>Potential positive factors</h3>{catalysts}</div>'
        f'<div class="story-card"><h3>Risks to keep in mind</h3>{risks}</div>'
        "</div></section>"
    )


_METRIC_LABELS = {
    "price": "Current share price",
    "revenue": "Revenue",
    "eps": "Earnings per share",
    "bookValuePerShare": "Book value per share",
    "fcf": "Free cash flow",
    "unleveredFcf": "Unlevered free cash flow",
    "fcfPerShare": "Free cash flow per share",
    "shares": "Shares outstanding",
    "netCash": "Net cash",
    "pe": "Price-to-earnings",
    "forwardPe": "Forward price-to-earnings",
    "pFcf": "Price-to-free-cash-flow",
    "fcfYield": "Free cash flow yield",
    "grossMargin": "Gross margin",
    "operatingMargin": "Operating margin",
    "profitMargin": "Net profit margin",
    "fcfMargin": "Free cash flow margin",
    "roe": "Return on equity",
    "roic": "Return on invested capital",
    "wacc": "Discount rate (WACC)",
    "currentRatio": "Current ratio",
    "debtEbitda": "Debt-to-EBITDA",
    "zScore": "Altman Z-score",
    "fScore": "Piotroski F-score",
    "dividendYield": "Dividend yield",
    "buybackYield": "Share buyback yield",
    "shareholderYield": "Total shareholder yield",
    "revenueForecastGrowthPct": "Forecast revenue growth",
    "epsForecastGrowthPct": "Forecast earnings growth",
    "historicalRevenueCagr": "Historical revenue growth (annual)",
    "historicalFcfCagr": "Historical free cash flow growth (annual)",
    "analystTarget": "Average analyst estimate",
    "analystTargetLow": "Low analyst estimate",
    "analystTargetHigh": "High analyst estimate",
    "analystCount": "Analyst estimates included",
    "analystConsensus": "Analyst consensus",
    "lynchFairValue": "Peter Lynch reference estimate",
    "grahamNumber": "Graham Number reference estimate",
}

_METRIC_GROUPS = [
    ("Business size & cash flow", (
        "revenue", "fcf", "unleveredFcf", "netCash", "shares",
    )),
    ("Per-share figures", (
        "price", "eps", "bookValuePerShare", "fcfPerShare",
    )),
    ("Growth", (
        "revenueForecastGrowthPct", "epsForecastGrowthPct",
        "historicalRevenueCagr", "historicalFcfCagr",
    )),
    ("Profitability", (
        "grossMargin", "operatingMargin", "profitMargin", "fcfMargin", "roe", "roic",
    )),
    ("Valuation & analyst estimates", (
        "pe", "forwardPe", "pFcf", "analystTarget", "analystTargetLow",
        "analystTargetHigh", "analystCount", "analystConsensus",
    )),
    ("Financial health & shareholder returns", (
        "wacc", "currentRatio", "debtEbitda", "zScore", "fScore",
        "fcfYield", "dividendYield", "buybackYield", "shareholderYield",
    )),
]


def _metric_value(key: str, value: Any) -> str:
    if value is None:
        return "Not available"
    if key in {"revenue", "fcf", "unleveredFcf", "netCash"}:
        return _money(value, compact=True)
    if key in {
        "price", "eps", "bookValuePerShare", "fcfPerShare",
        "analystTarget", "analystTargetLow", "analystTargetHigh",
        "lynchFairValue", "grahamNumber",
    }:
        return _money(value)
    if key == "shares":
        number = _number(value)
        return f"{number / 1_000_000_000:.2f}B shares" if number is not None else "Not available"
    if key in {
        "fcfYield", "grossMargin", "operatingMargin", "profitMargin", "fcfMargin",
        "roe", "roic", "wacc", "dividendYield", "buybackYield", "shareholderYield",
        "revenueForecastGrowthPct", "epsForecastGrowthPct",
    }:
        return _percent(value, fraction=True)
    if key in {"historicalRevenueCagr", "historicalFcfCagr"}:
        return _percent(value, fraction=True)
    if key in {"pe", "forwardPe", "pFcf", "currentRatio", "debtEbitda"}:
        number = _number(value)
        return f"{number:.2f}x" if number is not None else "Not available"
    if key == "fScore":
        number = _number(value)
        return f"{number:.0f} / 9" if number is not None else "Not available"
    if key == "analystCount":
        number = _number(value)
        return f"{number:.0f} analysts" if number is not None else "Not available"
    number = _number(value)
    return f"{number:,.2f}" if number is not None else _escape(value)


def _metric_table(metrics: Mapping[str, Any], keys: Sequence[str]) -> str:
    rows = [
        f"<tr><th>{_escape(_METRIC_LABELS.get(key, key))}</th>"
        f"<td>{_escape(_metric_value(key, metrics[key]))}</td></tr>"
        for key in keys if key in metrics
    ]
    return _table(("Measure", "Reading"), rows, "No values available for this group.")


def _score_bars(quality: Mapping[str, Any]) -> str:
    labels = (
        ("overall", "Overall business quality"),
        ("growth", "Growth"),
        ("profitability", "Profitability"),
        ("financialHealth", "Financial health"),
        ("shareholderReturn", "Shareholder returns"),
    )
    rows = []
    for key, label in labels:
        score = _number(quality.get(key))
        if score is None:
            continue
        width = max(0.0, min(100.0, score))
        rows.append(
            '<div class="score-row"><div class="score-heading">'
            f"<span>{_escape(label)}</span><strong>{score:.1f} / 100</strong></div>"
            f'<div class="score-track"><span style="width:{width:.1f}%"></span></div></div>'
        )
    return "".join(rows) or '<p class="muted">Business quality scores are not available.</p>'


def _business_table(rows: Sequence[Mapping[str, Any]], mode: str) -> str:
    rendered = []
    for item in rows:
        name = _escape(item.get("name") or "Other")
        if mode in {"segment", "geography"}:
            rendered.append(
                f"<tr><td>{name}</td><td>{_escape(item.get('date') or 'Not available')}</td>"
                f"<td>{_escape(_percent(item.get('mixPct')))}</td>"
                f"<td>{_escape(_money(item.get('value'), compact=True))}</td>"
                f"<td>{_escape(_percent(item.get('growthPct'), signed=True))}</td>"
                f"<td>{_escape(_money(item.get('change'), compact=True))}</td></tr>"
            )
        elif mode == "margin":
            margin_change = _number(item.get("change"))
            margin_change_text = (
                f"{margin_change:+.2f} percentage points"
                if margin_change is not None else "Not available"
            )
            rendered.append(
                f"<tr><td>{name}</td><td>{_escape(item.get('date') or 'Not available')}</td>"
                f"<td>{_escape(_percent(item.get('value')))}</td>"
                f"<td>{_escape(_percent(item.get('growthPct'), signed=True))}</td>"
                f"<td>{_escape(margin_change_text)}</td></tr>"
            )
        else:
            rendered.append(
                f"<tr><td>{name}</td><td>{_escape(item.get('date') or 'Not available')}</td>"
                f"<td>{_escape(_money(item.get('value'), compact=True))}</td>"
                f"<td>{_escape(_percent(item.get('growthPct'), signed=True))}</td>"
                f"<td>{_escape(_money(item.get('change'), compact=True))}</td></tr>"
            )
    if mode in {"segment", "geography"}:
        return _table(("Area", "Report date", "Share of sales", "Sales", "Year-over-year change", "Change in sales"), rendered)
    if mode == "margin":
        return _table(("Business line", "Report date", "Gross margin", "Year-over-year change", "Margin change"), rendered)
    return _table(("Expense category", "Report date", "Amount", "Year-over-year change", "Change in spend"), rendered)


def _business_context_html(context: Mapping[str, Any]) -> str:
    details = []
    sections = (
        ("segment", "Sales by product or service", "latestSegmentMix"),
        ("geography", "Sales by region", "latestGeographicMix"),
        ("margin", "Gross margins by business line", "latestGrossMargins"),
        ("expense", "Operating expense growth", "latestOperatingExpenseGrowth"),
    )
    for mode, title, key in sections:
        values = context.get(key) or []
        if values:
            details.append(_detail_block(title, _business_table(values, mode), f"{len(values)} items"))
    if not details:
        return '<p class="muted">Business segment detail was not available.</p>'
    return '<div class="detail-stack">' + "".join(details) + "</div>"


def _fundamental_html(analysis: Mapping[str, Any]) -> str:
    fundamental = analysis.get("fundamental") or {}
    quality = fundamental.get("quality") or {}
    metrics = fundamental.get("metrics") or {}
    business = fundamental.get("businessContext") or {}
    attractiveness = fundamental.get("attractiveness") or {}
    quality_html = (
        '<div class="quality-layout"><div>'
        f'<p class="quality-summary">Model rating: <strong>{_escape(quality.get("label") or "Not available")}</strong></p>'
        f'{_score_bars(quality)}</div>'
        '<div class="business-note">'
        f'<strong>Business attractiveness: {_format_price(attractiveness.get("score"))} / 100</strong>'
        f'<p>{_escape(attractiveness.get("label") or "Not available")}. '
        f'Unadjusted score: {_format_price(attractiveness.get("rawScore"))} / 100. '
        "This is separate from the component quality measures.</p>"
        "</div></div>"
    )
    metric_details = []
    shown = set()
    for title, keys in _METRIC_GROUPS:
        present = [key for key in keys if key in metrics]
        shown.update(present)
        if present:
            metric_details.append(_detail_block(title, _metric_table(metrics, present), f"{len(present)} measures"))
    reference_keys = {"lynchFairValue", "grahamNumber"}
    remaining = [key for key in metrics if key not in shown and key not in reference_keys]
    if remaining:
        metric_details.append(_detail_block("Other reported measures", _metric_table(metrics, remaining), f"{len(remaining)} measures"))

    reference = fundamental.get("referenceFormulas") or {}
    reference_rows = []
    for key, title in (("lynchFairValue", "Peter Lynch reference estimate"), ("grahamNumber", "Graham Number reference estimate")):
        value = reference.get(key, metrics.get(key))
        if value is not None:
            reference_rows.append(f"<tr><th>{title}</th><td>{_escape(_money(value))}</td></tr>")
    if reference_rows:
        reference_content = _table(("Reference only", "Estimate"), reference_rows)
        if reference.get("note"):
            reference_content += f'<p class="muted">{_escape(reference["note"])}</p>'
        metric_details.append(_detail_block("Additional reference estimates", reference_content, "not included in fair value"))

    largest_segment = business.get("largestSegmentConcentrationPct")
    weighted_growth = business.get("weightedSegmentGrowthPct")
    context_cards = [
        _metric_card("Largest product share", _percent(largest_segment), "Shows how much sales depend on one segment"),
        _metric_card("Weighted segment growth", _percent(weighted_growth, signed=True), "Growth weighted by each segment's share"),
    ]
    available_note = "" if fundamental.get("available", True) else '<p class="notice warning">Fundamental data was unavailable for this analysis.</p>'
    return (
        '<section class="analysis-section" id="business">'
        '<div class="section-kicker">Company fundamentals</div><h2>Business quality &amp; financials</h2>'
        f'{available_note}{quality_html}'
        f'<h3 class="subheading">Business mix</h3><div class="grid insight-grid">{"".join(context_cards)}</div>'
        f'{_business_context_html(business)}'
        '<h3 class="subheading">Financial measures</h3>'
        '<p class="muted">Open a category to see what the company reports. Missing values are labeled “Not available.”</p>'
        f'<div class="detail-stack">{"".join(metric_details)}</div>'
        "</section>"
    )


_METHOD_LABELS = {
    "dcf": "Cash-flow valuation (DCF)",
    "earningsMultiple": "Forward earnings comparison",
    "fcfMultiple": "Free cash flow comparison",
    "analystConsensus": "Analyst estimates",
}

_ASSUMPTION_LABELS = {
    "startingFcf": "Starting free cash flow",
    "waccPct": "Discount rate",
    "initialGrowthPct": "Early growth assumption",
    "terminalGrowthPct": "Long-term growth assumption",
    "forecastYears": "Forecast period",
    "forwardEps": "Forecast earnings per share",
    "targetPeLow": "Low earnings multiple",
    "targetPeBase": "Base earnings multiple",
    "targetPeHigh": "High earnings multiple",
    "fcfPerShare": "Free cash flow per share",
    "multipleLow": "Low cash-flow multiple",
    "multipleBase": "Base cash-flow multiple",
    "multipleHigh": "High cash-flow multiple",
    "analystCount": "Analyst estimates included",
    "consensus": "Analyst consensus",
}


def _assumption_value(key: str, value: Any) -> str:
    if value is None:
        return "Not available"
    if key in {"startingFcf"}:
        return _money(value, compact=True)
    if key in {"forwardEps", "fcfPerShare"}:
        return _money(value)
    if key in {"waccPct", "initialGrowthPct", "terminalGrowthPct"}:
        return _percent(value)
    if key in {"targetPeLow", "targetPeBase", "targetPeHigh", "multipleLow", "multipleBase", "multipleHigh"}:
        number = _number(value)
        return f"{number:.2f}x" if number is not None else "Not available"
    if key == "analystCount":
        number = _number(value)
        return f"{number:.0f} analysts" if number is not None else "Not available"
    if key == "forecastYears":
        number = _number(value)
        return f"{number:.0f} years" if number is not None else "Not available"
    return _escape(value)


def _method_assumptions(assumptions: Mapping[str, Any]) -> str:
    rows = [
        f"<tr><th>{_escape(_ASSUMPTION_LABELS.get(key, key))}</th>"
        f"<td>{_escape(_assumption_value(key, value))}</td></tr>"
        for key, value in assumptions.items()
    ]
    return _table(("Assumption", "Value"), rows, "No method assumptions were reported.")


def _valuation_methods_html(methods: Mapping[str, Any]) -> str:
    rows = []
    for key, item in methods.items():
        label = _METHOD_LABELS.get(key, re.sub(r"(?<!^)(?=[A-Z])", " ", key).replace("_", " ").title())
        assumptions = item.get("assumptions") or {}
        assumption_detail = _detail_block(
            "How this estimate is calculated",
            _method_assumptions(assumptions),
        ) if assumptions else '<span class="muted">No assumptions listed</span>'
        note = f'<div class="method-note">{_escape(item["note"])}</div>' if item.get("note") else ""
        flags = item.get("assumptionFlags") or []
        fallback_note = (
            '<span class="notice warning">Uses a generic fallback assumption.</span>'
            if item.get("usesGenericFallback") else ""
        )
        rows.append(
            "<tr>"
            f"<td><strong>{_escape(label)}</strong>{note}{fallback_note}"
            f"{_bullet_list(flags, '') if flags else ''}</td>"
            f"<td>{_escape(_money(item.get('low')))} to {_escape(_money(item.get('high')))}"
            f"<div class=\"muted\">Base: {_escape(_money(item.get('base')))}</div></td>"
            f"<td>{_escape(_percent(item.get('effectiveWeight', item.get('weight')), fraction=True))}"
            f"<div class=\"muted\">Starting weight: {_escape(_percent(item.get('baseWeight'), fraction=True))}</div></td>"
            f"<td>{_escape(_format_price(item.get('confidence')))} / 100</td>"
            f"<td>{assumption_detail}</td></tr>"
        )
    return _table(
        ("Valuation approach", "Estimated value range", "Model weight", "Confidence", "Details"),
        rows,
        "No valuation methods were available.",
    )


def _timeframe_rows(analysis: Mapping[str, Any]) -> str:
    rows = []
    for key, item in (analysis.get("timeframes") or {}).items():
        fair_value = item.get("fairValue") or {}
        weights = item.get("weights") or {}
        requested = item.get("requestedWeights") or {}
        reliability = item.get("reliability") or {}
        technical_weight = _percent(weights.get("technical"), fraction=True)
        fundamental_weight = _percent(weights.get("fundamental"), fraction=True)
        requested_text = (
            f"Requested blend: {_percent(requested.get('technical'), fraction=True)} technical / "
            f"{_percent(requested.get('fundamental'), fraction=True)} fundamental"
        )
        reliability_text = (
            f"Input reliability: {_percent(reliability.get('technical'), fraction=True)} technical / "
            f"{_percent(reliability.get('fundamental'), fraction=True)} fundamental"
        )
        gap = item.get("expectedGapPct")
        gap_class = "positive" if (_number(gap) or 0) >= 0 else "negative"
        rows.append(
            "<tr>"
            f"<td><strong>{_escape(item.get('label') or str(key).upper())}</strong>"
            f"<div class=\"muted\">{_escape(item.get('horizonTradingDays', 'Not available'))} trading days</div></td>"
            f"<td><span class=\"stance-pill\">{_escape(item.get('stance') or 'Not available')}</span>"
            f"<div class=\"muted\">Score: {_format_price(item.get('compositeScore'))} / 100 · "
            f"{_escape(item.get('valuation') or 'Valuation unavailable')}</div></td>"
            f"<td>{_escape(item.get('technicalTrend') or 'Not available')}"
            f"<div class=\"muted\">Technical score: {_format_price(item.get('technicalScore'))} / 100</div></td>"
            f"<td>{_escape(_money(fair_value.get('low')))} to {_escape(_money(fair_value.get('high')))}"
            f"<div class=\"muted\">Base estimate: {_escape(_money(fair_value.get('base')))}</div></td>"
            f"<td class=\"{gap_class}\">{_escape(_percent(gap, signed=True))}</td>"
            f"<td>{_escape(_format_price(item.get('confidence')))} / 100"
            f"<div class=\"muted\">{_escape(item.get('confidenceLabel') or '')}</div></td>"
            f"<td>{_escape(technical_weight)} technical / {_escape(fundamental_weight)} fundamental"
            f"<div class=\"muted\">Model-adjusted blend</div>"
            f"<div class=\"muted\">{_escape(requested_text)}<br>{_escape(reliability_text)}</div></td>"
            "</tr>"
        )
    return _table(
        ("Time horizon", "Overall model view", "Technical trend", "Blended estimate", "Gap vs. price", "Confidence", "Analysis blend"),
        rows,
        "No timeframe projections are available.",
    )


def _valuation_html(analysis: Mapping[str, Any]) -> str:
    fair_value = analysis.get("fairValue") or {}
    fundamental_value = fair_value.get("fundamental") or {}
    fundamental = analysis.get("fundamental") or {}
    consensus = fundamental.get("consensus") or fundamental_value
    current_price = (analysis.get("executiveSummary") or {}).get("currentPrice")
    methods = fundamental.get("methods") or {}
    dispersion = consensus.get("methodDispersionPct")
    cards = [
        _metric_card("Current share price", _money(current_price), "Latest price used in this analysis"),
        _metric_card(
            "Fundamental estimate",
            _money(fundamental_value.get("base")),
            f"Range: {_money(fundamental_value.get('low'))} to {_money(fundamental_value.get('high'))}",
        ),
        _metric_card(
            "Estimated gap to base value",
            _percent(fundamental_value.get("upsideToBasePct"), signed=True),
            fundamental_value.get("valuation") or "Model valuation view",
        ),
        _metric_card(
            "Valuation method spread",
            _percent(dispersion),
            "A wider spread means the methods disagree more",
        ),
    ]
    confidence = (
        f'<p class="muted">Confidence: {_format_price(fundamental_value.get("confidence"))} / 100'
        f' ({_escape(fundamental_value.get("confidenceLabel") or "not rated")}). Estimates vary by method, '
        "so read the range rather than treating the base estimate as precise.</p>"
    )
    return (
        '<section class="analysis-section" id="valuation">'
        '<div class="section-kicker">Estimated values, not promises</div><h2>Valuation &amp; time horizons</h2>'
        f'<div class="grid insight-grid">{"".join(cards)}</div>{confidence}'
        f'{_detail_block("How the fundamental estimate is built", _valuation_methods_html(methods), f"{len(methods)} methods")}'
        '<h3 class="subheading">How the model view changes over time</h3>'
        '<p class="muted">Shorter horizons lean more on technical signals; longer horizons lean more on business fundamentals. '
        "The model adjusts these weights based on the reliability of the available data.</p>"
        f'{_timeframe_rows(analysis)}'
        "</section>"
    )


_INDICATOR_LABELS = {
    "sma20": "20-session moving average",
    "sma50": "50-session moving average",
    "sma200": "200-session moving average",
    "vwap20": "20-session volume-weighted average",
    "vwap60": "60-session volume-weighted average",
    "rsi14": "RSI momentum (14 sessions)",
    "macd": "MACD momentum",
    "macdSignal": "MACD signal line",
    "atr14": "Average daily price range (ATR)",
    "annualizedVolatility": "Annualized volatility",
    "tenkan": "Ichimoku conversion line",
    "kijun": "Ichimoku base line",
    "spanA": "Ichimoku cloud line A",
    "spanB": "Ichimoku cloud line B",
}

_SIGNAL_LABELS = {
    "goldenCross": "Long-term moving averages crossed upward",
    "deathCross": "Long-term moving averages crossed downward",
    "sma20BullishCross": "Price crossed above the 20-session average",
    "sma20BearishCross": "Price crossed below the 20-session average",
    "rsiRecoveryAbove30": "RSI recovered above 30",
    "rsiCrossAbove50": "RSI crossed above its midpoint",
    "rsiCrossBelow50": "RSI crossed below its midpoint",
    "macdBullishCross": "MACD crossed above its signal line",
    "macdBearishCross": "MACD crossed below its signal line",
    "macdHistogramIncreasing": "MACD momentum is increasing",
    "macdHistogramDecreasing": "MACD momentum is decreasing",
    "macdZeroBullishCross": "MACD crossed above zero",
    "macdZeroBearishCross": "MACD crossed below zero",
    "atrExpansion": "Recent price range is expanding",
    "atrContraction": "Recent price range is contracting",
    "volatilityExpansion": "Volatility is increasing",
    "volatilityContraction": "Volatility is decreasing",
}


def _indicator_value(key: str, value: Any) -> str:
    if value is None:
        return "Not enough history"
    if key == "annualizedVolatility":
        return _percent(value, fraction=True)
    if key == "rsi14":
        return _format_price(value) + " / 100"
    return _money(value)


def _indicator_grid(indicators: Mapping[str, Any]) -> str:
    entries = []
    for key, value in indicators.items():
        if key == "ichimoku" and isinstance(value, Mapping):
            entries.extend(
                (child_key, child_value)
                for child_key, child_value in value.items()
            )
        else:
            entries.append((key, value))
    cards = []
    for key, value in entries:
        label = _INDICATOR_LABELS.get(
            key, re.sub(r"(?<!^)(?=[A-Z])", " ", key).replace("_", " ").title()
        )
        cards.append(
            f'<div class="indicator-card"><span>{_escape(label)}</span>'
            f'<strong>{_escape(_indicator_value(key, value))}</strong></div>'
        )
    return '<div class="indicator-grid">' + "".join(cards) + "</div>" if cards else '<p class="muted">No technical indicators available.</p>'


def _signal_details(signals: Mapping[str, Any]) -> str:
    if not signals:
        return '<p class="muted">No signal checks were reported.</p>'
    rows = []
    for key, active in signals.items():
        label = _SIGNAL_LABELS.get(
            key, re.sub(r"(?<!^)(?=[A-Z])", " ", key).replace("_", " ").title()
        )
        status = "Detected" if active else "Not detected"
        status_class = "signal-active" if active else "signal-inactive"
        rows.append(
            f'<div class="signal-row"><span>{_escape(label)}</span>'
            f'<strong class="{status_class}">{status}</strong></div>'
        )
    return '<div class="signal-grid">' + "".join(rows) + "</div>"


def _levels_text(values: Sequence[Any]) -> str:
    levels = [_money(value) for value in values if _number(value) is not None]
    return ", ".join(_escape(value) for value in levels) if levels else "Not available"


def _technical_card(key: str, item: Mapping[str, Any]) -> str:
    indicators = item.get("indicators") or {}
    levels = item.get("levels") or {}
    projection = item.get("projection") or {}
    reference = item.get("technicalReference") or {}
    sampling = item.get("sampling") or {}
    regression = item.get("regression") or {}
    quality = item.get("dataQuality") or {}
    label = item.get("label") or key.upper()
    score = _format_price(item.get("technicalScore"))
    confidence = _format_price(item.get("confidence"))
    active = [
        _SIGNAL_LABELS.get(signal, signal)
        for signal, is_active in (item.get("signals") or {}).items()
        if is_active
    ]
    signal_summary = (
        f'<div class="active-signals"><strong>Signals detected:</strong> '
        f'{_escape("; ".join(active))}</div>'
        if active else '<div class="active-signals muted">No notable signals were detected.</div>'
    )
    projection_rows = [
        f"<tr><th>Downside scenario</th><td>{_escape(_money(projection.get('bear')))}</td></tr>",
        f"<tr><th>Base scenario</th><td>{_escape(_money(projection.get('base')))}</td></tr>",
        f"<tr><th>Upside scenario</th><td>{_escape(_money(projection.get('bull')))}</td></tr>",
        f"<tr><th>Base scenario change</th><td>{_escape(_percent(projection.get('baseReturnPct'), signed=True))}</td></tr>",
    ]
    level_rows = [
        f"<tr><th>Support levels</th><td>{_levels_text(levels.get('supports') or [])}</td></tr>",
        f"<tr><th>Resistance levels</th><td>{_levels_text(levels.get('resistances') or [])}</td></tr>",
        f"<tr><th>Technical reference range</th><td>{_escape(_money(reference.get('low')))} to "
        f"{_escape(_money(reference.get('high')))}; base {_escape(_money(reference.get('base')))}, "
        f"equilibrium {_escape(_money(reference.get('equilibrium')))}</td></tr>",
    ]
    regression_rows = [
        f"<tr><th>Regression trend estimate (annualized)</th><td>{_escape(_percent(regression.get('annualizedTrend'), signed=True, fraction=True))}</td></tr>",
        f"<tr><th>Trend model fit</th><td>{_escape(_percent(regression.get('r2'), fraction=True))}</td></tr>",
        f"<tr><th>Regression reference price</th><td>{_escape(_money(regression.get('target')))}</td></tr>",
    ]
    data_warnings = quality.get("warnings") or []
    warning_html = _bullet_list(data_warnings, "No timeframe-specific data warnings.")
    timeframe_history = quality.get("priceHistory") or {}
    history_detail = _table(
        ("Data check", "Reading"),
        [
            f"<tr><th>Historical rows checked</th><td>{_escape(timeframe_history.get('rowCount', 'Not available'))}</td></tr>",
            f"<tr><th>Rows dropped during cleanup</th><td>{_escape(timeframe_history.get('droppedRows', 'Not available'))}</td></tr>",
            f"<tr><th>Data age</th><td>{_escape(timeframe_history.get('ageCalendarDays', 'Not available'))} calendar days</td></tr>",
            f"<tr><th>Freshness threshold</th><td>{_escape(timeframe_history.get('staleAfterDays', 'Not available'))} calendar days</td></tr>",
        ],
    )
    sampling_meaning = sampling.get("meaning") or ""
    sampling_note = (
        f'<p class="muted">{_escape(sampling_meaning)}</p>' if sampling_meaning else ""
    )
    signals = _detail_block("All signal checks", _signal_details(item.get("signals") or {}))
    content = (
        f'<p class="technical-description">{_escape(item.get("trend") or "Trend unavailable")}. '
        f'The technical score is {score} / 100, with {confidence} / 100 confidence.</p>'
        '<h4>Indicators</h4>'
        f'{_indicator_grid(indicators)}'
        '<div class="technical-columns">'
        f'<div><h4>Scenario estimates</h4>{_table(("Scenario", "Model estimate"), projection_rows)}'
        '<p class="muted">Scenarios are estimates based on historical data, not predictions or guarantees.</p></div>'
        f'<div><h4>Price reference levels</h4>{_table(("Level", "Reading"), level_rows)}'
        '<p class="muted">Support and resistance are reference zones, not guaranteed entry or exit prices.</p></div>'
        "</div>"
        '<h4>How the timeframe was calculated</h4>'
        '<div class="quality-facts">'
        f'<span><strong>Bars used</strong>{_escape(item.get("barsAnalyzed", sampling.get("barsAnalyzed", "Not available")))}</span>'
        f'<span><strong>Bar interval</strong>{_escape(sampling.get("barSizeTradingDays", item.get("barSizeTradingDays", "Not available")))} trading day(s)</span>'
        f'<span><strong>Price history</strong>{_escape(item.get("lookbackTradingDays", "Not available"))} trading days</span>'
        f'<span><strong>Indicator coverage</strong>{_escape(_percent(quality.get("indicatorCoveragePct")))}</span>'
        "</div>"
        f"{sampling_note}{_table(('Trend regression', 'Reading'), regression_rows)}"
        f'{signals}{_detail_block("Data notes", warning_html)}'
        f'{_detail_block("Price history checks", history_detail)}{signal_summary}'
    )
    return (
        '<details class="technical-card">'
        f'<summary><span class="horizon-name">{_escape(label)}</span>'
        f'<span class="technical-trend">{_escape(item.get("trend") or "Trend unavailable")}</span>'
        f'<span>Score <strong>{score}</strong> / 100</span>'
        f'<span>Confidence <strong>{confidence}</strong> / 100</span>'
        '<span class="expand-hint">Open timeframe details</span></summary>'
        f'<div class="technical-content">{content}</div></details>'
    )


def _technical_html(analysis: Mapping[str, Any]) -> str:
    technical = analysis.get("technical") or {}
    timeframes = technical.get("timeframes") or {}
    average_confidence = technical.get("averageConfidence")
    technical_score = technical.get("aggregateScore")
    data_points = technical.get("dataPoints")
    cards = [
        _metric_card("Overall technical score", f"{_format_price(technical_score)} / 100", "Combined across the requested horizons"),
        _metric_card("Technical confidence", f"{_format_price(average_confidence)} / 100", "Confidence in technical inputs"),
        _metric_card("Price observations", f"{data_points:,}" if isinstance(data_points, int) else "Not available", "Historical observations used"),
    ]
    if not technical.get("available", bool(timeframes)):
        return (
            '<section class="analysis-section" id="technical">'
            '<div class="section-kicker">Price history &amp; indicators</div><h2>Technical analysis</h2>'
            '<p class="notice warning">Technical analysis is unavailable for this report.</p></section>'
        )
    timeframe_cards = "".join(
        _technical_card(key, item) for key, item in timeframes.items()
    )
    start = _escape(_format_date(technical.get("from")))
    end = _escape(_format_date(technical.get("to")))
    return (
        '<section class="analysis-section" id="technical">'
        '<div class="section-kicker">Price history &amp; indicators</div><h2>Technical analysis</h2>'
        f'<div class="grid insight-grid">{ "".join(cards) }</div>'
        f'<p class="muted">Technical history covered {start} through {end}. '
        "Choose a horizon to see its indicators, levels, scenarios and signal checks.</p>"
        f'<div class="technical-stack">{timeframe_cards}</div></section>'
    )


def _data_quality_html(analysis: Mapping[str, Any]) -> str:
    quality = analysis.get("analysisQuality") or {}
    technical = analysis.get("technical") or {}
    price_quality = quality.get("priceHistory") or technical.get("priceDataQuality") or {}
    context = analysis.get("dataContext") or {}
    report_quality = (analysis.get("professionalReport") or {}).get("dataQuality") or {}
    fundamentals = analysis.get("fundamental") or {}
    input_quality = fundamentals.get("inputQuality") or report_quality.get("fundamentalInputQuality") or {}
    price_range = (
        f'{_escape(_format_date(price_quality.get("from")))} to '
        f'{_escape(_format_date(price_quality.get("to")))}'
    )
    cards = [
        _metric_card("Overall confidence", f"{_format_price(quality.get('overallConfidence'))} / 100", quality.get("confidenceLabel") or ""),
        _metric_card("Fundamental data coverage", _percent(input_quality.get("metricCoveragePct", quality.get("fundamentalMetricCoveragePct"))), "Core measures found in available financial data"),
        _metric_card("Price history quality", _percent(price_quality.get("qualityScore")), f"{price_quality.get('rowCount', 'Not available')} observations"),
    ]
    sources = []
    if context.get("generatedAt"):
        sources.append(f'<span><strong>Data collected</strong>{_escape(_format_date(context["generatedAt"]))}</span>')
    source_errors = context.get("sourceErrors") or []
    errors_html = (
        "<ul class=\"story-list\">" + "".join(
            f'<li><strong>{_escape(str(item.get("section") or "Data").replace("_", " ").replace("-", " ").title())}</strong>: Data is unavailable.</li>'
            for item in source_errors
        ) + "</ul>"
        if source_errors else '<p class="positive">No data retrieval errors were reported.</p>'
    )
    warnings = list(quality.get("warnings") or []) + list(price_quality.get("warnings") or [])
    for timeframe in (technical.get("timeframes") or {}).values():
        warnings.extend((timeframe.get("dataQuality") or {}).get("warnings") or [])
    warning_items = list(dict.fromkeys(str(item) for item in warnings))
    warning_html = _bullet_list(warning_items, "No data quality warnings were reported.")
    sections = context.get("sectionsAvailable") or []
    requested_sections = context.get("sectionsRequested") or []
    section_list = ", ".join(
        re.sub(r"[_-]+", " ", str(name)).title() for name in sections
    ) or "No section details available"
    requested_section_list = ", ".join(
        re.sub(r"[_-]+", " ", str(name)).title() for name in requested_sections
    ) or "No request details available"
    core_available = input_quality.get("coreMetricsAvailable")
    core_expected = input_quality.get("coreMetricsExpected")
    fundamental_facts = [
        f"<tr><th>Core measures found</th><td>{_escape(core_available if core_available is not None else 'Not available')} / "
        f"{_escape(core_expected if core_expected is not None else 'Not available')}</td></tr>",
        f"<tr><th>Valuation approaches available</th><td>{_escape(input_quality.get('valuationMethodCount', 'Not available'))}</td></tr>",
        f"<tr><th>Confidence across valuation methods</th><td>{_escape(_format_price(input_quality.get('weightedMethodConfidence'))) } / 100</td></tr>",
    ]
    fallback_methods = input_quality.get("genericFallbackMethods") or []
    assumption_flags = input_quality.get("assumptionFlags") or []
    if fallback_methods:
        fundamental_facts.append(
            f"<tr><th>Methods using generic assumptions</th><td>{_escape(', '.join(map(str, fallback_methods)))}</td></tr>"
        )
    if assumption_flags:
        fundamental_facts.append(
            f"<tr><th>Assumption notes</th><td>{_escape('; '.join(map(str, assumption_flags)))}</td></tr>"
        )
    fundamental_quality_detail = _table(("Fundamental input check", "Reading"), fundamental_facts)
    price_status = (
        '<p class="notice warning">The historical price data is marked stale; interpret technical results cautiously.</p>'
        if price_quality.get("stale") else ""
    )
    return (
        '<section class="analysis-section" id="quality">'
        '<div class="section-kicker">How much to trust the inputs</div><h2>Data quality &amp; coverage</h2>'
        f'<div class="grid insight-grid">{ "".join(cards) }</div>'
        f'{price_status}<div class="quality-facts"><span><strong>Price history</strong>{price_range}</span>'
        f'<span><strong>Rows used</strong>{_escape(price_quality.get("rowCount", "Not available"))}</span>'
        f'<span><strong>Sections available</strong>{len(sections)}</span>'
        f'<span><strong>Section coverage</strong>{_escape(_percent(context.get("sectionCoveragePct")))}</span>'
        f'<span><strong>Data retrieval issues</strong>{_escape(context.get("sourceErrorCount", len(source_errors)))}</span></div>'
        f'<div class="source-facts">{"".join(sources)}</div>'
        f'{_detail_block("Fundamental input checks", fundamental_quality_detail)}'
        f'{_detail_block("Data sections used", f"<p><strong>Available:</strong> {_escape(section_list)}</p><p><strong>Requested:</strong> {_escape(requested_section_list)}</p>")}'
        f'{_detail_block("Warnings", warning_html)}'
        f'{_detail_block("Data retrieval issues", errors_html)}'
        "</section>"
    )


def _methodology_html(analysis: Mapping[str, Any]) -> str:
    methodology = analysis.get("methodology") or {}
    timeframes = methodology.get("timeframes") or []
    rows = []
    for item in timeframes:
        rows.append(
            f"<tr><td><strong>{_escape(item.get('label') or item.get('key') or 'Horizon')}</strong></td>"
            f"<td>{_escape(_percent(item.get('technical_weight'), fraction=True))}</td>"
            f"<td>{_escape(_percent(item.get('fundamental_weight'), fraction=True))}</td>"
            f"<td>{_escape(item.get('horizon_days', 'Not available'))} trading days</td>"
            f"<td>{_escape(item.get('lookback_days', 'Not available'))} trading days</td>"
            f"<td>{_escape(item.get('bar_days', 'Not available'))} trading day(s)</td></tr>"
        )
    parts = []
    for key, title in (
        ("fundamental", "Business valuation"),
        ("technical", "Technical scoring"),
        ("blending", "Combining the two views"),
    ):
        if methodology.get(key):
            parts.append(
                f'<div class="method-explainer"><h3>{title}</h3>'
                f'<p>{_escape(methodology[key])}</p></div>'
            )
    disclaimer = analysis.get("disclaimer") or (
        "Analytical model output, not personalized investment advice or a guaranteed forecast."
    )
    return (
        '<section class="analysis-section" id="methodology">'
        '<div class="section-kicker">How to read the report</div><h2>Method &amp; limitations</h2>'
        '<p class="muted">These are estimates based on available public data. Different assumptions or missing inputs can change the results.</p>'
        f'{_table(("Horizon", "Technical weight", "Fundamental weight", "Target horizon", "History lookback", "Price bar"), rows)}'
        f'<div class="method-explainers">{"".join(parts)}</div>'
        f'<p class="notice">{_escape(disclaimer)}</p></section>'
    )


def _analysis_results_html(
    analysis: Mapping[str, Any],
    chart: str,
    chart_details: str,
    stock_data: Optional[Mapping[str, Any]] = None,
) -> str:
    return (
        '<nav class="analysis-nav" aria-label="Analysis sections">'
        '<a href="#summary">Summary</a><a href="#valuation">Valuation</a>'
        '<a href="#company-data">Company profile</a><a href="#business">Business fundamentals</a>'
        '<a href="#technical">Technical detail</a><a href="#news">News</a>'
        '<a href="#quality">Data quality</a><a href="#methodology">Method &amp; limitations</a></nav>'
        + _overview_html(analysis)
        + '<section class="panel chart-panel"><h2>Price action, trend and momentum</h2>'
        + '<p class="muted">Candles show historical prices. The lines show common trend and volatility measures.</p>'
        + f'<div class="chart-wrap">{chart}</div></section>'
        + chart_details
        + _source_data_html(stock_data or {}, "stock")
        + _valuation_html(analysis)
        + _fundamental_html(analysis)
        + _technical_html(analysis)
        + _news_html(stock_data or {})
        + _data_quality_html(analysis)
        + _methodology_html(analysis)
    )


def _normalized_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _pick_value(sources: Sequence[Any], *keys: str) -> Any:
    wanted = {_normalized_key(key) for key in keys}
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        for key, value in source.items():
            if _normalized_key(key) in wanted and value not in (None, ""):
                if isinstance(value, Mapping):
                    value = next(
                        (value.get(wrapper) for wrapper in ("value", "displayValue", "label", "name")
                         if value.get(wrapper) not in (None, "")),
                        value,
                    )
                return value
    return None


def _company_name(data: Mapping[str, Any], ticker: str) -> str:
    company = data.get("company") or {}
    return str(_pick_value(
        (company.get("profile"), data.get("profile"), company.get("identity"),
         data.get("identity"), (data.get("overview") or {}).get("identity")),
        "nameFull", "companyName", "longName", "name", "shortName",
    ) or ticker)


def _period_label(row: Mapping[str, Any]) -> str:
    value = _pick_value((row,), "datekey", "fiscalYear", "period", "date")
    return str(value or "Latest")


def _statement_html(financials: Mapping[str, Any]) -> str:
    statement_specs = (
        ("incomeStatement", "Income statement", (
            ("Revenue", ("revenue", "totalRevenue")),
            ("Cost of revenue", ("costOfRevenue", "costOfGoodsSold")),
            ("Gross profit", ("grossProfit",)),
            ("Research and development", ("researchAndDevelopment", "researchDevelopment")),
            ("Selling, general and administrative", ("sellingGeneralAndAdministrative", "sgaExpense")),
            ("Operating expenses", ("operatingExpenses",)),
            ("Operating income", ("operatingIncome",)),
            ("Interest expense", ("interestExpense",)),
            ("Pretax income", ("pretaxIncome", "incomeBeforeTax")),
            ("Income tax", ("incomeTaxExpense", "incomeTax")),
            ("Net income", ("netIncome",)),
            ("EBITDA", ("ebitda",)),
            ("Earnings per share", ("eps", "epsdil", "earningsPerShare")),
            ("Shares outstanding", ("sharesOutstanding", "weightedAverageShares")),
        )),
        ("balanceSheet", "Balance sheet", (
            ("Total assets", ("totalAssets",)),
            ("Current assets", ("currentAssets",)),
            ("Cash and equivalents", ("cashAndCashEquivalents", "cash", "cashAndShortTermInvestments")),
            ("Accounts receivable", ("accountsReceivable", "receivables")),
            ("Inventory", ("inventory",)),
            ("Property and equipment", ("propertyPlantEquipment", "netPpe")),
            ("Current liabilities", ("currentLiabilities",)),
            ("Accounts payable", ("accountsPayable",)),
            ("Total liabilities", ("totalLiabilities",)),
            ("Shareholders' equity", ("totalEquity", "stockholdersEquity")),
            ("Total debt", ("totalDebt",)),
            ("Long-term debt", ("longTermDebt",)),
            ("Retained earnings", ("retainedEarnings",)),
        )),
        ("cashFlow", "Cash flow", (
            ("Net income", ("netIncome",)),
            ("Depreciation and amortization", ("depreciationAndAmortization", "depreciation")),
            ("Stock-based compensation", ("stockBasedCompensation",)),
            ("Change in working capital", ("changeInWorkingCapital",)),
            ("Operating cash flow", ("operatingCashFlow", "cashFromOperatingActivities")),
            ("Capital expenditures", ("capitalExpenditures",)),
            ("Free cash flow", ("freeCashFlow",)),
            ("Investing cash flow", ("investingCashFlow", "cashFromInvestingActivities")),
            ("Financing cash flow", ("financingCashFlow", "cashFromFinancingActivities")),
            ("Debt repaid", ("debtRepayment", "repaymentsOfDebt")),
            ("Dividends paid", ("dividendsPaid", "cashDividendsPaid")),
        )),
    )
    metadata_fields = {
        "date", "datekey", "fiscalyear", "fiscalquarter", "period", "currency",
        "symbol", "ticker", "reportdate", "calendaryear",
    }

    def normalized(value: Any) -> str:
        return re.sub(r"[^a-z0-9]", "", str(value).lower())

    def label_for(field: str, definitions: Sequence[Any]) -> str:
        for definition in definitions:
            if not isinstance(definition, Mapping):
                continue
            definition_id = _pick_value((definition,), "id", "key", "name")
            if definition_id is not None and normalized(definition_id) == normalized(field):
                title = _pick_value((definition,), "title", "label", "name")
                if title:
                    return str(title)
        return re.sub(r"(?<!^)(?=[A-Z])", " ", field).replace("_", " ").strip().title()

    panels = []
    for key, title, metrics in statement_specs:
        statement = financials.get(key) or {}
        rows = statement.get("rows") if isinstance(statement, Mapping) else []
        if not isinstance(rows, list):
            rows = []
        rows = [row for row in rows if isinstance(row, Mapping)]
        rows.sort(key=_period_label, reverse=True)
        rows = rows[:4]
        if not rows:
            continue
        columns = [_period_label(row) for row in rows]
        definitions = statement.get("rowDefinitions") or []
        metric_rows = list(metrics)
        used_fields = {normalized(alias) for _, aliases in metrics for alias in aliases}
        row_fields = list(dict.fromkeys(field for row in rows for field in row))
        for field in row_fields:
            field_key = normalized(field)
            if field_key in metadata_fields or field_key in used_fields:
                continue
            if not any(_number(row.get(field)) is not None for row in rows):
                continue
            metric_rows.append((label_for(str(field), definitions), (str(field),)))
            used_fields.add(field_key)

        table_rows = []
        for label, aliases in metric_rows[:30]:
            values = [_pick_value((row,), *aliases) for row in rows]
            if not any(value is not None for value in values):
                continue
            rendered_values = []
            for value in values:
                if value is None:
                    rendered_values.append("—")
                    continue
                metric_key = normalized(aliases[0])
                number = _number(value)
                if "margin" in metric_key or "growth" in metric_key or "yield" in metric_key or "ratio" in metric_key:
                    displayed = _fund_percent(value, fraction=number is not None and abs(number) <= 1)
                elif "shares" in metric_key:
                    displayed = f"{number:,.0f}" if number is not None else str(value)
                else:
                    displayed = _money(value, compact=metric_key not in {"eps", "epsdil", "earningspershare"})
                rendered_values.append(_escape(displayed))
            table_rows.append(
                f"<tr><td>{_escape(label)}</td>" + "".join(f"<td>{value}</td>" for value in rendered_values) + "</tr>"
            )
        if table_rows:
            panels.append(
                f'<div class="statement-panel" id="{_escape(key.replace("Statement", "-statements").replace("Sheet", "-sheet"))}"><h3>{_escape(title)}</h3>'
                + _table(("Measure", *columns), table_rows)
                + "</div>"
            )
    return "".join(panels) or '<p class="muted">Statement details are not available.</p>'


def _company_information_html(data: Mapping[str, Any]) -> str:
    company = data.get("company") or {}
    overview = data.get("overview") or {}
    profile = data.get("profile") or {}
    identity = data.get("identity") or overview.get("identity") or {}
    contact = company.get("contact") or {}
    details = company.get("details") or {}
    sources = (profile, company.get("profile"), identity, details, contact)
    headquarters = _pick_value(sources, "address", "headquarters", "location", "city")
    if isinstance(headquarters, Mapping):
        headquarters = ", ".join(str(value) for value in (
            _pick_value((headquarters,), "city"),
            _pick_value((headquarters,), "state", "region"),
            _pick_value((headquarters,), "country"),
        ) if value)
    facts = (
        ("Sector", _pick_value(sources, "sector")),
        ("Industry", _pick_value(sources, "industry")),
        ("Employees", _pick_value(sources, "employees", "employeeCount", "fullTimeEmployees")),
        ("Headquarters", headquarters),
        ("Exchange", _pick_value(sources, "exchange")),
        ("Founded", _pick_value(sources, "founded", "ipoDate")),
    )
    fact_cards = "".join(
        _metric_card(
            label,
            f"{int(_number(value)):,}" if label == "Employees" and _number(value) is not None
            else str(value),
        )
        for label, value in facts if value not in (None, "")
    )
    description = _pick_value(
        (company, company.get("profile"), profile, overview),
        "description", "businessSummary", "summary",
    )
    fundamentals = data.get("fundamentals") or overview.get("fundamentals") or {}
    financial_facts = (
        ("Market capitalization", ("marketCap",), True),
        ("Annual revenue", ("revenue",), True),
        ("Net income", ("netIncome",), True),
        ("Earnings per share", ("eps",), False),
        ("Price-to-earnings", ("peRatio", "pe"), False),
    )
    financial_cards = []
    for label, aliases, compact in financial_facts:
        value = _pick_value((fundamentals,), *aliases)
        if value is not None:
            formatted = _financial_value(value, compact=compact or label == "Earnings per share")
            if label == "Price-to-earnings":
                number = _number(value)
                formatted = f"{number:.2f}×" if number is not None else f"{value}×"
            financial_cards.append(_metric_card(label, formatted))
    financials = data.get("financials") or {}
    stats = (data.get("analysis") or {}).get("statistics") or {}
    stat_rows = []
    sections = stats.get("sections") or {}
    stat_labels = {
        "marketcap": "Market capitalization", "pe": "Price-to-earnings",
        "peratio": "Price-to-earnings", "forwardpe": "Forward price-to-earnings",
        "forwardperatio": "Forward price-to-earnings", "beta": "Beta",
        "dividendyield": "Dividend yield", "profitmargin": "Profit margin",
        "returnonequity": "Return on equity", "debttoequity": "Debt to equity",
    }
    if isinstance(sections, Mapping):
        for section in sections.values():
            items = section.get("items") or [] if isinstance(section, Mapping) else []
            for item in items:
                if not isinstance(item, Mapping):
                    continue
                key = _normalized_key(item.get("id") or item.get("name") or item.get("label"))
                value = item.get("value")
                if value is None or key not in stat_labels:
                    continue
                if key == "marketcap":
                    displayed = _financial_value(value, compact=True)
                elif key in {"dividendyield", "profitmargin", "returnonequity"}:
                    displayed = _fund_percent(value, decimals=2)
                elif key in {"pe", "peratio", "forwardpe", "forwardperatio"}:
                    displayed = f"{_format_price(value)}×"
                elif key == "beta":
                    displayed = _format_price(value)
                else:
                    displayed = _financial_value(value)
                stat_rows.append(f"<tr><td>{_escape(stat_labels[key])}</td><td>{_escape(displayed)}</td></tr>")
                if len(stat_rows) == 8:
                    break
            if len(stat_rows) == 8:
                break

    forecast = (data.get("analysis") or {}).get("forecast") or {}
    forecast_consensus = forecast.get("consensus") or {}
    price_targets = forecast.get("priceTargets") or {}
    consensus_rating = _pick_value((forecast_consensus,), "rating", "recommendation", "consensus")
    analyst_count = _pick_value((forecast_consensus,), "numberOfAnalysts", "analystCount", "numAnalysts")
    target_mean = _pick_value((price_targets, forecast_consensus), "targetMeanPrice", "averageTarget", "meanTarget", "targetMean", "priceTarget")
    target_low = _pick_value((price_targets,), "targetLowPrice", "lowTarget", "targetLow", "low")
    target_high = _pick_value((price_targets,), "targetHighPrice", "highTarget", "targetHigh", "high")
    current_price = _pick_value(
        (data.get("marketData") or {}, data.get("quote") or {}, data.get("fundamentals") or {}),
        "currentPrice", "regularMarketPrice", "price",
    )
    forecast_cards = []
    if consensus_rating is not None:
        forecast_cards.append(_metric_card("Analyst consensus", str(consensus_rating), "Reported analyst recommendation"))
    if analyst_count is not None:
        forecast_cards.append(_metric_card("Analysts covering", str(analyst_count), "Count reported by the source"))
    if target_mean is not None:
        note = "Average analyst price target"
        target_value = _number(target_mean)
        price_value = _number(current_price)
        if target_value is not None and price_value:
            note += f" · {_percent((target_value / price_value - 1) * 100, signed=True)} vs. current price"
        forecast_cards.append(_metric_card("Average price target", _money(target_mean), note))
    if target_low is not None or target_high is not None:
        forecast_cards.append(_metric_card(
            "Analyst target range",
            f"{_money(target_low)} – {_money(target_high)}",
            "Lowest to highest reported target",
        ))
    ratings_data = (data.get("analysis") or {}).get("ratings") or {}
    ratings = forecast.get("recentRatings") or ratings_data.get("ratings") or []
    rating_rows = []
    if isinstance(ratings, list):
        for item in ratings[:5]:
            if not isinstance(item, Mapping):
                continue
            firm = _pick_value((item,), "company", "analyst", "firm", "title") or "Analyst"
            action = _pick_value((item,), "action", "action_rt")
            rating = _pick_value((item,), "rating", "rating_new", "grade")
            action_rating = " · ".join(
                str(value) for value in (action, rating) if value not in (None, "")
            ) or "—"
            date = _pick_value((item,), "date", "published")
            if action_rating != "—" or date is not None:
                rating_rows.append(
                    f"<tr><td>{_escape(firm)}</td><td>{_escape(action_rating)}</td>"
                    f"<td>{_escape(date or '—')}</td></tr>"
                )
    forecast_html = (
        f'<div class="grid insight-grid forecast-cards">{"".join(forecast_cards)}</div>'
        if forecast_cards else '<p class="muted">Analyst consensus and price targets are not available.</p>'
    )
    forecast_html += (
        '<h4 class="subheading">Recent analyst rating changes</h4>'
        + _table(("Analyst or firm", "Action / rating", "Date"), rating_rows, "Recent analyst rating changes are not available.")
    )

    dividends = (data.get("market") or {}).get("dividends") or {}
    dividend_metrics = dividends.get("metrics") or dividends.get("summary") or dividends
    dividend_cards = []
    for label, aliases, kind in (
        ("Annual dividend yield", ("yield", "dividendYield"), "percent"),
        ("Annual dividend per share", ("annualDividend", "dividend", "annualRate"), "money"),
        ("Next ex-dividend date", ("exDividendDate", "exDate"), "text"),
        ("Payout ratio", ("payoutRatio",), "percent"),
    ):
        value = _pick_value((dividend_metrics, dividends), *aliases)
        if value is not None:
            if kind == "percent":
                number = _number(value)
                display = _fund_percent(value, fraction=number is not None and abs(number) < 1)
            elif kind == "money" and _number(value) is not None:
                display = _money(value)
            else:
                display = str(value)
            dividend_cards.append(_metric_card(label, display))
    history = dividends.get("history") or dividends.get("recentHistory") or []
    dividend_rows = []
    if isinstance(history, list):
        for item in history[:8]:
            if not isinstance(item, Mapping):
                continue
            date = _pick_value((item,), "exDate", "date", "paymentDate")
            amount = _pick_value((item,), "amount", "cashAmount", "dividend")
            if date or amount:
                dividend_rows.append(
                    f"<tr><td>{_escape(date or 'Date not reported')}</td><td>{_escape(_money(amount) if amount is not None else '—')}</td></tr>"
                )
    dividend_html = (
        f'<div class="grid insight-grid dividend-cards">{"".join(dividend_cards)}</div>'
        if dividend_cards else '<p class="muted">Dividend yield and payment details are not available.</p>'
    )
    dividend_html += (
        '<h4 class="subheading">Recent dividend history</h4>'
        + _table(("Ex-dividend date", "Cash amount per share"), dividend_rows, "Recent dividend payments are not available.")
    )

    business = data.get("businessMetrics") or {}
    business_overview = business.get("overview") or {}
    business_description = _pick_value((business_overview,), "description", "title")
    business_items = []
    for metric_set in (
        business_overview.get("trailingMetrics") or [],
        business_overview.get("annualMetrics") or [],
    ):
        for item in metric_set if isinstance(metric_set, list) else []:
            if not isinstance(item, Mapping):
                continue
            label = _pick_value((item,), "title", "name", "label", "metric")
            value = _pick_value((item,), "value", "latest", "currentValue")
            if label and value is not None:
                business_items.append(f"<tr><td>{_escape(label)}</td><td>{_escape(value)}</td></tr>")
                if len(business_items) >= 8:
                    break
        if len(business_items) >= 8:
            break

    financial_cards_html = "".join(financial_cards) or '<p class="muted">Key financial figures are not available.</p>'
    return (
        '<section class="analysis-section company-data" id="company-data">'
        '<div class="section-kicker">Company information</div><h2 id="overview">Company profile</h2>'
        + (f'<details class="company-description"><summary>Show company description</summary><p>{_escape(description)}</p></details>' if description else "")
        + (f'<div class="grid insight-grid">{fact_cards}</div>' if fact_cards else "")
        + '<div class="company-grid">'
        + f'<div class="story-card"><h3>Financial snapshot</h3><div class="grid insight-grid">{financial_cards_html}</div></div>'
        + f'<div class="story-card" id="statistics"><h3>Statistics</h3>{_table(("Measure", "Latest reported value"), stat_rows, "Key statistics are not available.")}</div>'
        + '</div><h3 class="subheading" id="financial-statements">Financial statements</h3>'
        + _statement_html(financials)
        + '<div class="company-grid">'
        + f'<div class="story-card" id="forecasts"><h3>Forecasts &amp; analyst ratings</h3>{forecast_html}</div>'
        + f'<div class="story-card" id="dividends"><h3>Dividends</h3>{dividend_html}</div>'
        + '</div><div class="story-card business-metrics" id="business-metrics"><h3>Business metrics</h3>'
        + (f'<p>{_escape(business_description)}</p>' if business_description else "")
        + _table(("Business measure", "Latest reported value"), business_items, "Business metrics are not available.")
        + '</div></section>'
    )


def _source_data_html(data: Mapping[str, Any], asset_type: str) -> str:
    if asset_type != "stock" or not data:
        return ""
    return _company_information_html(data)


def _news_html(data: Mapping[str, Any]) -> str:
    def clean_article_text(value: Any) -> str:
        text = str(value)
        for damaged, corrected in (
            ("\u00e2\u0080\u0098", "‘"),
            ("\u00e2\u0080\u0099", "’"),
            ("\u00e2\u0080\u009c", "“"),
            ("\u00e2\u0080\u009d", "”"),
            ("\u00e2\u0080\u0093", "–"),
            ("\u00e2\u0080\u0094", "—"),
        ):
            text = text.replace(damaged, corrected)
        return text

    articles = []
    for item in data.get("news") or []:
        if not isinstance(item, Mapping):
            continue
        url = str(item.get("url") or "").strip()
        if not url.lower().startswith(("https://", "http://")):
            continue
        title = clean_article_text(item.get("title") or item.get("headline") or "Article")
        published = clean_article_text(item.get("published") or item.get("date") or "Date not available")
        source = clean_article_text(item.get("source") or "Source not available")
        summary = item.get("summary") or item.get("text")
        if summary:
            summary_text = clean_article_text(summary).strip()
            if len(summary_text) > 220:
                summary_text = summary_text[:217].rsplit(" ", 1)[0] + "..."
            summary_html = f'<p>{_escape(summary_text)}</p>'
        else:
            summary_html = ""
        articles.append(
            '<a class="news-card" target="_blank" rel="noopener noreferrer" href="'
            + html.escape(url, quote=True) + '"><div class="news-meta">'
            + _escape(source) + ' <span>·</span> ' + _escape(published)
            + '</div><h3>' + _escape(title) + '</h3>' + summary_html
            + '<span class="news-link">Read article <span aria-hidden="true">↗</span></span></a>'
        )
    visible, additional = articles[:6], articles[6:]
    news_content = "".join(visible)
    if additional:
        news_content += (
            f'<details class="news-more"><summary>Show {len(additional)} more articles</summary>'
            + "".join(additional) + "</details>"
        )
    return (
        '<section class="analysis-section" id="news"><div class="section-kicker">Recent coverage</div>'
        '<h2>News &amp; headlines</h2>'
        + (f'<div class="news-grid">{news_content}</div>' if articles else
           '<p class="muted">No linked news articles are available.</p>')
        + "</section>"
    )


def write_stock_visual_report(
    ticker: str,
    price_data: Any,
    analysis: Mapping[str, Any],
    output_path: Path,
    stock_data: Optional[Mapping[str, Any]] = None,
) -> Path:
    """Write a self-contained HTML/SVG research dashboard without plotting packages."""
    pd = _pandas_module()
    frame = _prepare_prices(price_data)
    technical = analysis.get("technical") or {}
    timeframe_data = technical.get("timeframes") or {}
    levels = timeframe_data.get("1m") or next(iter(timeframe_data.values()), {})
    summary = analysis.get("executiveSummary") or {}
    indicators = (levels.get("indicators") or {})
    current = _number(frame["Close"].iloc[-1])
    previous_close = _number(frame["Close"].iloc[-2]) if len(frame) > 1 else current
    change_pct = ((current / previous_close - 1) * 100) if current is not None and previous_close else None
    escaped_ticker = html.escape(str(ticker))
    company_name = html.escape(_company_name(stock_data or {}, str(ticker)))
    title = f"MOMO Visual Research · {escaped_ticker}"
    generated = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M UTC")
    chart = _build_chart(frame, levels.get("levels") or {})
    current_color = _COLORS["up"] if change_pct is None or change_pct >= 0 else _COLORS["down"]
    score = summary.get("overallScore")
    stance = html.escape(str(summary.get("stance") or "—"))
    price_text = _money(current)
    rsi = _number(indicators.get("rsi14"))
    macd = _number(indicators.get("macd"))
    support = (levels.get("levels") or {}).get("supports") or []
    resistance = (levels.get("levels") or {}).get("resistances") or []
    moving_averages = " / ".join(
        _money(indicators.get(key)) for key in ("sma20", "sma50", "sma200")
    )
    chart_details = (
        '<section class="grid insight-grid chart-insights">'
        f'{_metric_card("Nearest support", _money(support[0] if support else None), "Model reference level")}'
        f'{_metric_card("Nearest resistance", _money(resistance[0] if resistance else None), "Model reference level")}'
        f'{_metric_card("Moving averages", moving_averages, "20 / 50 / 200 sessions")}'
        f'{_metric_card("Latest RSI", f"{_format_price(rsi)} / 100", "Momentum indicator")}'
        "</section>"
    )
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root{{color-scheme:dark;--bg:{_COLORS["background"]};--panel:{_COLORS["panel"]};--line:{_COLORS["grid"]};--text:{_COLORS["text"]};--muted:{_COLORS["muted"]};--up:{_COLORS["up"]};--down:{_COLORS["down"]}}}
*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(ellipse at 10% 0%,#172541 0,transparent 36%),var(--bg);color:var(--text);font:15px/1.5 Segoe UI,Arial,sans-serif}}
main{{max-width:1480px;margin:0 auto;padding:32px 26px 48px}}header{{display:flex;justify-content:space-between;align-items:flex-end;gap:20px;margin-bottom:22px}}h1{{font-size:clamp(28px,4vw,44px);line-height:1.05;margin:6px 0}}.eyebrow,.muted{{color:var(--muted)}}.eyebrow{{font-size:12px;letter-spacing:.16em;text-transform:uppercase}}.asof{{text-align:right;color:var(--muted);font-size:13px}}.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:13px;margin:16px 0}}.card,.panel{{background:linear-gradient(145deg,rgba(20,31,53,.98),rgba(14,22,39,.98));border:1px solid rgba(143,161,186,.14);border-radius:16px;box-shadow:0 12px 30px rgba(0,0,0,.18)}}.card{{padding:17px 19px;min-height:94px}}.label{{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}}.value{{font-size:23px;font-weight:650;margin-top:8px}}.positive{{color:var(--up)}}.negative{{color:var(--down)}}.panel{{padding:18px 20px;margin-top:15px;overflow:hidden}}.panel h2{{font-size:16px;margin:0 0 14px}}.chart-wrap{{overflow-x:auto}}svg{{display:block;width:100%;min-width:800px;height:auto}}svg text{{fill:{_COLORS["muted"]};font:11px Segoe UI,Arial,sans-serif}}table{{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}}th,td{{text-align:left;padding:12px 10px;border-bottom:1px solid rgba(143,161,186,.13)}}th{{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.08em}}td{{font-size:13px}}.foot{{color:var(--muted);font-size:12px;margin:20px 4px 0}}@media(max-width:800px){{main{{padding:22px 14px 36px}}.grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}header{{align-items:flex-start;flex-direction:column}}.asof{{text-align:left}}}}@media(max-width:480px){{.grid{{grid-template-columns:1fr 1fr;gap:9px}}.card{{padding:13px;min-height:82px}}.value{{font-size:19px}}}}
.analysis-nav{{position:sticky;top:0;z-index:5;display:flex;gap:8px;overflow-x:auto;margin:18px -8px 8px;padding:10px 8px;background:rgba(11,16,32,.92);backdrop-filter:blur(12px);border-bottom:1px solid var(--line)}}.analysis-nav a{{flex:0 0 auto;color:var(--text);text-decoration:none;padding:8px 12px;border:1px solid var(--line);border-radius:999px;font-size:13px}}.analysis-nav a:hover,.analysis-nav a:focus{{border-color:#fbbf24;color:#fbbf24}}.analysis-section{{scroll-margin-top:70px;margin:30px 0}}.section-kicker{{color:#fbbf24;font-size:11px;font-weight:700;letter-spacing:.13em;text-transform:uppercase}}.analysis-section h2{{font-size:clamp(23px,3vw,30px);margin:4px 0 10px}}.section-lead{{font-size:19px;max-width:920px;margin:10px 0 16px}}.analysis-copy{{max-width:1050px;margin:12px 0}}.insight-grid{{grid-template-columns:repeat(auto-fit,minmax(210px,1fr))}}.insight-card{{min-height:120px}}.card-note{{color:var(--muted);font-size:12px;margin-top:6px}}.gap-callout,.notice{{padding:13px 15px;border:1px solid rgba(143,161,186,.16);border-radius:10px;background:rgba(143,161,186,.06);margin:14px 0}}.story-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-top:18px}}.story-card,.business-note,.method-explainer{{padding:16px;border:1px solid rgba(143,161,186,.13);border-radius:12px;background:rgba(17,26,46,.65)}}.story-card h3,.method-explainer h3{{font-size:14px;margin:0 0 8px}}.story-list{{padding-left:20px;margin:8px 0}}.story-list li{{margin:7px 0}}.subheading{{font-size:17px;margin:24px 0 8px}}.detail-stack{{display:grid;gap:9px;margin:12px 0}}.detail-block{{border:1px solid rgba(143,161,186,.15);border-radius:12px;background:rgba(17,26,46,.5);overflow:hidden;margin:12px 0}}.detail-block>summary{{cursor:pointer;padding:13px 15px;font-weight:650;list-style-position:inside}}.detail-block>summary:hover{{color:#fbbf24}}.detail-note{{float:right;color:var(--muted);font-size:12px;font-weight:400}}.detail-content{{padding:0 14px 14px}}.table-wrap{{overflow-x:auto;margin:10px 0}}.table-wrap table{{min-width:560px}}th{{white-space:nowrap}}td{{vertical-align:top}}.stance-pill{{display:inline-block;padding:4px 9px;border-radius:999px;background:rgba(96,165,250,.12);border:1px solid rgba(96,165,250,.28);white-space:nowrap}}.quality-layout{{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(240px,.7fr);gap:20px;align-items:center;margin:14px 0 20px}}.quality-summary{{font-size:18px;margin:0 0 14px}}.business-note p{{color:var(--muted);margin-bottom:0}}.score-row{{margin:12px 0}}.score-heading{{display:flex;justify-content:space-between;gap:12px;font-size:13px;margin-bottom:5px}}.score-track{{height:8px;background:rgba(143,161,186,.14);border-radius:999px;overflow:hidden}}.score-track span{{display:block;height:100%;background:linear-gradient(90deg,#60a5fa,#2dd4a7);border-radius:inherit}}.technical-stack{{display:grid;gap:10px;margin-top:16px}}.technical-card{{border:1px solid rgba(143,161,186,.15);border-radius:14px;background:rgba(17,26,46,.58);overflow:hidden}}.technical-card>summary{{cursor:pointer;display:grid;grid-template-columns:minmax(170px,1.3fr) repeat(3,minmax(125px,1fr)) minmax(145px,1fr);align-items:center;gap:12px;padding:15px 17px;list-style-position:inside}}.technical-card>summary:hover{{background:rgba(143,161,186,.05)}}.horizon-name{{font-weight:700}}.technical-trend{{color:var(--up)}}.expand-hint{{color:var(--muted);font-size:12px;text-align:right}}.technical-content{{padding:2px 18px 18px}}.technical-description{{color:var(--muted)}}.technical-content h4{{font-size:14px;margin:18px 0 8px}}.indicator-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:8px}}.indicator-card{{display:flex;justify-content:space-between;gap:10px;padding:10px 12px;border:1px solid rgba(143,161,186,.11);border-radius:9px;background:rgba(11,16,32,.45)}}.indicator-card span{{color:var(--muted);font-size:12px}}.indicator-card strong{{text-align:right;font-size:13px}}.technical-columns{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}}.signal-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}}.signal-row{{display:flex;justify-content:space-between;gap:12px;padding:9px 11px;border:1px solid rgba(143,161,186,.1);border-radius:8px;font-size:12px}}.signal-active{{color:var(--up)}}.signal-inactive{{color:var(--muted);font-weight:400}}.active-signals{{padding:12px 14px;margin:14px 0;background:rgba(45,212,167,.07);border-radius:9px;font-size:13px}}.quality-facts,.source-facts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:9px;margin:14px 0}}.quality-facts span,.source-facts span{{display:flex;flex-direction:column;gap:4px;padding:12px;border:1px solid rgba(143,161,186,.12);border-radius:9px;font-size:13px;overflow-wrap:anywhere}}.quality-facts strong,.source-facts strong{{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.05em}}.source-facts{{grid-template-columns:repeat(auto-fit,minmax(220px,1fr))}}.warning{{border-color:rgba(251,191,36,.25);background:rgba(251,191,36,.07)}}.method-explainers{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:18px 0}}.method-explainer p{{color:var(--muted);font-size:13px;margin:0}}.chart-panel{{margin-top:18px}}@media(max-width:900px){{.technical-card>summary{{grid-template-columns:repeat(2,minmax(0,1fr))}}.technical-trend{{text-align:right}}.expand-hint{{text-align:left}}.method-explainers{{grid-template-columns:1fr}}}}@media(max-width:680px){{main{{padding:18px 12px 32px}}.story-grid,.quality-layout,.technical-columns{{grid-template-columns:1fr}}.technical-card>summary{{grid-template-columns:1fr 1fr;gap:7px;padding:12px}}.horizon-name{{grid-column:1/-1}}.technical-trend{{text-align:left}}.expand-hint{{grid-column:1/-1}}.detail-note{{float:none;display:block;margin:4px 0 0}}.section-lead{{font-size:17px}}}}@media(max-width:480px){{.analysis-nav a{{font-size:12px;padding:7px 10px}}.story-card{{padding:13px}}.signal-grid{{grid-template-columns:1fr}}.chart-insights{{grid-template-columns:repeat(2,minmax(0,1fr))}}
</style>
<style>
.company-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin:14px 0}}
.company-data .story-card{{min-width:0}}.company-data .story-card .grid{{margin:8px 0}}
.company-data .story-card .card{{min-height:84px}}.statement-panel{{margin:12px 0;padding:16px;border:1px solid rgba(143,161,186,.13);border-radius:12px;background:rgba(17,26,46,.55)}}
.company-data .table-wrap table{{min-width:0}}
.statement-panel h3,.company-data .story-card h3{{font-size:15px;margin:0 0 8px}}
.company-description{{max-width:920px;margin:10px 0 16px;border:1px solid rgba(143,161,186,.13);border-radius:10px;background:rgba(17,26,46,.45)}}
.company-description>summary{{cursor:pointer;padding:9px 12px;color:var(--muted);font-size:13px;font-weight:600}}
.company-description>summary:hover{{color:#fbbf24}}.company-description p{{margin:0;padding:0 12px 12px;color:var(--muted);font-size:14px;line-height:1.55}}
.forecast-cards,.dividend-cards{{grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:9px}}
.forecast-cards .card,.dividend-cards .card{{padding:13px;min-height:76px}}
.forecast-cards .value,.dividend-cards .value{{font-size:18px}}
.company-data .subheading{{font-size:14px;margin:18px 0 6px}}
.news-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin:14px 0}}
.news-card{{display:flex;flex-direction:column;min-width:0;padding:16px 18px;border:1px solid rgba(143,161,186,.14);border-radius:14px;background:linear-gradient(145deg,rgba(20,31,53,.98),rgba(14,22,39,.98));color:var(--text);text-decoration:none;transition:border-color .15s,transform .15s}}
.news-card:hover,.news-card:focus{{border-color:#60a5fa;transform:translateY(-2px);outline:none}}
.news-meta{{color:var(--muted);font-size:12px}}.news-card h3{{font-size:16px;line-height:1.35;margin:8px 0}}.news-card p{{color:var(--muted);font-size:13px;margin:0 0 10px;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}}.news-link{{color:#8ac5ff;font-size:12px;font-weight:650;margin-top:auto;padding-top:10px}}
.news-more{{grid-column:1/-1;border:1px solid rgba(143,161,186,.15);border-radius:12px;padding:12px 14px;background:rgba(17,26,46,.5)}}
.news-more>summary{{cursor:pointer;font-weight:650}}.news-more .news-card{{margin-top:10px}}
@media(max-width:680px){{.company-grid,.news-grid{{grid-template-columns:1fr}}}}
</style>
</head>
<body><main>
<header><div><div class="eyebrow">MOMO · Visual Research</div><h1>{escaped_ticker}</h1><div class="muted">{company_name}</div></div><div class="asof">Generated {generated}<br>Model stance: <strong>{stance}</strong></div></header>
<section class="grid">
<article class="card"><div class="label">Latest close</div><div class="value">{price_text}</div></article>
<article class="card"><div class="label">Latest session</div><div class="value" style="color:{current_color}">{'—' if change_pct is None else f'{change_pct:+.2f}%'}</div></article>
<article class="card"><div class="label">Composite score</div><div class="value">{html.escape(str(score if score is not None else '—'))}<span class="muted" style="font-size:14px"> / 100</span></div></article>
<article class="card"><div class="label">Technical snapshot</div><div class="value" style="font-size:17px">RSI {_format_price(rsi)} · MACD {_money(macd)}</div></article>
</section>
{_analysis_results_html(analysis, chart, chart_details, stock_data)}
<p class="foot">Charts and indicators are model outputs for research, not personalized investment advice or guaranteed forecasts. Historical price patterns do not predict future results.</p>
</main></body></html>"""
    output_path.write_text(page, encoding="utf-8")
    return output_path


def write_etf_visual_report(
    ticker: str,
    data: Mapping[str, Any],
    output_path: Path,
    price_data: Any = None,
    technical: Optional[Mapping[str, Any]] = None,
) -> Path:
    """Write a fund-specific research dashboard with clickable article links."""
    profile = data.get("profile") or {}
    quote = data.get("quote") or {}
    fund = data.get("fundMetrics") or {}
    holdings = data.get("holdings") or {}
    dividends = data.get("dividends") or {}
    performance = data.get("performance") or {}
    total_return = performance.get("totalReturn") or {}
    latest = _number(fund.get("marketPrice") or quote.get("price"))
    nav = fund.get("nav") or profile.get("nav")
    expense_ratio = profile.get("expenseRatio")
    if isinstance(expense_ratio, str) and "%" in expense_ratio:
        expense_display = _fund_percent(expense_ratio, decimals=2)
    elif expense_ratio is not None:
        profile_ratio = _number(expense_ratio)
        expense_display = _fund_percent(
            expense_ratio, fraction=profile_ratio is not None and abs(profile_ratio) < 0.01, decimals=2
        )
    else:
        expense_display = _fund_percent(fund.get("expenseRatio"), fraction=True, decimals=2)
    distribution_yield = dividends.get("yield")
    yield_display = (
        _fund_percent(distribution_yield) if distribution_yield is not None
        else _fund_percent(fund.get("dividendYield"), fraction=True)
    )
    metrics = (
        ("Market price", _money(latest), "Most recent available quote"),
        ("Net asset value", _money(nav), "NAV per share"),
        ("Expense ratio", expense_display, "Annual fund operating expenses"),
        ("Assets under management", _money(_assets_under_management(data), compact=True), "Fund size"),
        ("Distribution yield", yield_display, "Reported yield"),
        ("Holdings", str(holdings.get("totalHoldingsCount") or "Not available"), "Positions in the portfolio"),
    )
    cards = "".join(_metric_card(label, value, note) for label, value, note in metrics)

    disclosed_holdings = [
        item for item in (holdings.get("topHoldings") or []) if isinstance(item, Mapping)
    ]
    raw_weights = [
        (item.get("weight") if item.get("weight") is not None else item.get("weightPct"))
        for item in disclosed_holdings
    ]
    numeric_weights = [
        _number(value) for value in raw_weights
        if not (isinstance(value, str) and "%" in value)
    ]
    percentage_points = any(value is not None and abs(value) > 1 for value in numeric_weights)

    def weight_percent(value: Any) -> Optional[float]:
        number = _number(value)
        if number is None:
            return None
        if isinstance(value, str) and "%" in value:
            return number
        return number if percentage_points else number * 100

    def holding_row(item: Mapping[str, Any]) -> str:
        name = item.get("name") or item.get("ticker") or "—"
        symbol = item.get("ticker") or "—"
        raw_weight = item.get("weight") if item.get("weight") is not None else item.get("weightPct")
        pct = weight_percent(raw_weight)
        value = f"{pct:.2f}%" if pct is not None else "—"
        bar = min(max(pct or 0, 0), 100)
        return (
            f'<tr><td>{_escape(name)}</td><td>{_escape(symbol)}</td>'
            f'<td><div class="weight-value">{_escape(value)}</div>'
            f'<div class="weight-track"><span style="width:{bar:.2f}%"></span></div></td></tr>'
        )

    holding_rows = [holding_row(item) for item in disclosed_holdings]
    top_holding_rows = holding_rows[:10]
    more_holding_rows = holding_rows[10:]
    holdings_table = _table(("Holding", "Ticker", "Portfolio weight"), top_holding_rows, "Holdings are not available.")
    if more_holding_rows:
        holdings_table += (
            f'<details class="news-more holdings-more"><summary>Show {len(more_holding_rows)} more holdings</summary>'
            + _table(("Holding", "Ticker", "Portfolio weight"), more_holding_rows)
            + "</details>"
        )

    sector_source = holdings.get("sectors") or []
    if isinstance(sector_source, Mapping):
        sector_source = [{"name": key, "weight": value} for key, value in sector_source.items()]
    sector_entries = [item for item in sector_source if isinstance(item, Mapping)]
    sector_raw_weights = [
        item.get("weight") or item.get("weightPct") or item.get("v") or item.get("value") or item.get("w")
        for item in sector_entries
    ]
    sector_numeric = [
        _number(value) for value in sector_raw_weights
        if not (isinstance(value, str) and "%" in value)
    ]
    sector_points = any(value is not None and abs(value) > 1 for value in sector_numeric)
    sector_rows = []
    for item, raw_weight in zip(sector_entries, sector_raw_weights):
        pct = _number(raw_weight)
        if pct is not None and not (isinstance(raw_weight, str) and "%" in raw_weight) and not sector_points:
            pct *= 100
        name = item.get("name") or item.get("sector") or item.get("n") or item.get("label") or item.get("t") or "—"
        if pct is None:
            rendered = "Not available"
            bar = 0
        else:
            rendered = f"{pct:.2f}%"
            bar = min(max(pct, 0), 100)
        sector_rows.append(
            f'<div class="allocation-row"><div class="allocation-heading"><span>{_escape(name)}</span>'
            f'<strong>{_escape(rendered)}</strong></div><div class="allocation-track">'
            f'<span style="width:{bar:.2f}%"></span></div></div>'
        )

    period_labels = {
        "1m": "1 month", "tr1m": "1 month",
        "3m": "3 months", "tr3m": "3 months",
        "6m": "6 months", "tr6m": "6 months",
        "ytd": "Year to date", "trytd": "Year to date",
        "1y": "1 year", "tr1y": "1 year",
        "3y": "3 years", "tr3y": "3 years",
        "5y": "5 years", "tr5y": "5 years", "cagr5y": "5-year annualized",
        "cagr10y": "10-year annualized", "cagrmax": "Since inception (annualized)",
    }
    return_rows = []
    if isinstance(total_return, Mapping):
        for key, value in total_return.items():
            if value is None:
                continue
            label = period_labels.get(_normalized_key(key), re.sub(r"(?<!^)([A-Z])", r" \1", str(key)).replace("_", " ").strip())
            return_rows.append(
                f"<tr><td>{_escape(label)}</td><td>{_escape(_fund_percent(value))}</td></tr>"
            )

    distribution_history = dividends.get("history") or []
    distribution_rows = []
    if isinstance(distribution_history, list):
        for item in distribution_history[:8]:
            if not isinstance(item, Mapping):
                continue
            date = _pick_value((item,), "exDate", "date", "paymentDate")
            amount = _pick_value((item,), "cashAmount", "amount", "distribution")
            if date or amount is not None:
                distribution_rows.append(
                    f"<tr><td>{_escape(date or '—')}</td><td>{_escape(_money(amount) if amount is not None else '—')}</td></tr>"
                )
    distribution_summary = dividends.get("metrics") or dividends.get("summary") or {}
    distribution_facts = []
    for label, aliases in (
        ("Annual distribution", ("annualDividend", "annualRate", "dps")),
        ("Payment frequency", ("payoutFrequency", "frequency")),
        ("Next ex-date", ("exDividendDate", "exDate")),
    ):
        value = _pick_value((distribution_summary, dividends), *aliases)
        if value is not None:
            if label == "Annual distribution" and _number(value) is not None:
                value = _money(value)
            distribution_facts.append(_metric_card(label, str(value)))

    concentration = sum(
        pct for pct in (weight_percent(value) for value in raw_weights) if pct is not None
    ) if raw_weights else None
    if concentration is not None and concentration > 100:
        diversification = (
            '<p class="muted">Reported top-holding weights total more than 100%, so concentration is not shown. '
            'Check the source weights before drawing conclusions.</p>'
        )
    elif concentration is not None:
        diversification = (
            f'<p>The disclosed top holdings represent <strong>{concentration:.1f}%</strong> of fund assets. '
            'This is partial concentration, not a measure of the full portfolio diversification.</p>'
        )
    else:
        diversification = '<p class="muted">Holding weights are unavailable, so concentration cannot be calculated.</p>'
    chart_html = ""
    if price_data is not None:
        try:
            frame = _prepare_prices(price_data)
            levels = ((technical or {}).get("timeframes") or {}).get("1m") or {}
            chart_html = f'<div class="chart-wrap">{_build_chart(frame, levels.get("levels") or {})}</div>'
        except (ValueError, RuntimeError):
            chart_html = '<p class="muted">Price chart is not available for the supplied history.</p>'
    timeframe_rows = []
    for key, item in ((technical or {}).get("timeframes") or {}).items():
        timeframe_rows.append(
            f"<tr><td>{_escape(item.get('label') or key)}</td><td>{_escape(item.get('trend') or '—')}</td>"
            f"<td>{_escape(item.get('technicalScore') if item.get('technicalScore') is not None else '—')}</td>"
            f"<td>{_escape(item.get('confidence') if item.get('confidence') is not None else '—')}</td></tr>"
        )
    technical_section = _table(("Timeframe", "Trend", "Technical score", "Confidence"), timeframe_rows,
                               (technical or {}).get("reason") or "Technical analysis is unavailable.")
    escaped_ticker = html.escape(str(ticker))
    news_section = _news_html(data)
    title = f"MOMO Fund Research · {escaped_ticker}"
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title><style>
:root{{color-scheme:dark;--bg:{_COLORS["background"]};--panel:{_COLORS["panel"]};--line:{_COLORS["grid"]};--text:{_COLORS["text"]};--muted:{_COLORS["muted"]};--up:{_COLORS["up"]};--down:{_COLORS["down"]}}}
*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(ellipse at 10% 0%,#172541 0,transparent 36%),var(--bg);color:var(--text);font:15px/1.5 Segoe UI,Arial,sans-serif}}
main{{max-width:1480px;margin:0 auto;padding:32px 26px 48px}}header{{display:flex;justify-content:space-between;align-items:flex-end;gap:20px;margin-bottom:22px}}h1{{font-size:clamp(28px,4vw,44px);line-height:1.05;margin:6px 0}}h2{{font-size:clamp(23px,3vw,30px);margin:4px 0 10px}}h3{{font-size:15px;margin:0 0 8px}}.eyebrow,.muted{{color:var(--muted)}}.eyebrow{{font-size:12px;letter-spacing:.16em;text-transform:uppercase}}.asof{{text-align:right;color:var(--muted);font-size:13px}}
.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:13px;margin:16px 0}}.card,.panel{{background:linear-gradient(145deg,rgba(20,31,53,.98),rgba(14,22,39,.98));border:1px solid rgba(143,161,186,.14);border-radius:16px;box-shadow:0 12px 30px rgba(0,0,0,.18)}}.card{{padding:17px 19px;min-height:94px}}.label{{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}}.value{{font-size:23px;font-weight:650;margin-top:8px;overflow-wrap:anywhere}}.card-note{{color:var(--muted);font-size:12px;margin-top:6px}}.panel{{padding:18px 20px;margin:16px 0;overflow:hidden}}.analysis-section{{scroll-margin-top:70px;margin:30px 0}}.section-kicker{{color:#fbbf24;font-size:11px;font-weight:700;letter-spacing:.13em;text-transform:uppercase}}.section-lead{{font-size:18px;max-width:920px;margin:10px 0 16px}}.analysis-nav{{position:sticky;top:0;z-index:5;display:flex;gap:8px;overflow-x:auto;margin:18px -8px 8px;padding:10px 8px;background:rgba(11,16,32,.92);backdrop-filter:blur(12px);border-bottom:1px solid var(--line)}}.analysis-nav a{{flex:0 0 auto;color:var(--text);text-decoration:none;padding:8px 12px;border:1px solid var(--line);border-radius:999px;font-size:13px}}
.table-wrap{{overflow-x:auto;margin:10px 0}}table{{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}}th,td{{text-align:left;padding:12px 10px;border-bottom:1px solid rgba(143,161,186,.13)}}th{{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.08em;white-space:nowrap}}td{{font-size:13px;overflow-wrap:anywhere;vertical-align:top}}.chart-wrap{{overflow-x:auto}}svg{{display:block;width:100%;min-width:800px;height:auto}}svg text{{fill:{_COLORS["muted"]}}}.foot{{color:var(--muted);font-size:12px;margin:20px 4px 0}}
.allocation-row{{margin:12px 0}}.allocation-heading{{display:flex;justify-content:space-between;gap:12px;font-size:13px;margin-bottom:6px}}.allocation-track,.weight-track{{height:8px;background:rgba(143,161,186,.14);border-radius:999px;overflow:hidden}}.allocation-track span,.weight-track span{{display:block;height:100%;background:linear-gradient(90deg,#60a5fa,#2dd4a7);border-radius:inherit}}.weight-value{{font-variant-numeric:tabular-nums;font-weight:650;margin-bottom:5px}}.weight-track{{height:5px;max-width:180px}}.distribution-summary{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}}.news-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin:14px 0}}.news-card{{display:flex;flex-direction:column;min-width:0;padding:16px 18px;border:1px solid rgba(143,161,186,.14);border-radius:14px;background:linear-gradient(145deg,rgba(20,31,53,.98),rgba(14,22,39,.98));color:var(--text);text-decoration:none}}.news-card:hover,.news-card:focus{{border-color:#60a5fa;outline:none}}.news-meta{{color:var(--muted);font-size:12px}}.news-card h3{{font-size:16px;line-height:1.35;margin:8px 0}}.news-card p{{color:var(--muted);font-size:13px;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}}.news-link{{color:#8ac5ff;font-size:12px;font-weight:650;margin-top:auto;padding-top:10px}}.news-more{{grid-column:1/-1;border:1px solid rgba(143,161,186,.15);border-radius:12px;padding:12px 14px;background:rgba(17,26,46,.5)}}.news-more>summary{{cursor:pointer;font-weight:650}}.news-more .news-card{{margin-top:10px}}
@media(max-width:800px){{main{{padding:22px 14px 36px}}.grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}header{{align-items:flex-start;flex-direction:column}}.asof{{text-align:left}}}}@media(max-width:680px){{.news-grid{{grid-template-columns:1fr}}}}@media(max-width:480px){{main{{padding:18px 12px 32px}}.grid{{grid-template-columns:1fr 1fr;gap:9px}}.card{{padding:13px;min-height:82px}}.value{{font-size:19px}}}}
</style></head><body><main>
<header><div><div class="eyebrow">MOMO · Fund research</div><h1>{escaped_ticker}</h1><div class="muted">{_escape(fund.get("fundName") or profile.get("name") or "Exchange-traded fund")}</div></div><div class="asof">Fund profile and market data<br>Price, NAV and performance are shown separately</div></header>
<nav class="analysis-nav" aria-label="Fund sections"><a href="#overview">Overview</a><a href="#holdings">Holdings</a><a href="#allocation">Sector allocation</a><a href="#diversification">Diversification</a><a href="#performance">Performance</a><a href="#distributions">Distributions</a><a href="#technical">Technical analysis</a><a href="#news">News</a></nav>
<section class="analysis-section" id="overview"><div class="section-kicker">Fund profile</div><h2>Fund overview</h2>
<div class="grid">{cards}</div><div class="panel"><p class="section-lead">{_escape(profile.get("description") or "Fund description is not available.")}</p>
<p class="muted">Fund characteristics, holdings and distributions can change over time. Review the dates and measures shown before comparing funds.</p></div></section>
<section class="analysis-section panel" id="holdings"><div class="section-kicker">Portfolio composition</div><h2>Top holdings</h2>
<p class="muted">Weights show each disclosed holding as a share of the fund.</p>{holdings_table}</section>
<section class="analysis-section panel" id="allocation"><div class="section-kicker">Portfolio composition</div><h2>Sector allocation</h2>
{''.join(sector_rows) if sector_rows else '<p class="muted">Sector allocation is not available.</p>'}</section>
<section class="analysis-section panel" id="diversification"><div class="section-kicker">Portfolio breadth</div><h2>Diversification &amp; concentration</h2>
{diversification}<p class="muted">Top-holdings concentration covers only positions disclosed above; it is not the fund's complete diversification profile.</p></section>
<section class="analysis-section panel" id="performance"><div class="section-kicker">Returns</div><h2>Fund performance</h2>
{_table(("Period", "Reported return"), return_rows, "Performance history is not available.")}
<h3 class="subheading">Price history and technical indicators</h3>{chart_html or '<p class="muted">Historical price chart is unavailable.</p>'}</section>
<section class="analysis-section panel" id="distributions"><div class="section-kicker">Income</div><h2>Distributions</h2>
<div class="distribution-summary">{''.join(distribution_facts) or '<p class="muted">Distribution details are not available.</p>'}</div>
<h3 class="subheading">Recent distribution history</h3>{_table(("Ex-date", "Distribution per share"), distribution_rows, "Distribution history is not available.")}</section>
<section class="analysis-section panel" id="technical"><div class="section-kicker">Price-based indicators</div><h2>Technical analysis</h2>
<p class="muted">Trend and momentum indicators use market price history and are separate from the fund's holdings and fundamentals.</p>{technical_section}</section>
{news_section}<p class="foot">Fund data may change. NAV, market price, expenses, distributions and performance are distinct measures. This research is not investment advice.</p>
</main></body></html>"""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(page, encoding="utf-8")
    return output_path


def visual_filename(ticker: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", str(ticker)).strip("._-").lower()
    return f"momo_{slug or 'stock'}_visual.html"


def etf_visual_filename(ticker: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", str(ticker)).strip("._-").lower()
    return f"momo_{slug or 'fund'}_visual.html"
