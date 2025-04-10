import requests
import hashlib
import logging
import os
import json
import uuid
from datetime import datetime, timedelta
from auth_dropbox import get_access_token
from concurrent.futures import ThreadPoolExecutor

# Constants
DROPBOX_LOG_EVENTS_URL = "https://api.dropboxapi.com/2/team_log/get_events"
DROPBOX_LOG_EVENTS_CONTINUE_URL = "https://api.dropboxapi.com/2/team_log/get_events/continue"
EXTRACTED_DIR = "extracted_batch_documents"

RELEVANT_EVENT_TYPES = {
    "file_add", "file_delete", "file_edit", "file_rename",
    "folder_add", "folder_delete", "folder_rename",
}

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

def ensure_directories():
    os.makedirs(EXTRACTED_DIR, exist_ok=True)

def save_state(date_str, cursor, batch_number):
    date_dir = os.path.join(EXTRACTED_DIR, date_str)
    os.makedirs(date_dir, exist_ok=True)
    state_file = os.path.join(date_dir, "state.json")
    with open(state_file, "w") as f:
        json.dump({
            "date": date_str,
            "cursor": cursor,
            "last_batch_number": batch_number
        }, f)

def load_state(date_str):
    state_file = os.path.join(EXTRACTED_DIR, date_str, "state.json")
    if os.path.exists(state_file):
        with open(state_file, "r") as f:
            return json.load(f)
    return {"cursor": None, "last_batch_number": 0}

def generate_event_hash(event):
    event_type = event.get("event_type", {}).get(".tag", "")
    timestamp = event.get("timestamp", "")
    actor = (
        event.get("actor", {}).get("admin")
        or event.get("actor", {}).get("user")
        or {}
    )
    actor_email = actor.get("email", "")
    asset = next(
        (a for a in event.get("assets", []) if a.get(".tag") in {"file", "folder"}),
        {},
    )
    path = asset.get("path", {}).get("contextual", "")
    display_name = asset.get("display_name", "")

    raw_string = f"{timestamp}|{event_type}|{actor_email}|{path}|{display_name}"
    return hashlib.sha256(raw_string.encode()).hexdigest()

def fetch_events_for_date(date_str):
    logging.info(f"[{date_str}] Starting fetch thread")
    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    ensure_directories()
    state = load_state(date_str)
    cursor = state.get("cursor")
    batch_number = state.get("last_batch_number", 0) + 1

    body = {"time": {"start_time": f"{date_str}T00:00:00Z", "end_time": f"{date_str}T23:59:59Z"}}
    if cursor:
        body = {"cursor": cursor}

    while True:
        url = DROPBOX_LOG_EVENTS_CONTINUE_URL if cursor else DROPBOX_LOG_EVENTS_URL
        response = requests.post(url, headers=headers, json=body)

        if response.status_code != 200:
            logging.error(f"[{date_str}] Error {response.status_code}: {response.text}")
            break

        data = response.json()
        events = data.get("events", [])
        cursor = data.get("cursor")
        has_more = data.get("has_more", False)

        logging.info(f"[{date_str}] Fetched {len(events)} events for batch {batch_number}")

        current_batch = []
        for event in events:
            try:
                event_type = event.get("event_type", {}).get(".tag")
                if event_type not in RELEVANT_EVENT_TYPES:
                    logging.info(f"[{date_str}] Skipping irrelevant event type: {event_type}")
                    continue

                asset = next(
                    (a for a in event.get("assets", []) if a.get(".tag") in {"file", "folder"}),
                    {}
                )
                actor = (
                    event.get("actor", {}).get("admin")
                    or event.get("actor", {}).get("user")
                    or {}
                )

                event_data = {
                    "event_id": generate_event_hash(event),
                    "timestamp": event.get("timestamp"),
                    "event_type": event_type,
                    "asset_type": asset.get(".tag"),
                    "name": asset.get("display_name"),
                    "path": asset.get("path", {}).get("contextual"),
                    "actor_name": actor.get("display_name"),
                    "actor_email": actor.get("email"),
                }

                current_batch.append(event_data)
            except Exception as e:
                logging.warning(f"[{date_str}] Skipping malformed event: {e}")

        print(current_batch)

        if current_batch:
            date_dir = os.path.join(EXTRACTED_DIR, date_str)
            os.makedirs(date_dir, exist_ok=True)
            filename = os.path.join(date_dir, f"{date_str}_batch_{batch_number}_{uuid.uuid4().hex[:6]}.json")
            print(filename)
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(current_batch, f, indent=2)
            logging.info(f"[{date_str}] Saved batch {batch_number} ({len(current_batch)} events)")

            batch_number += 1

        save_state(date_str, cursor, batch_number)

        if has_more and cursor:
            body = {"cursor": cursor}
        else:
            logging.info(f"[{date_str}] Completed fetching")
            break

def run_parallel_fetch(start_date, end_date, max_threads=8):
    dates = []
    current = start_date
    while current <= end_date:
        dates.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)

    with ThreadPoolExecutor(max_workers=max_threads) as executor:
        executor.map(fetch_events_for_date, dates)

if __name__ == "__main__":
    start = datetime.strptime("2024-01-01", "%Y-%m-%d")
    end = datetime.strptime("2024-01-02", "%Y-%m-%d")
    run_parallel_fetch(start, end)
