"""Strict financial-unit parsing and normalization."""

from __future__ import annotations

from math import isfinite
from numbers import Real
from typing import Any, Iterable, Optional
from dataclasses import dataclass
from enum import Enum


MISSING_TEXT = {"", "-", "--", "n/a", "na", "none", "null", "not available", "nm"}
MULTIPLIERS = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}


class FinancialUnit(str, Enum):
    NUMBER = "number"
    CURRENCY = "currency"
    PERCENT = "percent"
    MISSING = "missing"


@dataclass(frozen=True)
class FinancialValue:
    value: Optional[float]
    unit: FinancialUnit
    scale: float = 1.0
    missing: bool = False


def normalize_financial(value: Any, percent: bool = False) -> FinancialValue:
    """Strictly normalize a financial scalar and retain unit metadata."""
    if value is None or isinstance(value, bool):
        return FinancialValue(None, FinancialUnit.MISSING, missing=True)
    text = str(value).strip().replace("\u2212", "-")
    explicit_currency = "$" in text or "\u00a3" in text or "\u20ac" in text
    explicit_percent = text.lower().endswith("%")
    parsed = (
        parse_percent(value)
        if percent or explicit_percent
        else parse_number(value)
    )
    if parsed is None:
        return FinancialValue(None, FinancialUnit.MISSING, missing=True)
    scale = 1.0
    stripped = text.replace(",", "").strip()
    if stripped and stripped[-1:].upper() in MULTIPLIERS:
        scale = MULTIPLIERS[stripped[-1:].upper()]
    unit = FinancialUnit.PERCENT if percent or explicit_percent else (
        FinancialUnit.CURRENCY if explicit_currency else FinancialUnit.NUMBER
    )
    return FinancialValue(parsed, unit, scale=scale)


def _finite(value: float) -> Optional[float]:
    return float(value) if isfinite(value) else None


def parse_number(value: Any) -> Optional[float]:
    """Parse a currency/amount/ratio, preserving sign and suffix scale."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Real):
        return _finite(float(value))

    text = str(value).strip().replace("\u2212", "-")
    if not text or text.lower() in MISSING_TEXT:
        return None

    parenthetical = text.startswith("(") and text.endswith(")")
    if parenthetical:
        text = text[1:-1].strip()
    text = text.replace(",", "").replace("$", "").replace("\u00a3", "").replace("\u20ac", "").strip()
    if text.lower().endswith("%"):
        text = text[:-1].strip()

    multiplier = 1.0
    if text and text[-1:].upper() in MULTIPLIERS:
        multiplier = MULTIPLIERS[text[-1:].upper()]
        text = text[:-1].strip()
    try:
        number = float(text) * multiplier
    except (TypeError, ValueError):
        return None
    if parenthetical:
        number = -number
    return _finite(number)


def parse_percent(value: Any) -> Optional[float]:
    """Normalize percentages to decimal fractions (10.81%% -> 0.1081)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Real):
        number = float(value)
        if not isfinite(number):
            return None
        return number / 100.0 if number > 1.0 or number < -1.0 else number

    text = str(value).strip()
    if not text or text.lower() in MISSING_TEXT:
        return None
    explicit_percent = text.endswith("%")
    number = parse_number(text)
    if number is None:
        return None
    if explicit_percent:
        return number / 100.0
    # Native percentage strings such as "10.81" are percentage points.
    return number / 100.0


def first_number(values: Iterable[Any], percent: bool = False) -> Optional[float]:
    parser = parse_percent if percent else parse_number
    for value in values:
        parsed = parser(value)
        if parsed is not None:
            return parsed
    return None


def find_nested_number(value: Any, terms: Iterable[str], percent: bool = False) -> Optional[float]:
    wanted = {term.lower().replace("_", "").replace(" ", "") for term in terms}
    stack = [value]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key, child in node.items():
                normalized = str(key).lower().replace("_", "").replace(" ", "")
                if normalized in wanted:
                    parsed = normalize_financial(child, percent=percent)
                    if parsed.value is not None:
                        return parsed.value
                if isinstance(child, (dict, list, tuple)):
                    stack.append(child)
        elif isinstance(node, (list, tuple)):
            stack.extend(node)
    return None
