# SuperC Termin Watcher

Watches the Ausländeramt Aachen booking system for **"RWTH Studenten"** appointments at the
**Außenstelle RWTH (SuperC, Templergraben 57)**, the ones RWTH students need for a visa
extension, and sends an urgent push notification to your phone via [ntfy](https://ntfy.sh)
(and beeps) as soon as one shows up. No account or token needed.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
```

1. Install the **ntfy** app ([Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy) / [iOS](https://apps.apple.com/app/ntfy/id1625396347)).
2. Make up a long random topic name, e.g. `superc-yourname-8f3k2q`. Anyone who knows it can read
   your alerts, so don't use something guessable. Put it in `NTFY_TOPIC` in `.env`.
3. In the app, tap **+** and subscribe to that topic (server `ntfy.sh`).
4. Check that it works: `python superc.py --test-notify`

Alerts are sent with **urgent** priority, so they make a loud, persistent notification. On Android
you can let ntfy override Do Not Disturb in the topic's notification settings. Tapping the
notification (or **Book now**) opens the booking page.

## Run

```bash
python superc.py          # watch continuously
python superc.py --once   # check once (exit code 0 = slots available)
```

Leave it running on your laptop or a small server. When slots appear you get a push with the
dates and times and the booking link. Then go to the link, pick **RWTH Studenten** under
**RWTH - Außenstelle Super C**, and book quickly.

## Settings (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `NTFY_TOPIC` | empty | Your ntfy topic (required for push notifications) |
| `NTFY_SERVER` | `https://ntfy.sh` | Change only if you self-host ntfy |
| `NTFY_TOKEN` | empty | Access token for protected topics / self-hosted servers |
| `INTERVAL` | `60` | Seconds between checks (minimum 30) |
| `FAST_WINDOW` | empty | Local time window with faster checks, e.g. `07:00-09:30` |
| `FAST_INTERVAL` | `20` | Seconds between checks inside `FAST_WINDOW` (minimum 15) |
| `BEEP` | `1` | Beep on this computer when slots appear |

## How it works

Each check does 3 requests: open the start page (session cookie and the id of the
"RWTH Studenten" entry, found by its label), open the location page, and select the SuperC
location, which goes straight to the suggestion page. It then reads the offered dates and times.

- You get a push only when the offer changes, so the same slots don't spam you.
- If the page looks different from both "no slots" and the known slot layout, it alerts anyway, so a layout change doesn't make it miss slots.
- Failed checks back off up to 10 minutes. After 10 failures in a row you get a warning push.
