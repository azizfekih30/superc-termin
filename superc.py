"""SuperC Termin watcher: RWTH students, visa extension (Ausländeramt Aachen - Außenstelle RWTH).

Polls the Städteregion Aachen booking system and sends a phone push via ntfy
(and optionally beeps) as soon as an appointment for "RWTH Studenten" appears.

Usage:
    python superc.py           # watch forever
    python superc.py --once    # check once and print the result
"""
import argparse
import datetime as dt
import logging
import os
import random
import sys
import time

import bs4
import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://termine.staedteregion-aachen.de/auslaenderamt/"
START_URL = BASE_URL + "select2?md=1"
SECTION_NEEDLE = "Super C"          # <h3>RWTH - Außenstelle Super C</h3>
CONCERN_NEEDLE = "RWTH Studenten"   # the <li> for students
NO_SLOTS_TEXT = "Kein freier Termin verfügbar"
ALERT_TITLE = "SuperC termin available (RWTH Studenten)!"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

NTFY_TOPIC = os.getenv("NTFY_TOPIC", "").strip()
NTFY_SERVER = os.getenv("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
NTFY_TOKEN = os.getenv("NTFY_TOKEN", "")                     # only for protected topics / self-hosted
INTERVAL = max(30, int(os.getenv("INTERVAL", "60")))         # seconds between checks
FAST_INTERVAL = max(15, int(os.getenv("FAST_INTERVAL", "20")))
FAST_WINDOW = os.getenv("FAST_WINDOW", "")                   # e.g. "07:00-09:30", local time
BEEP = os.getenv("BEEP", "1") == "1"

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger("superc")


class Result:
    def __init__(self, available: bool, slots: list[str], note: str = ""):
        self.available = available
        self.slots = slots
        self.note = note

    def key(self):
        return (self.available, tuple(self.slots))


def _soup(html: str) -> bs4.BeautifulSoup:
    return bs4.BeautifulSoup(html, "html.parser")


def find_student_cnc_id(soup: bs4.BeautifulSoup) -> str:
    """Locate the 'RWTH Studenten' entry under the Super C heading by its label, not its position."""
    header = soup.find("h3", string=lambda s: bool(s) and SECTION_NEEDLE in s)
    if not header:
        raise RuntimeError(f"Heading containing '{SECTION_NEEDLE}' not found - site layout changed?")
    container = header.find_next_sibling()
    for li in container.find_all("li") if container else []:
        if CONCERN_NEEDLE in li.get_text(" ", strip=True) and li.get("id"):
            return li["id"].split("-")[-1]
    raise RuntimeError(f"'{CONCERN_NEEDLE}' not found under '{SECTION_NEEDLE}'")


def parse_suggest_page(html: str) -> Result:
    if NO_SLOTS_TEXT in html:
        return Result(False, [])
    soup = _soup(html)
    accordion = soup.find("div", {"id": "sugg_accordion"})
    if accordion:
        slots = []
        for h3 in accordion.find_all("h3"):
            day = " ".join(h3.get_text(" ", strip=True).split())
            panel = h3.find_next_sibling()
            times = []
            if panel:
                for btn in panel.find_all(["button", "input"]):
                    label = (btn.get("title") or btn.get("value") or btn.get_text(strip=True) or "").strip()
                    if ":" in label and len(label) <= 40:
                        times.append(label)
            slots.append(f"{day}: {', '.join(times[:6])}" if times else day)
        return Result(True, slots)
    summary = soup.find("summary", id="suggest_details_summary")
    if summary:
        return Result(True, [summary.get_text(" ", strip=True)])
    # Page is neither "no slots" nor a known slot layout: alert anyway rather than miss a slot.
    return Result(True, [], note="Unknown page layout - slots may be available, check manually!")


class SuperCChecker:
    def __init__(self):
        self.cnc_id = None

    def check(self) -> Result:
        session = requests.Session()
        session.headers["User-Agent"] = USER_AGENT

        # 1) Start page: sets the session cookie and lists all concerns (Anliegen).
        r = session.get(START_URL, timeout=20)
        r.raise_for_status()
        if self.cnc_id is None:
            self.cnc_id = find_student_cnc_id(_soup(r.text))
            log.info("RWTH Studenten cnc id: %s", self.cnc_id)

        # 2) Location page for "1x RWTH Studenten".
        location_url = f"{BASE_URL}location?mdt=89&select_cnc=1&cnc-{self.cnc_id}=1"
        r = session.get(location_url, timeout=20)
        r.raise_for_status()
        loc_input = _soup(r.text).find("input", {"name": "loc"})
        if not loc_input:
            self.cnc_id = None  # the id may have changed; look it up again next time
            raise RuntimeError("Location form not found")

        # 3) Selecting the SuperC location redirects straight to the suggestion page.
        r = session.post(location_url, data={
            "loc": loc_input["value"],
            "select_location": "Ausländeramt Aachen - Außenstelle RWTH auswählen",
        }, timeout=20)
        r.raise_for_status()
        return parse_suggest_page(r.text)


def format_message(res: Result) -> str:
    lines = list(res.slots)
    if res.note:
        lines.append(res.note)
    lines.append("Tap to book: pick 'RWTH Studenten' under 'RWTH - Außenstelle Super C'.")
    return "\n".join(lines)


def notify(title: str, body: str, priority: str = "default", tags: str = "", click: str = ""):
    """Push a notification via ntfy (https://ntfy.sh). Title must stay ASCII (it is sent as a header)."""
    if not NTFY_TOPIC:
        log.warning("NTFY_TOPIC not set - skipping push notification")
        return
    headers = {"Title": title, "Priority": priority}
    if tags:
        headers["Tags"] = tags
    if click:
        headers["Click"] = click
        headers["Actions"] = f"view, Book now, {click}"
    if NTFY_TOKEN:
        headers["Authorization"] = f"Bearer {NTFY_TOKEN}"
    try:
        r = requests.post(f"{NTFY_SERVER}/{NTFY_TOPIC}", data=body.encode("utf-8"), headers=headers, timeout=15)
        if not r.ok:
            log.error("ntfy error %s: %s", r.status_code, r.text[:200])
    except requests.RequestException as e:
        log.error("ntfy request failed: %s", e)


def beep():
    if not BEEP:
        return
    try:
        import winsound
        for _ in range(3):
            winsound.Beep(1000, 400)
    except Exception:
        print("\a" * 3, end="", flush=True)


def in_fast_window(now: dt.datetime) -> bool:
    if not FAST_WINDOW:
        return False
    try:
        start, end = (dt.time.fromisoformat(p.strip()) for p in FAST_WINDOW.split("-"))
    except ValueError:
        return False
    return start <= now.time() <= end


def watch():
    checker = SuperCChecker()
    last_key = None
    errors = 0
    log.info("Watching SuperC for RWTH Studenten (every %ss%s)", INTERVAL,
             f", every {FAST_INTERVAL}s during {FAST_WINDOW}" if FAST_WINDOW else "")
    while True:
        try:
            res = checker.check()
            errors = 0
            if res.available:
                log.info("AVAILABLE: %s", "; ".join(res.slots) or res.note)
                if res.key() != last_key:  # only notify when the offer changes
                    notify(ALERT_TITLE, format_message(res), priority="urgent",
                           tags="rotating_light,calendar", click=START_URL)
                    beep()
            else:
                log.info("No slots")
                if last_key and last_key[0]:
                    notify("SuperC slots gone", "The SuperC slots are gone again.", priority="low", tags="x")
            last_key = res.key()
        except Exception as e:
            errors += 1
            log.error("Check failed (%d in a row): %s", errors, e)
            if errors == 10:
                notify("SuperC watcher failing", f"10 failed checks in a row. Last error: {e}",
                       priority="high", tags="warning")

        base = FAST_INTERVAL if in_fast_window(dt.datetime.now()) else INTERVAL
        backoff = min(600, base * 2 ** min(errors, 4)) if errors else base
        time.sleep(backoff + random.uniform(0, base * 0.2))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles default to cp1252
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--once", action="store_true", help="check once and exit")
    parser.add_argument("--test-notify", action="store_true", help="send a test push notification and exit")
    args = parser.parse_args()

    if args.test_notify:
        notify("SuperC watcher test", "Notifications work ✅ You'll get an urgent alert like this when slots appear.",
               priority="urgent", tags="white_check_mark", click=START_URL)
        return
    if args.once:
        res = SuperCChecker().check()
        if res.available:
            print(ALERT_TITLE, format_message(res), START_URL, sep="\n")
        else:
            print("No SuperC slots for RWTH Studenten right now.")
        sys.exit(0 if res.available else 1)
    try:
        watch()
    except KeyboardInterrupt:
        log.info("Stopped")


if __name__ == "__main__":
    main()
