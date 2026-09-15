"""
Nallisport tennis court watcher.

Checks nallisport.cintoia.com for 15:00-16:00 court availability on
Mon/Tue/Thu and emails you when a new slot opens up.

STILL TO DO: fill in fetch_availability() once we know the real API
endpoint and response shape (see Part 1 of the chat).
"""

import os
import json
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta

import requests

# ---- Config -----------------------------------------------------------

TARGET_WEEKDAYS = {0, 1, 3}   # Monday=0, Tuesday=1, Thursday=3 (datetime.weekday())
TARGET_START_HOUR = 15        # 15:00
DAYS_AHEAD = 14                # how many days forward to check

SEEN_FILE = "seen_slots.json"  # tracks slots we've already emailed about

FIRESTORE_INDEX_URL = (
    "https://firestore.googleapis.com/v1/projects/falcon-328a1/databases/"
    "(default)/documents/freeindex/nallisport-20FA1VH3/indexes/public"
)

# TODO: fill in once we have the court-id -> name/sport mapping.
# Only court-ids in here are considered; everything else (other sports) is ignored.
TENNIS_COURT_IDS = {
    "8SBqXMilmzCpvGcvm8ot": "Tennis 5",
    "KMs5DvsfeNyAuh7fFz6K": "Tennis 6",
    "WesxXK9z3jk8Ku37FSnT": "Tennis 3 LVI-Pitkälä",
    "nED4IPFSLG2TAFdOQZw4": "Tennis 2 LähiTapiola Pohjoinen",
    "tPWrnMlnzaNi5p2iKkEs": "Tennis 1 Lexia",
    "vt5JoUiXzQIVCirrdqYa": "Tennis 4 Hohde",
}

TARGET_START = "1500"  # matches the "s" field format, e.g. "1500" = 15:00
TARGET_END = "1600"

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = os.environ["SMTP_USER"]          # set as GitHub Actions secret
SMTP_PASS = os.environ["SMTP_PASS"]          # app password, set as secret
NOTIFY_TO = os.environ.get("NOTIFY_TO", SMTP_USER)


# ---- Data fetching ------------------------------------------------------

def get_date_to_s3_url():
    """
    Query the public Firestore index document and return a dict mapping
    date strings ("20250908") to the S3 JSON URL holding that day's
    availability.
    """
    resp = requests.get(FIRESTORE_INDEX_URL, timeout=15)
    resp.raise_for_status()
    doc = resp.json()

    result = {}
    for date_key, field in doc.get("fields", {}).items():
        try:
            url = field["mapValue"]["fields"]["key"]["stringValue"]
        except KeyError:
            continue
        result[date_key] = url
    return result


def relevant_dates(date_to_url):
    """Filter the Firestore date keys down to upcoming Mon/Tue/Thu within DAYS_AHEAD."""
    today = datetime.now().date()
    cutoff = today + timedelta(days=DAYS_AHEAD)

    out = []
    for date_key in date_to_url:
        try:
            d = datetime.strptime(date_key, "%Y%m%d").date()
        except ValueError:
            continue
        if today <= d <= cutoff and d.weekday() in TARGET_WEEKDAYS:
            out.append((date_key, d))
    return out


def fetch_availability():
    """
    Return a list of dicts like:
        {"date": "2026-09-14", "start": "15:00", "court": "Court 1"}
    for tennis slots starting at TARGET_START on upcoming target weekdays.
    """
    date_to_url = get_date_to_s3_url()
    slots = []

    for date_key, d in relevant_dates(date_to_url):
        s3_url = date_to_url[date_key]
        try:
            resp = requests.get(s3_url, timeout=15)
            resp.raise_for_status()
            day_data = resp.json()
        except requests.RequestException as e:
            print(f"Warning: failed to fetch {s3_url}: {e}")
            continue

        for court_id, court_slots in day_data.items():
            if court_id not in TENNIS_COURT_IDS:
                continue
            court_name = TENNIS_COURT_IDS[court_id]

            for slot in court_slots:
                if slot.get("s") == TARGET_START and slot.get("e") == TARGET_END:
                    slots.append({
                        "date": d.strftime("%Y-%m-%d"),
                        "start": f"{TARGET_START[:2]}:{TARGET_START[2:]}",
                        "court": court_name,
                    })

    return slots


# ---- Filtering ------------------------------------------------------------

def matches_target(slot):
    d = datetime.strptime(slot["date"], "%Y-%m-%d")
    hour = int(slot["start"].split(":")[0])
    return d.weekday() in TARGET_WEEKDAYS and hour == TARGET_START_HOUR


def slot_key(slot):
    return f'{slot["date"]}_{slot["start"]}_{slot["court"]}'


# ---- Seen-slot tracking (so we don't re-notify) ---------------------------

def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE) as f:
            return set(json.load(f))
    return set()


def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(sorted(seen), f, indent=2)


# ---- Email ------------------------------------------------------------

def send_email(new_slots):
    body_lines = [f'{s["date"]} ({datetime.strptime(s["date"], "%Y-%m-%d").strftime("%A")}) '
                  f'{s["start"]} - {s["court"]}' for s in new_slots]
    body = "New Nallisport court slots available:\n\n" + "\n".join(body_lines)

    msg = MIMEText(body)
    msg["Subject"] = f"🎾 {len(new_slots)} Nallisport court slot(s) open"
    msg["From"] = SMTP_USER
    msg["To"] = NOTIFY_TO

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, [NOTIFY_TO], msg.as_string())


# ---- Main ------------------------------------------------------------

def main():
    seen = load_seen()

    all_slots = fetch_availability()
    target_slots = [s for s in all_slots if matches_target(s)]
    target_keys = {slot_key(s) for s in target_slots}

    new_slots = [s for s in target_slots if slot_key(s) not in seen]

    if new_slots:
        send_email(new_slots)
        print(f"Notified about {len(new_slots)} new slot(s).")
    else:
        print("No new matching slots.")

    # Update seen set: keep only keys still relevant (still in the
    # target window) so stale entries don't pile up forever, and add
    # everything currently open so we don't re-notify on the next run.
    seen = (seen & target_keys) | target_keys
    save_seen(seen)


if __name__ == "__main__":
    main()
