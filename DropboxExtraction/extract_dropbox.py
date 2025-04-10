import requests
import hashlib
import logging
import os
import json
from auth_dropbox import get_access_token

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

DROPBOX_LOG_EVENTS_URL = "https://api.dropboxapi.com/2/team_log/get_events"
DROPBOX_LOG_EVENTS_CONTINUE_URL = "https://api.dropboxapi.com/2/team_log/get_events/continue"
EXTRACTED_DIR = "extracted_documents"
STATE_FILE = os.path.join(EXTRACTED_DIR, "state.json")

RELEVANT_EVENT_TYPES = {
    "file_add", "file_delete", "file_edit", "file_rename",
    "folder_add", "folder_delete", "folder_rename",
}

def ensure_directories():
    os.makedirs(EXTRACTED_DIR, exist_ok=True)

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {"cursor": None, "last_batch_number": 0}

def save_state(cursor, batch_number):
    with open(STATE_FILE, "w") as f:
        json.dump({
            "cursor": cursor,
            "last_batch_number": batch_number
        }, f)

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

def get_activity_log():
    logging.info("Fetching Dropbox access token...")
    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    ensure_directories()
    state = load_state()
    cursor = state.get("cursor")
    batch_number = state.get("last_batch_number", 0) + 1

    body = {"cursor": cursor} if cursor else {}

    while True:
        url = DROPBOX_LOG_EVENTS_CONTINUE_URL if cursor else DROPBOX_LOG_EVENTS_URL
        response = requests.post(url, headers=headers, json=body)

        if response.status_code != 200:
            logging.error(f"Error {response.status_code}: {response.text}")
            break

        data = response.json()
        events = data.get("events", [])
        cursor = data.get("cursor")
        has_more = data.get("has_more", False)

        logging.info(f"Fetched {len(events)} events for batch {batch_number}")

        current_batch = []

        for event in events:
            try:
                event_type = event.get("event_type", {}).get(".tag")
                if event_type not in RELEVANT_EVENT_TYPES:
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
                logging.warning(f"Skipping malformed event: {e}")

        if current_batch:
            output_file = os.path.join(EXTRACTED_DIR, f"{batch_number}.json")
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(current_batch, f, indent=2)
            logging.info(f"Saved batch {batch_number} with {len(current_batch)} events")

            batch_number += 1

        save_state(cursor, batch_number)

        if has_more and cursor:
            body = {"cursor": cursor}
        else:
            logging.info("No more events. Exiting.")
            break

if __name__ == "__main__":
    get_activity_log()
