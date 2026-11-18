import json
from pathlib import Path

import pytest


FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def sveltekit_stock_html():
    payload = {
        "info": {
            "ticker": "AAPL",
            "name": "Apple Inc.",
            "quote": {"p": 225.0, "c": 4.5, "cp": "2.04%", "u": "2026-11-18"},
        },
        "quote": {"p": 225.0},
        "financialData": {
            "datekey": ["2025", "2024", "TTM"],
            "fiscalYear": [2025, 2024, None],
            "revenue": ["400B", "383B", "420B"],
            "epsdil": ["6.08", "6.13", "6.20"],
            "fcf": ["100B", "90B", "110B"],
            "sharesOutTotalCommon": ["15B", "15B", "15B"],
        },
    }
    script = (
        "<script>__sveltekit_fixture=1; "
        "{data:[{data:"
        + json.dumps(payload)
        + "}]}</script>"
    )
    return f"<!doctype html><html><head></head><body>{script}<table><tr><td>Revenue</td><td>$400B</td></tr></table></body></html>"


@pytest.fixture
def malformed_sveltekit_html():
    return "<!doctype html><html><body><script>__sveltekit_fixture=1; {data:[{data:{broken:]}</script></body></html>"
