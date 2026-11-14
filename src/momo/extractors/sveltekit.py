from __future__ import annotations
import json
import re
from typing import Any, Dict, List, Optional
from bs4 import BeautifulSoup as bs
from ..core.config import logger

def _extract_data_array_text(script_text: str) -> Optional[str]:
    """Find `data: [ ... ]` text inside a SvelteKit script, tracking brackets
    while respecting string literals."""
    idx = script_text.find("data:")
    if idx == -1:
        return None
    start = idx + len("data:")
    while start < len(script_text) and script_text[start].isspace():
        start += 1
    if start >= len(script_text) or script_text[start] != "[":
        return None

    depth = 0
    in_str = False
    str_char = ""
    escape = False
    i = start
    n = len(script_text)
    while i < n:
        ch = script_text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == str_char:
                in_str = False
        else:
            if ch in ("'", '"'):
                in_str = True
                str_char = ch
            elif ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    return script_text[start:i + 1]
        i += 1
    return None

_IDENTIFIER_KEY_RE = re.compile(r'[A-Za-z_$][A-Za-z0-9_$]*')

def _js_object_literal_to_json(js_text: str) -> str:
    """Convert a permissive JavaScript object literal into valid JSON text.

    Penanganan:
      - `void 0` / `undefined` / `NaN`      -> null
      - key tanpa kutip (mis. `n:"IBM"`)    -> diberi kutip ("n":"IBM")
      - angka berawalan titik (`.02`)       -> diberi 0 di depan (0.02)
    - trailing commas before `}`/`]`      -> removed
    String literals are preserved as-is.
    """
    text = re.sub(r"\bvoid\s+0\b", "null", js_text)
    text = re.sub(r"\bundefined\b", "null", text)
    text = re.sub(r"\bNaN\b", "null", text)

    out: List[str] = []
    i, n = 0, len(text)
    in_str = False
    str_char = ""
    escape = False

    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == str_char:
                in_str = False
            i += 1
            continue

        if ch in ("'", '"'):
            in_str = True
            str_char = ch
            out.append('"' if ch == "'" else ch)  # normalize single to double quotes
            i += 1
            continue

        if ch in "{,":
            out.append(ch)
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                out.append(text[j])
                j += 1
            m = _IDENTIFIER_KEY_RE.match(text[j:])
            if m and j + m.end() < n and text[j + m.end()] == ":":
                key = m.group(0)
                out.append(f'"{key}"')
                i = j + len(key)
                continue
            i = j
            continue

        out.append(ch)
        i += 1

    result = "".join(out)
    # Add a leading zero to dot-prefixed numbers after ':', '[' or ','.
    result = re.sub(r"([:\[,]\s*)(-?)\.(\d)", r"\1\g<2>0.\3", result)
    # Remove trailing commas before object/array closers.
    result = re.sub(r",(\s*[}\]])", r"\1", result)
    return result

def _extract_embedded_data(html: str) -> Dict[str, Any]:
    """Find every <script> containing `__sveltekit_`, extract its `data:[...]`
    array, normalize it to JSON, and merge each element's `.data` fields into
    one flat dict.

    Returns an empty dict on failure so callers can use the HTML/CSS fallback.
    """
    soup = bs(html, "html.parser")
    merged: Dict[str, Any] = {}

    for script in soup.find_all("script"):
        text = script.string
        if not text or "__sveltekit_" not in text:
            continue

        arr_text = _extract_data_array_text(text)
        if not arr_text:
            continue

        json_text = _js_object_literal_to_json(arr_text)
        try:
            data_list = json.loads(json_text)
        except json.JSONDecodeError:
            continue

        if not isinstance(data_list, list):
            continue

        for entry in data_list:
            if isinstance(entry, dict) and isinstance(entry.get("data"), dict):
                merged.update(entry["data"])
        break  # relevant script found

    if not merged:
        logger.debug("Embedded JSON data was not found on the page; using the HTML/CSS fallback.")

    return merged
