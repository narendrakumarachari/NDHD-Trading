# Running NDHD 24/7 on Oracle Cloud (free)

So the dashboard, the paper engine and the kill switch keep running when
your PC is off or asleep.

## How it works, simply

Right now NDHD lives in your PC, like a shop that's only open while you're
home. We're renting a small free "shop" in Oracle's building that never
closes. The same three things move there: the engine (the worker), the
dashboard (the window), and the kill switch (the big red button). Your phone
and PC reach it through the same private Tailscale tunnel as before, so it is
still invisible to everyone else on the internet.

Only one worker may trade the account at a time, so the move ends with the
PC's engine switched off and its save file handed to the server.

## Which free server

Checked 2026-10-18; free offers change, so re-check before relying on them.

| Option | Free for | Size | Notes |
| --- | --- | --- | --- |
| **Oracle Cloud Always Free (Arm)**, recommended | No end date | 2 CPUs, 12 GB RAM, 200 GB disk | Cut from 4 CPUs / 24 GB in mid-2026. Popular regions can run out of free capacity. |
| Google Cloud e2-micro | No end date | 2 shared CPUs, 1 GB RAM, 30 GB disk | Only us-west1, us-central1, us-east1. Tight for pandas; add swap. |
| AWS (accounts opened after July 2025) | Up to 6 months | Credit-based ($100–$200) | Fine for a trial, not for years. |

**Two Oracle cautions.** Your *home region* is chosen once at sign-up and
can't be changed, and Always Free servers are only created there. Oracle
may also reclaim an Always Free server that stays almost idle for a week;
NDHD uses very little CPU, so that can happen. Upgrading the account to
*Pay As You Go* removes that rule while Always Free resources stay free.
If you do it, set a budget alert of $1 (Billing > Budgets) so any charge is
noticed at once. Read Oracle's current terms before deciding.

## Steps

Steps marked **(you)** need your accounts, card or clicks; Claude can't
create accounts or enter card details. Everything else is a command.

### 1. Create the Oracle account (you)

<https://signup.cloud.oracle.com/>: email, phone, and a card for identity
verification (Always Free use isn't charged). Pick a home region near you.

### 2. Create the server (you)

Oracle console > **Compute > Instances > Create instance**:

| Setting | Choose |
| --- | --- |
| Name | `ndhd-server` |
| Image | **Canonical Ubuntu 24.04** (the aarch64 / Arm build) |
| Shape | **Ampere > VM.Standard.A1.Flex**, 2 OCPUs, 12 GB memory (shows "Always Free-eligible") |
| Networking | defaults (public subnet, assign a public IPv4 address) |
| SSH keys | **Generate a key pair for me** > **Save private key** |
| Boot volume | default |

Save the private key as `C:\Users\<you>\.ssh\oracle-ndhd.key`. Keep the
default firewall: only SSH (port 22) is open. **Don't open port 8000** or
any other port; the dashboard is reached through Tailscale only.

*"Out of capacity"*: try another availability domain on the same page, or
try again later (capacity frees up during the day).

Note the instance's **public IP address** once it shows *Running*.

### 3. Connect from your PC

Windows' SSH refuses a key file others can read, so lock it to you first:

```powershell
icacls $HOME\.ssh\oracle-ndhd.key /inheritance:r /grant:r "$($env:USERNAME):R"
ssh -i $HOME\.ssh\oracle-ndhd.key ubuntu@<public-ip>     # type "yes" the first time
```

### 4. Download NDHD on the server

In that SSH window:

```bash
git clone https://github.com/narendrakumarachari/NDHD-Trading.git
exit
```

### 5. Copy your private files (PC)

`.env` and the Robinhood snapshot are git-ignored, so copy them over SSH.
Nothing is printed.

```powershell
.\scripts\sync-to-server.ps1 -Server <public-ip> -Key $HOME\.ssh\oracle-ndhd.key -DotEnv -Snapshot
```

### 6. Set up the server, dashboard only for now

```bash
ssh -i $HOME\.ssh\oracle-ndhd.key ubuntu@<public-ip>
cd NDHD-Trading && bash deploy/setup-server.sh --no-engine
```

It installs Python 3.14 and the hashed **Linux** lock
(`requirements.lock.linux.txt`, same versions as the Windows lock plus
`uvloop`), builds the dashboard with a checksum-verified Node, runs all the
tests, installs the services (`ndhd-api`, `ndhd-engine`) that restart on
crash and on reboot, installs Tailscale from its signed repository and
turns on Tailscale Serve. It stops if `.env` turns live trading on or has
no API key.

When it prints a Tailscale link, open it and sign in **with the same
account as your PC and phone** (you). Then, in
<https://login.tailscale.com/admin/machines>, open **ndhd-server > ⋯ >
Disable key expiry** (you).

### 7. Switch trading from the PC to the server (PC)

Outside market hours:

```powershell
.\scripts\switch-to-server.ps1 -Key $HOME\.ssh\oracle-ndhd.key
```

It checks the server's dashboard answers over Tailscale and that its engine
isn't running yet. It then stops the PC's engine and dashboard, removes the
PC's start-at-sign-in task, stops the PC sharing its dashboard, and copies
the engine's save file to the server. Then it prints the last step.

### 8. Start the server's engine

```bash
ssh -i $HOME\.ssh\oracle-ndhd.key ubuntu@ndhd-server
sudo systemctl enable --now ndhd-engine
```

### 9. Use the new address everywhere (you)

Open `https://ndhd-server.<your-tailnet>.ts.net/` on your phone and PC,
and paste the API key in ⚙ Settings once on each (a new address means a new
browser entry). The old PC address stops working, so nobody can press a
kill switch that no longer controls the engine.

### Optional: close SSH to the internet

Once step 8 works, SSH also works over Tailscale (`ubuntu@ndhd-server`).
In the Oracle console (**Networking > Virtual cloud networks > your VCN >
Security lists > Default**), remove the ingress rule for port 22. The
server then has no open port on the internet at all.

## Day to day

| Task | Command |
| --- | --- |
| Status | `systemctl status ndhd-api ndhd-engine` |
| Live engine log | `journalctl -u ndhd-engine -f` |
| Stop / start the engine | `sudo systemctl stop ndhd-engine` / `sudo systemctl start ndhd-engine` |
| Update to the latest code | `cd NDHD-Trading && git pull && bash deploy/setup-server.sh` |
| Robinhood panel data | After Claude refreshes the snapshot on your PC: `.\scripts\sync-to-server.ps1 -Server ndhd-server -Key $HOME\.ssh\oracle-ndhd.key -Snapshot` |
| Move trading back to the PC | On the server `sudo systemctl disable --now ndhd-engine`, copy its save file back, then on the PC `.\scripts\switch-to-server.ps1 -Undo` |

Stopping the engine uses the same signal as Ctrl+C, so it shuts down cleanly
and releases its lock. The dashboard service always restarts, so the kill
switch stays reachable even after a crash.

## Run only one engine

Two engines would trade the same Alpaca paper account and fight over it,
each with its own save file and kill switch. `switch-to-server.ps1` enforces
the order: the server's engine must be off while the PC hands over, and the
PC's engine, dashboard and autostart are off before the server's engine
starts.
