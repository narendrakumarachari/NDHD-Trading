"""Compare the installed packages with requirements.lock.txt.

Prints one line per package that differs ("name installed -> locked") and
exits 1, or prints nothing and exits 0. Used by start-ndhd.ps1 to decide
whether the .venv needs a refresh before anything starts.
"""
import importlib.metadata as md
import re
import sys
from pathlib import Path

LOCK = Path(__file__).resolve().parent.parent / "requirements.lock.txt"

pins = re.findall(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s\\;]+)", LOCK.read_text(encoding="utf-8"), re.M)
stale = []
for name, locked in pins:
    try:
        installed = md.version(name)
    except md.PackageNotFoundError:
        installed = "missing"
    if installed != locked:
        stale.append(f"{name} {installed} -> {locked}")

print("\n".join(stale))
sys.exit(1 if stale else 0)
