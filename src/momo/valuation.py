from __future__ import annotations

from math import isfinite, sqrt
from typing import Any, Dict, Mapping, Optional
from .core.units import parse_number, parse_percent, normalize_financial


_ALIASES = {
    "price": ("currentprice", "price", "marketprice", "regularmarketprice"),
    "fcf": ("freecashflow", "fcf", "freecashflowttm", "leveredfreecashflow"),
    "wacc": ("wacc", "costofcapital", "discount rate"),
    "eps": ("eps", "earningspershare", "dilutedeps", "trailingeps"),
    "book_value_per_share": ("bookvaluepershare", "bvps", "tangiblebookvaluepershare"),
    "dividend_per_share": ("annualdividend", "dividendpershare", "annualdividendsper share", "forwardannualdividendrate"),
    "dividend_growth": ("dividendgrowth", "dividendgrowthrate"),
    "total_assets": ("totalassets", "assets"),
    "total_liabilities": ("totalliabilities", "liabilities"),
    "shares": ("sharesoutstanding", "shares"),
    "net_income": ("netincome", "netincomecommonstockholders", "netincometocommon"),
    "nopat": ("nopat", "netoperatingprofitaftertax"),
    "invested_capital": ("investedcapital", "capitalemployed"),
    "normalized_earnings": ("normalizedearnings", "normalizedeps"),
    "growth_rate": ("epsgrowth", "earningsgrowth", "growthrate", "earningsgrowthrate"),
    "current_ratio": ("currentratio",),
    "debt": ("totaldebt", "longtermdebt", "debt"),
    "dividend_yield": ("dividendyield", "trailingannualdividendyield"),
    "pe": ("trailingpe", "peratio", "pe"),
    "price_to_book": ("pricetobook", "pb", "pb ratio"),
}


def _number(value: Any) -> Optional[float]:
    return normalize_financial(value).value


def _find(data: Mapping[str, Any], names: tuple[str, ...]) -> Optional[float]:
    aliases = {"".join(ch for ch in name.lower() if ch.isalnum()) for name in names}
    stack: list[Any] = [data]
    while stack:
        item = stack.pop()
        if isinstance(item, Mapping):
            for key, value in item.items():
                normalized = "".join(ch for ch in str(key).lower() if ch.isalnum())
                if normalized in aliases:
                    found = _number(value)
                    if found is not None:
                        return found
                if isinstance(value, (Mapping, list, tuple)):
                    stack.append(value)
        elif isinstance(item, (list, tuple)):
            stack.extend(item)
    return None


def _find_percent(data: Mapping[str, Any], names: tuple[str, ...]) -> Optional[float]:
    aliases = {"".join(ch for ch in name.lower() if ch.isalnum()) for name in names}
    stack: list[Any] = [data]
    while stack:
        item = stack.pop()
        if isinstance(item, Mapping):
            for key, value in item.items():
                normalized = "".join(ch for ch in str(key).lower() if ch.isalnum())
                if normalized in aliases:
                    found = parse_percent(value)
                    if found is not None:
                        return found
                if isinstance(value, (Mapping, list, tuple)):
                    stack.append(value)
        elif isinstance(item, (list, tuple)):
            stack.extend(item)
    return None


def _result(
    value: Optional[float],
    formula: str,
    inputs: Mapping[str, Any],
    assumptions: Mapping[str, Any],
    interpretation: str,
    reason: Optional[str] = None,
    unit: str = "per_share",
) -> Dict[str, Any]:
    valid = value is not None and isfinite(value)
    return {
        "value": round(value, 4) if valid else None,
        "unit": unit,
        "formula": formula,
        "inputs": dict(inputs),
        "assumptions": dict(assumptions),
        "interpretation": interpretation,
        "status": "available" if valid else "unavailable",
        **({"reason": reason} if reason else {}),
    }


def calculate_fair_values(
    data: Mapping[str, Any],
    assumptions: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Calculate disclosed stock valuation models from available source metrics."""
    supplied = dict(assumptions or {})
    allowed_assumptions = {
        "wacc", "growth_rate", "forecast_years", "discount_rate",
        "dividend_growth", "required_return", "rim_years", "cost_of_capital",
    }
    unknown = set(supplied) - allowed_assumptions
    if unknown:
        raise ValueError(f"Unknown valuation assumption(s): {', '.join(sorted(unknown))}.")
    rate_names = ("wacc", "growth_rate", "discount_rate", "required_return",
                  "dividend_growth", "cost_of_capital")
    for name in rate_names:
        if name in supplied and parse_percent(supplied[name]) is None:
            raise ValueError(f"{name} must be a finite number.")
    for name in ("forecast_years", "rim_years"):
        if name in supplied:
            value = _number(supplied[name])
            if value is None or value < 1 or value > 20 or not value.is_integer():
                raise ValueError(f"{name} must be an integer between 1 and 20.")
    metric = {name: _find(data, aliases) for name, aliases in _ALIASES.items()}
    # Explicit model assumptions are distinguished from reported company data.
    wacc = parse_percent(supplied.get("wacc"))
    if wacc is None:
        wacc = _find_percent(data, _ALIASES["wacc"])
    growth_input = parse_percent(supplied.get("growth_rate"))
    if growth_input is None:
        growth_input = _find_percent(data, _ALIASES["growth_rate"])
    growth = growth_input
    forecast_input = _number(supplied.get("forecast_years"))
    forecast_years = int(forecast_input) if forecast_input is not None else 5
    discount_rate = parse_percent(supplied.get("discount_rate"))
    if discount_rate is None:
        discount_rate = wacc

    output: Dict[str, Any] = {}
    fcf = metric["fcf"]
    shares = metric["shares"]
    if (fcf is not None and fcf > 0 and shares is not None and shares > 0
            and wacc is not None and growth is not None
            and wacc > 0 and wacc > growth > -1):
        cashflows = [fcf * (1 + growth) ** year for year in range(1, forecast_years + 1)]
        terminal = cashflows[-1] * (1 + growth) / (wacc - growth)
        value = (sum(amount / (1 + wacc) ** year for year, amount in enumerate(cashflows, 1))
                 + terminal / (1 + wacc) ** forecast_years) / shares
        output["dcf"] = _result(value, "sum(FCF_t/(1+WACC)^t) + TV/(1+WACC)^n; TV=FCF_n(1+g)/(WACC-g)",
                                {"free_cash_flow": fcf, "shares_outstanding": shares},
                                {"wacc": wacc, "growth_rate": growth, "forecast_years": forecast_years},
                                "Estimated intrinsic value per share; compare separately with the observed market price.")
    else:
        output["dcf"] = _result(None, "sum(FCF_t/(1+WACC)^t) + TV/(1+WACC)^n; TV=FCF_n(1+g)/(WACC-g)",
                                {"free_cash_flow": fcf, "shares_outstanding": shares},
                                {"wacc": wacc, "growth_rate": growth, "forecast_years": forecast_years},
                                "A DCF estimate requires positive free cash flow, shares outstanding, and WACC greater than growth.",
                                "Required inputs are missing or violate valuation constraints.")

    dividend = metric["dividend_per_share"]
    dividend_growth = parse_percent(supplied.get("dividend_growth"))
    if dividend_growth is None:
        dividend_growth = _find_percent(data, _ALIASES["dividend_growth"])
    if dividend_growth is None:
        dividend_growth = 0.0
    required_input = parse_percent(supplied.get("required_return"))
    required_return = discount_rate if required_input is None else required_input
    next_dividend = dividend * (1 + dividend_growth) if dividend is not None else None
    ddm_value = (next_dividend / (required_return - dividend_growth)
                 if next_dividend is not None and next_dividend > 0 and required_return is not None
                 and required_return > 0 and dividend_growth > -1
                 and required_return > dividend_growth else None)
    output["ddm"] = _result(ddm_value, "D1/(r-g)",
                            {"annual_dividend_per_share": dividend, "next_dividend_per_share": next_dividend},
                            {"required_return": required_return, "dividend_growth": dividend_growth},
                            "Dividend-based intrinsic value per share; not a market-price target.",
                            None if ddm_value is not None else "Positive dividend and required return greater than dividend growth are required.")

    eps = metric["eps"]
    bvps = metric["book_value_per_share"]
    rim_year_input = _number(supplied.get("rim_years"))
    years = int(rim_year_input) if rim_year_input is not None else 5
    if eps is not None and bvps is not None and bvps > 0 and discount_rate is not None and discount_rate > 0:
        book = bvps
        residuals = []
        for _ in range(years):
            residuals.append(eps - discount_rate * book)
            book += eps - (dividend or 0.0)
        rim_value = bvps + sum(ri / (1 + discount_rate) ** year for year, ri in enumerate(residuals, 1))
        rim_assumptions = {"required_return": discount_rate, "projection_years": years,
                           "earnings_per_share_held_constant": True, "dividend_reinvestment_assumed": dividend is None}
    else:
        rim_value = None
        rim_assumptions = {"required_return": discount_rate, "projection_years": years}
    output["rim"] = _result(rim_value, "B0 + sum((NI_t-r*B_(t-1))/(1+r)^t)",
                            {"earnings_per_share": eps, "book_value_per_share": bvps, "annual_dividend_per_share": dividend},
                            rim_assumptions, "Residual-income estimate per share; a constant-earnings projection is a simplifying assumption.",
                            None if rim_value is not None else "Positive book value, earnings per share, and a discount rate are required.")

    assets, liabilities = metric["total_assets"], metric["total_liabilities"]
    nav = ((assets - liabilities) / shares
           if assets is not None and liabilities is not None and shares is not None and shares > 0 else None)
    output["nav"] = _result(nav, "(Total assets-Total liabilities)/Shares outstanding",
                            {"total_assets": assets, "total_liabilities": liabilities, "shares_outstanding": shares},
                            {}, "Balance-sheet net asset value per share; distinct from market price.",
                            None if nav is not None else "Reported assets, liabilities, and positive shares outstanding are required.")

    reported_normalized_earnings = metric["normalized_earnings"]
    normalized_earnings = reported_normalized_earnings if reported_normalized_earnings is not None else eps
    cost_input = parse_percent(supplied.get("cost_of_capital"))
    cost_of_capital = wacc if cost_input is None else cost_input
    epv = normalized_earnings / cost_of_capital if normalized_earnings is not None and normalized_earnings > 0 and cost_of_capital is not None and cost_of_capital > 0 else None
    output["epv"] = _result(epv, "Normalized earnings per share/Cost of capital",
                            {"normalized_earnings_per_share": normalized_earnings},
                            {"cost_of_capital": cost_of_capital,
                             "earnings_basis": "reported normalized earnings" if reported_normalized_earnings is not None else "reported EPS proxy"},
                            "Earnings-power value per share under a no-growth assumption. Reported EPS is used as a proxy when normalized earnings are unavailable.",
                            None if epv is not None else "Positive normalized earnings and cost of capital are required.")

    nopat, invested = metric["nopat"], metric["invested_capital"]
    eva = nopat - invested * wacc if nopat is not None and invested is not None and invested >= 0 and wacc is not None and wacc >= 0 else None
    output["eva"] = _result(eva, "NOPAT-(Invested capital*WACC)",
                            {"nopat": nopat, "invested_capital": invested}, {"wacc": wacc},
                            "Economic profit in the source financial units, not an intrinsic share price.",
                            None if eva is not None else "NOPAT and non-negative invested capital are required.", unit="financial_units")

    graham_value = sqrt(22.5 * eps * bvps) if eps is not None and bvps is not None and eps > 0 and bvps > 0 else None
    output["graham_number"] = _result(graham_value, "sqrt(22.5*EPS*BVPS)",
                                      {"eps": eps, "book_value_per_share": bvps}, {},
                                      "Graham Number reference estimate per share, not a forecast.",
                                      None if graham_value is not None else "Positive EPS and book value per share are required.")
    current_ratio, debt = metric["current_ratio"], metric["debt"]
    price = metric["price"]
    pe = metric["pe"] if metric["pe"] is not None else (price / eps if price is not None and eps is not None and eps > 0 else None)
    price_to_book = metric["price_to_book"] if metric["price_to_book"] is not None else (
        price / bvps if price is not None and bvps is not None and bvps > 0 else None
    )
    defensive_ok = (
        graham_value is not None and price is not None and price <= graham_value
        and pe is not None and pe <= 15
        and price_to_book is not None and price_to_book <= 1.5
        and current_ratio is not None and current_ratio >= 2.0
    )
    output["graham_defensive"] = _result(graham_value if defensive_ok else None,
                                         "sqrt(22.5*EPS*BVPS), subject to defensive-stock screens",
                                         {"eps": eps, "book_value_per_share": bvps, "market_price": price,
                                          "price_to_earnings": pe, "price_to_book": price_to_book,
                                          "current_ratio": current_ratio, "debt": debt},
                                         {"maximum_pe": 15, "maximum_price_to_book": 1.5, "minimum_current_ratio": 2.0},
                                         "Partial Graham-style defensive screen; long-term earnings and dividend history are not tested.",
                                         None if defensive_ok else "Required positive valuation metrics and current ratio do not meet the stated defensive screens or are unavailable.")

    growth_pct = _find_percent(data, _ALIASES["growth_rate"])
    if growth_pct is not None:
        growth_pct *= 100
    lynch = eps * growth_pct / 100 if eps is not None and eps > 0 and growth_pct is not None and growth_pct > 0 else None
    output["peter_lynch"] = _result(lynch, "EPS*earnings_growth_rate",
                                    {"eps": eps, "earnings_growth_rate_pct": growth_pct}, {},
                                    "Peter Lynch growth-to-P/E reference estimate per share; growth is not guaranteed.",
                                    None if lynch is not None else "Positive EPS and reported or explicitly supplied positive growth are required.")
    dividend_yield = _find_percent(data, _ALIASES["dividend_yield"])
    if dividend_yield is not None:
        dividend_yield *= 100
    pegy_value = eps * (growth_pct + (dividend_yield or 0.0)) / 100 if eps is not None and eps > 0 and growth_pct is not None and growth_pct > 0 else None
    output["peter_lynch_pegy"] = _result(pegy_value, "EPS*(earnings_growth_rate_pct+dividend_yield_pct)/100",
                                         {"eps": eps, "earnings_growth_rate_pct": growth_pct, "dividend_yield_pct": dividend_yield},
                                         {"dividend_yield_pct_used": dividend_yield or 0.0,
                                          "zero_dividend_yield_assumed": dividend_yield is None},
                                         "Dividend-adjusted PEG reference estimate per share; growth and dividend yield are not guarantees.",
                                         None if pegy_value is not None else "Positive EPS and positive growth are required.")
    return {
        "market_price": metric["price"],
        "price_context": "Market price is a current quote and is distinct from all model estimates.",
        "models": output,
    }
