"""Print the safety-relevant .env settings as yes/no, never their values.

Used by scripts/start-ndhd.ps1 and deploy/setup-server.sh so both read .env
exactly the way the engine does (python-dotenv, then Config's env_bool:
only "true", any case, counts as on). Prints one line of JSON:
{"env_file": true, "paper": true, "live_trading": false, "api_key_set": true}
"""

import json
import os
import sys
from pathlib import Path

from dotenv import dotenv_values

root = Path(__file__).resolve().parent.parent
env_path = root / ".env"
values = {**dotenv_values(env_path), **os.environ} if env_path.exists() else dict(os.environ)


def flag(name: str, default: bool) -> bool:
    return str(values.get(name) or default).lower() == "true"


json.dump({
    "env_file": env_path.exists(),
    "paper": flag("ALPACA_PAPER", True),
    "live_trading": flag("LIVE_TRADING", False),
    "api_key_set": bool((values.get("DASHBOARD_API_KEY") or "").strip()),
}, sys.stdout)
print()
