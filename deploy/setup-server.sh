#!/usr/bin/env bash
# One-time setup of NDHD on a free, always-on Ubuntu 24.04 server
# (Oracle Cloud Always Free Arm, or a Google Cloud e2-micro). See docs/SERVER.md.
#
# Run as your normal login user (it uses sudo where needed), from the repo root,
# after copying your .env (paper keys + DASHBOARD_API_KEY) into it:
#     bash deploy/setup-server.sh            # dashboard + paper engine
#     bash deploy/setup-server.sh --no-engine
# Safe to run again: it updates in place, then restarts the services.
#
# What it guarantees:
#   - Paper trading only: stops if .env turns LIVE_TRADING on.
#   - The dashboard listens on 127.0.0.1 only; no public port is opened.
#     Your devices reach it through Tailscale Serve (tailnet only, never Funnel).
#   - systemd restarts the dashboard and the engine after a crash or reboot,
#     so the dashboard and its kill switch stay reachable.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="$(id -un)"
NODE_VERSION="v24.21.0"
UV_VERSION="0.12.22"
WITH_ENGINE=1
[[ "${1:-}" == "--no-engine" ]] && WITH_ENGINE=0
cd "$REPO"

step() { printf '\n== %s\n' "$*"; }
die() { printf 'Stopped: %s\n' "$*" >&2; exit 1; }

[[ -f .env ]] || die "copy your .env into $REPO first (paper keys, DASHBOARD_API_KEY). See docs/SERVER.md."
[[ "$(uname -s)" == "Linux" ]] || die "this script is for a Linux server."

step "1/6 System packages"
sudo apt-get update -y
sudo apt-get install -y ca-certificates curl git python3-venv xz-utils

step "2/6 Python 3.14 and the hashed Linux lock (requirements.lock.linux.txt)"
[[ -x .venv-tools/bin/uv ]] || { python3 -m venv .venv-tools && .venv-tools/bin/pip install --disable-pip-version-check "uv==$UV_VERSION"; }
.venv-tools/bin/uv python install 3.14
[[ -x .venv/bin/python ]] || .venv-tools/bin/uv venv --python 3.14 .venv
.venv-tools/bin/uv pip install --python .venv/bin/python --require-hashes -r requirements.lock.linux.txt

# Same rules the engine uses to read .env; prints yes/no only, never values.
env_state="$(.venv/bin/python scripts/env_check.py)"
echo "$env_state" | grep -q '"live_trading": false' || die "LIVE_TRADING is on in .env. This server setup is for paper trading only."
echo "$env_state" | grep -q '"paper": true' || die "ALPACA_PAPER is not true in .env."
echo "$env_state" | grep -q '"api_key_set": true' || die "set DASHBOARD_API_KEY in .env first (docs/REMOTE_ACCESS.md, step 2)."
echo "Safety check: paper trading on, live trading off, API key set."

step "3/6 Build the dashboard (Node $NODE_VERSION, checksum-verified)"
case "$(uname -m)" in
  aarch64|arm64) node_arch=arm64 ;;
  x86_64) node_arch=x64 ;;
  *) die "unsupported CPU $(uname -m)" ;;
esac
node_name="node-$NODE_VERSION-linux-$node_arch"
if ! .tools/node/bin/node --version 2>/dev/null | grep -qx "$NODE_VERSION"; then
  mkdir -p .tools && echo '*' > .tools/.gitignore
  curl -fsSL -o ".tools/$node_name.tar.xz" "https://nodejs.org/dist/$NODE_VERSION/$node_name.tar.xz"
  curl -fsSL -o .tools/SHASUMS256.txt "https://nodejs.org/dist/$NODE_VERSION/SHASUMS256.txt"
  (cd .tools && grep "  $node_name.tar.xz\$" SHASUMS256.txt | sha256sum -c -) || die "Node checksum mismatch; not using that download."
  rm -rf .tools/node && tar -xJf ".tools/$node_name.tar.xz" -C .tools && mv ".tools/$node_name" .tools/node
  rm -f ".tools/$node_name.tar.xz"
fi
(cd web && PATH="$REPO/.tools/node/bin:$PATH" npm ci --ignore-scripts --no-fund && PATH="$REPO/.tools/node/bin:$PATH" npm run build)

step "4/6 Tests"
.venv/bin/python -m unittest discover -s congress_trades -t .
.venv/bin/python -m unittest discover -s robinhood_view -t .

step "5/6 Services that restart on crash and on reboot"
install_unit() {
  sed -e "s|@REPO@|$REPO|g" -e "s|@USER@|$RUN_USER|g" "deploy/$1" | sudo tee "/etc/systemd/system/$1" >/dev/null
}
install_unit ndhd-api.service
install_unit ndhd-engine.service
sudo systemctl daemon-reload
sudo systemctl enable ndhd-api.service
sudo systemctl restart ndhd-api.service
if [[ "$WITH_ENGINE" == 1 ]]; then
  sudo systemctl enable ndhd-engine.service
  sudo systemctl restart ndhd-engine.service
else
  sudo systemctl disable --now ndhd-engine.service 2>/dev/null || true
fi
for _ in $(seq 1 40); do curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1 && break; sleep 1; done
curl -fsS http://127.0.0.1:8000/api/health >/dev/null || die "the dashboard did not start: sudo journalctl -u ndhd-api -n 50"

step "6/6 Private access with Tailscale (tailnet only)"
if ! command -v tailscale >/dev/null; then
  . /etc/os-release
  curl -fsSL "https://pkgs.tailscale.com/stable/ubuntu/${VERSION_CODENAME}.noarmor.gpg" \
    | sudo tee /usr/share/keyrings/tailscale-archive-keyring.gpg >/dev/null
  curl -fsSL "https://pkgs.tailscale.com/stable/ubuntu/${VERSION_CODENAME}.tailscale-keyring.list" \
    | sudo tee /etc/apt/sources.list.d/tailscale.list >/dev/null
  sudo apt-get update -y && sudo apt-get install -y tailscale
fi
if ! tailscale status >/dev/null 2>&1; then
  echo "Sign this server in to your tailnet: open the link printed below in a browser."
  sudo tailscale up
fi
sudo tailscale serve --bg 8000 >/dev/null
url="https://$(tailscale status --json | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')/"

printf '\nDone.\n  Dashboard (your devices only): %s\n' "$url"
printf '  Status:  systemctl status ndhd-api ndhd-engine\n  Logs:    journalctl -u ndhd-engine -f\n'
printf '  Engine:  %s\n' "$([[ "$WITH_ENGINE" == 1 ]] && echo 'running in paper mode (places paper orders while the market is open)' || echo 'not started (--no-engine)')"
