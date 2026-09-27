"""
api/logs.py

Incremental tail of Config.log_file (the FileHandler added to
portfolio_engine.py's logging setup - see the LOGGING section there) for
the dashboard's live console panel.

Both the trading engine process and this dashboard API process (since it
imports portfolio_engine) write to the same file. This module only reads
it - LogTailer tracks a byte offset so repeated calls return just the
lines appended since the last call, the way `tail -f` would.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

from . import deps

_LEVEL_RE = re.compile(r"\|\s*(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s*\|")


def classify_level(line: str) -> str:
    match = _LEVEL_RE.search(line)
    return match.group(1) if match else "INFO"


def _log_path() -> Path:
    return Path(deps.engine.CONFIG.log_file)


def _to_records(lines: List[str]) -> List[Dict[str, Any]]:
    return [{"line": line, "level": classify_level(line)} for line in lines if line.strip()]


class LogTailer:
    def __init__(self) -> None:
        self._offset = 0

    def read_new(self, max_lines: int = 500) -> List[Dict[str, Any]]:
        path = _log_path()
        if not path.exists():
            return []

        try:
            size = path.stat().st_size
        except OSError:
            return []

        if size < self._offset:
            # Truncated or replaced (e.g. the engine restarted and the
            # file was recreated) - start over rather than erroring.
            self._offset = 0

        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(self._offset)
                chunk = handle.read()
                self._offset = handle.tell()
        except OSError:
            return []

        lines = chunk.splitlines()
        if len(lines) > max_lines:
            lines = lines[-max_lines:]
        return _to_records(lines)


def read_tail(max_lines: int = 200) -> List[Dict[str, Any]]:
    """Last N lines - used to seed a newly-connected WS client so the
    console panel isn't empty until the next incremental tick."""
    path = _log_path()
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
    except OSError:
        return []
    return _to_records([line.rstrip("\n") for line in lines[-max_lines:]])
