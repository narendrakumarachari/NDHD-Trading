# Running NDHD 24/7 on a free server

So the dashboard, the paper engine and the kill switch keep running when your
PC is off. Checked 2026-10-18; free offers change, so re-check before relying
on them.

| Option | Free for | Size | Notes |
| --- | --- | --- | --- |
| **Oracle Cloud Always Free (Arm)**, recommended | No end date | 2 CPUs, 12 GB RAM, 200 GB disk | Cut from 4 CPUs / 24 GB in mid-2026. Idle free VMs can be reclaimed; popular regions can run out of capacity. |
| Google Cloud e2-micro | No end date | 2 shared CPUs, 1 GB RAM, 30 GB disk | Only us-west1, us-central1, us-east1. Tight for pandas; add swap. |
| AWS (accounts opened after July 2025) | Up to 6 months | Credit-based ($100–$200) | Fine for a trial, not for years. |

## 1. Create the server (you do this; it needs your account)

Ubuntu **24.04**, the smallest free shape (Oracle: `VM.Standard.A1.Flex`
with 2 OCPU / 12 GB). Keep the default firewall: **don't open port 8000** or
any other port; only SSH (22) stays open. Remote access goes through
Tailscale.

## 2. Copy the project and your `.env`

```bash
git clone https://github.com/narendrakumarachari/NDHD-Trading.git
cd NDHD-Trading
nano .env        # paste your .env: paper keys, ALPACA_PAPER=true, LIVE_TRADING=false, DASHBOARD_API_KEY
```

Use paper credentials only. `.env` is git-ignored and never leaves the server.

## 3. Run the setup script

```bash
bash deploy/setup-server.sh              # dashboard + paper engine
bash deploy/setup-server.sh --no-engine  # dashboard only
```

It installs Python 3.14 and the hashed **Linux** lock
(`requirements.lock.linux.txt`, same versions as the Windows lock plus
`uvloop`), builds the dashboard with a checksum-verified Node, runs the
tests, installs two services that restart on crash and on reboot
(`ndhd-api`, `ndhd-engine`), installs Tailscale from its signed apt
repository, asks you to sign the server in (open the printed link), and turns
on Tailscale Serve. It stops if `.env` turns live trading on or has no API
key. Running it again updates everything in place.

At the end it prints the private link, for example
`https://ndhd-server.tailabc.ts.net/`. Open it from any device in your
tailnet ([REMOTE_ACCESS.md](REMOTE_ACCESS.md)).

## 4. Day to day

| Task | Command |
| --- | --- |
| Status | `systemctl status ndhd-api ndhd-engine` |
| Live engine log | `journalctl -u ndhd-engine -f` |
| Stop / start the engine | `sudo systemctl stop ndhd-engine` / `sudo systemctl start ndhd-engine` |
| Update to the latest code | `git pull && bash deploy/setup-server.sh` |
| Robinhood panel data | Snapshots are made on your PC by Claude Code; copy `data/robinhood_snapshot.json` to the server's `data/` folder |

Stopping the engine uses the same signal as Ctrl+C, so it shuts down cleanly
and releases its lock. The dashboard service always restarts, so the kill
switch stays reachable even after a crash.

## Run only one engine

The engine on your PC and the one on the server would trade the same Alpaca
paper account and fight over it. Once the server runs the engine, stop it on
your PC (or start the PC side with `.\scripts\start-ndhd.ps1 -NoEngine`).
