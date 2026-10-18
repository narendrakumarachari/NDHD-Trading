# Private access from your laptop and phone (Tailscale)

Open the NDHD dashboard, including the **kill switch**, from your laptop or
phone, anywhere, without putting it on the public internet.

## How it works, simply

Think of your devices as houses in different towns. Normally, to visit your
PC from your phone, you'd have to open your PC's front door to the whole
street (the internet), and anyone could knock.

**Tailscale builds a private tunnel between your own houses only.** Each
device gets a membership card when you sign in with the same account. Only
devices with your card can use the tunnels. Everyone else doesn't even see a
door.

- **Your tailnet** is your private club: your PC, laptop, phone, and later
  your cloud server.
- **Tailscale Serve** puts the dashboard on that private club's map at an
  address like `https://my-pc.tailabc.ts.net/`, with a padlock (HTTPS).
- **Tailscale Funnel** is different: it opens the door to the whole
  internet. **We never use Funnel** for this dashboard, because it can place
  orders and press the kill switch.
- The tunnels are encrypted (WireGuard). Tailscale's servers only help your
  devices find each other; your dashboard traffic goes device to device.

**Why it's a good fit:** free for personal use, nothing to configure on your
router, no public port, works on Wi-Fi or mobile data, and you can remove a
lost phone from the club in one click.

**What it can't do:** reach a computer that's turned off. While NDHD runs on
this PC, the PC must be on. For 24/7, run NDHD on a free server
([SERVER.md](SERVER.md)) and add it to the same tailnet.

## Setup (one time, about 10 minutes)

### 1. Sign in on this PC

Tailscale is installed (`winget install Tailscale.Tailscale`). Click the
Tailscale icon by the clock, choose **Log in**, and sign in with Google,
Microsoft, GitHub or Apple. That account *is* your tailnet: use the same one
everywhere.

When it asks, turn on **HTTPS certificates** for your tailnet (Serve needs
them for the padlock). You can also do it later in the admin console:
<https://login.tailscale.com/admin/dns>.

### 2. Give the dashboard a password (API key)

Tailscale decides **who can reach** the dashboard; the API key is a second
lock on the dashboard itself. Remote access is refused without it.

From the repository root:

```powershell
.\scripts\new-api-key.ps1
```

It writes a random 43-character key into `.env`, copies it to your
clipboard, and never shows it on screen. If a key already exists it changes
nothing; `-Replace` makes a new one (do that if a device is lost).

Restart the dashboard so it reads the key (Ctrl+C in its window, then
`.\scripts\start-ndhd.ps1`). Then, **on every browser you use, including this
PC's**: open ⚙ Settings in the dashboard and paste the key from `.env`.
Without it, buttons such as the kill switch answer "401 Missing or invalid
X-API-Key".

### 3. Start everything

```powershell
.\scripts\start-ndhd.ps1
```

It checks `.env` is paper-only, starts the dashboard and the engine (each in a
minimized window), turns on Tailscale Serve, and prints your private link:

```text
On this PC:          http://127.0.0.1:8000/
Laptop and phone:    https://my-pc.tailabc.ts.net/
```

### 4. Add your laptop and phone

1. Install Tailscale: Windows/Mac from <https://tailscale.com/download>,
   phones from the App Store or Google Play.
2. Sign in with **the same account** as step 1.
3. Open the private link from step 3, then ⚙ Settings, paste the API key.
   On a phone, "Add to Home Screen" makes it an app-like icon.

That's it. The kill switch is the pink button at the top right.

## Everyday use

| You want to | Do this |
| --- | --- |
| Start NDHD | `.\scripts\start-ndhd.ps1` |
| Start NDHD at every Windows sign-in | `.\scripts\install-autostart.ps1` (undo: `-Remove`) |
| See the private link again | `tailscale serve status` |
| Stop sharing the dashboard (keeps it running locally) | `tailscale serve reset` |
| Remove a lost phone | <https://login.tailscale.com/admin/machines>, then ⋯ > Remove |
| Check nothing is public | `tailscale funnel status` should list nothing |

## Safety rules

- **Never run `tailscale funnel`** for port 8000. The start script warns in
  red if Funnel is on.
- The dashboard still listens on `127.0.0.1` only; Serve passes your tailnet
  traffic to it. Don't change `--host` to `0.0.0.0`.
- Keep `DASHBOARD_API_KEY` secret: it's in `.env` (git-ignored). If a device
  is lost, remove it from the tailnet and change the key.
- Sharing your tailnet with another person (Tailscale lets you invite up to
  six users on the free plan) gives them a way to reach the dashboard too.
  Don't, unless they should have the kill switch.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| Start script: "Tailscale: not signed in" | Click the tray icon, Log in, run the script again |
| Start script: "skipped. Set DASHBOARD_API_KEY" | Step 2 |
| Phone: page doesn't load | Tailscale app on the phone is connected (toggle on)? PC awake? |
| "HTTPS certificates are disabled" | Admin console > DNS > enable HTTPS, then run the script again |
| Buttons say 401 | Paste the API key in ⚙ Settings on that device |
| PC goes to sleep | Windows Settings > Power: set sleep to Never while NDHD runs, or use [SERVER.md](SERVER.md) |
