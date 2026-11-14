from __future__ import annotations

import html
import math
import re
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


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


def _number(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _series_path(values: Sequence[Optional[float]], xs: Sequence[float], y_for: Any) -> str:
    points = []
    for x, value in zip(xs, values):
        if value is not None:
            points.append(f"{x:.2f},{y_for(value):.2f}")
    return " ".join(points)


def _format_price(value: Any) -> str:
    number = _number(value)
    return f"{number:,.2f}" if number is not None else "—"


def _format_date(value: Any) -> str:
    pd = _pandas_module()
    try:
        return pd.Timestamp(value).strftime("%b %d, %Y")
    except (TypeError, ValueError):
        return str(value)


def _prepare_prices(price_data: Any) -> pd.DataFrame:
    pd = _pandas_module()
    if not isinstance(price_data, pd.DataFrame) or price_data.empty:
        raise ValueError("Historical OHLCV is unavailable; a chart cannot be generated.")
    frame = price_data.copy()
    frame.columns = [str(column).strip().title() for column in frame.columns]
    required = {"Open", "High", "Low", "Close"}
    if not required.issubset(frame.columns):
        raise ValueError("Historical price data is missing OHLC columns.")
    if "Volume" not in frame.columns:
        frame["Volume"] = 0.0
    frame = frame.dropna(subset=["Open", "High", "Low", "Close"]).sort_index()
    if frame.empty:
        raise ValueError("Historical price data has no complete OHLC rows.")
    for column in ["Open", "High", "Low", "Close", "Volume"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["Open", "High", "Low", "Close"])
    close = frame["Close"]
    for window in (20, 50, 200):
        frame[f"SMA{window}"] = close.rolling(
            window,
            min_periods=max(5, int(round(window * 0.60))),
        ).mean()
    frame["BBMiddle"] = close.rolling(20, min_periods=5).mean()
    deviation = close.rolling(20, min_periods=5).std(ddof=0)
    frame["BBUpper"] = frame["BBMiddle"] + 2.0 * deviation
    frame["BBLower"] = frame["BBMiddle"] - 2.0 * deviation

    delta = close.diff()
    average_gain = delta.clip(lower=0).ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    average_loss = -delta.clip(upper=0).ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    relative_strength = average_gain / average_loss.replace(0, float("nan"))
    frame["RSI"] = (100.0 - 100.0 / (1.0 + relative_strength)).fillna(50.0)

    macd = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    frame["MACD"] = macd
    frame["MACDSignal"] = macd.ewm(span=9, adjust=False).mean()
    frame["MACDHist"] = frame["MACD"] - frame["MACDSignal"]
    return frame.tail(252)


def _build_chart(frame: pd.DataFrame, levels: Mapping[str, Any]) -> str:
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


def _timeframe_rows(analysis: Mapping[str, Any]) -> str:
    rows = []
    for key, item in (analysis.get("timeframes") or {}).items():
        fair_value = item.get("fairValue") or {}
        weights = item.get("weights") or {}
        rows.append(
            "<tr>"
            f"<td><strong>{html.escape(str(key).upper())}</strong></td>"
            f"<td>{html.escape(str(item.get('technicalTrend') or '—'))}</td>"
            f"<td>{_format_price(fair_value.get('low'))} – {_format_price(fair_value.get('base'))} – {_format_price(fair_value.get('high'))}</td>"
            f"<td>{html.escape(str(item.get('expectedGapPct') if item.get('expectedGapPct') is not None else '—'))}%</td>"
            f"<td>{html.escape(str(item.get('confidence') if item.get('confidence') is not None else '—'))}/100</td>"
            f"<td>{html.escape(str(round((_number(weights.get('technical')) or 0) * 100)))} / "
            f"{html.escape(str(round((_number(weights.get('fundamental')) or 0) * 100)))}%</td>"
            "</tr>"
        )
    return "".join(rows) or '<tr><td colspan="6">No timeframe projections available.</td></tr>'


def write_stock_visual_report(
    ticker: str,
    price_data: Any,
    analysis: Mapping[str, Any],
    output_path: Path,
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
    title = f"MOMO Visual Research · {escaped_ticker}"
    generated = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M UTC")
    chart = _build_chart(frame, levels.get("levels") or {})
    current_color = _COLORS["up"] if change_pct is None or change_pct >= 0 else _COLORS["down"]
    score = summary.get("overallScore")
    stance = html.escape(str(summary.get("stance") or "—"))
    price_text = _format_price(current)
    rsi = _number(indicators.get("rsi14"))
    macd = _number(indicators.get("macd"))
    support = (levels.get("levels") or {}).get("supports") or []
    resistance = (levels.get("levels") or {}).get("resistances") or []
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
</style>
</head>
<body><main>
<header><div><div class="eyebrow">MOMO · Visual Research</div><h1>{escaped_ticker}</h1><div class="muted">{html.escape(str((analysis.get("dataContext") or {}).get("source") or "Price + fundamentals"))} · Last {len(frame)} trading sessions</div></div><div class="asof">Generated {generated}<br>Model stance: <strong>{stance}</strong></div></header>
<section class="grid">
<article class="card"><div class="label">Latest close</div><div class="value">{price_text}</div></article>
<article class="card"><div class="label">Latest session</div><div class="value" style="color:{current_color}">{'—' if change_pct is None else f'{change_pct:+.2f}%'}</div></article>
<article class="card"><div class="label">Composite score</div><div class="value">{html.escape(str(score if score is not None else '—'))}<span class="muted" style="font-size:14px"> / 100</span></div></article>
<article class="card"><div class="label">Technical snapshot</div><div class="value" style="font-size:17px">RSI {_format_price(rsi)} · MACD {_format_price(macd)}</div></article>
</section>
<section class="panel"><h2>Price action · trend bands · volume · momentum</h2><div class="chart-wrap">{chart}</div></section>
<section class="grid">
<article class="card"><div class="label">Support</div><div class="value positive">{_format_price(support[0] if support else None)}</div><div class="muted">Nearest model level</div></article>
<article class="card"><div class="label">Resistance</div><div class="value negative">{_format_price(resistance[0] if resistance else None)}</div><div class="muted">Nearest model level</div></article>
<article class="card"><div class="label">SMA 20 / 50 / 200</div><div class="value" style="font-size:16px">{_format_price(indicators.get('sma20'))} / {_format_price(indicators.get('sma50'))} / {_format_price(indicators.get('sma200'))}</div></article>
<article class="card"><div class="label">Technical confidence</div><div class="value">{html.escape(str(levels.get('confidence') if levels.get('confidence') is not None else '—'))}<span class="muted" style="font-size:14px"> / 100</span></div></article>
</section>
<section class="panel"><h2>Multi-timeframe outlook</h2><div class="chart-wrap"><table><thead><tr><th>Horizon</th><th>Trend</th><th>Fair value · low / base / high</th><th>Expected gap</th><th>Confidence</th><th>T / F weight</th></tr></thead><tbody>{_timeframe_rows(analysis)}</tbody></table></div></section>
<p class="foot">Charts and indicators are model outputs for research, not personalized investment advice or guaranteed forecasts. Historical price patterns do not predict future results.</p>
</main></body></html>"""
    output_path.write_text(page, encoding="utf-8")
    return output_path


def visual_filename(ticker: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", str(ticker)).strip("._-").lower()
    return f"momo_{slug or 'stock'}_visual.html"
