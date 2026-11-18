from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_json_write(path: Path, payload: Any, *, default: Any = None) -> Path:
    """Serialize before touching disk, then atomically replace the destination."""
    dump_kwargs = {
        "indent": 2,
        "ensure_ascii": False,
        "allow_nan": False,
    }
    if default is not None:
        dump_kwargs["default"] = default
    serialized = json.dumps(payload, **dump_kwargs)

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            dir=str(output_path.parent),
        )
        temp_path = Path(temp_name)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, output_path)
        temp_path = None
        try:
            os.chmod(output_path, 0o600)
        except OSError:
            pass
        return output_path
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
