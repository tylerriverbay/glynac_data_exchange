import requests
import hashlib
import logging
import os
import json
import uuid
from datetime import datetime, timedelta
from auth_dropbox import get_access_token
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

# Constants
DROPBOX_LOG_EVENTS_URL = "https://api.dropboxapi.com/2/team_log/get_events"
DROPBOX_LOG_EVENTS_CONTINUE_URL = (
    "https://api.dropboxapi.com/2/team_log/get_events/continue"
)
EXTRACTED_DIR = "extracted_batch_documents"
EVENT_TYPES_FILE = os.path.join(EXTRACTED_DIR, "all_event_types.json")

RELEVANT_EVENT_TYPES = {
    "file_add_comment",
    "file_resolve_comment",
    "file_transfers_file_add",
    "file_transfers_transfer_download",
    "file_transfers_transfer_send",
    "file_transfers_transfer_view",
    "shared_folder_create",
    "shared_folder_mount",
    "shared_folder_nest",
    "shared_folder_unmount",
    "shared_folder_change_members_management_policy",
    "shared_folder_change_members_policy",
    "shared_folder_transfer_ownership",
    "shared_content_add_invitees",
    "shared_content_add_member",
    "shared_content_change_invitee_role",
    "shared_content_change_member_role",
    "shared_content_claim_invitation",
    "shared_content_copy",
    "shared_content_download",
    "shared_content_relinquish_membership",
    "shared_content_remove_invitees",
    "shared_content_remove_member",
    "shared_content_request_access",
    "shared_content_unshare",
    "shared_content_view",
    "shared_link_create",
    "shared_link_copy",
    "shared_link_download",
    "shared_link_view",
    "shared_link_add_expiry",
    "shared_link_change_visibility",
    "shared_link_disable",
    "shared_link_settings_add_expiration",
    "shared_link_settings_add_password",
    "shared_link_settings_allow_download_disabled",
    "shared_link_settings_change_expiration",
    "shared_link_settings_change_password",
    "team_folder_rename",
}


# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

# Global Set and Lock for event types
event_type_lock = Lock()
if os.path.exists(EVENT_TYPES_FILE):
    with open(EVENT_TYPES_FILE, "r") as f:
        ALL_EVENT_TYPES = set(json.load(f))
else:
    ALL_EVENT_TYPES = set()


def ensure_directories():
    os.makedirs(EXTRACTED_DIR, exist_ok=True)


def save_state(date_str, cursor, batch_number):
    date_dir = os.path.join(EXTRACTED_DIR, date_str)
    os.makedirs(date_dir, exist_ok=True)
    state_file = os.path.join(date_dir, "state.json")
    with open(state_file, "w") as f:
        json.dump(
            {"date": date_str, "cursor": cursor, "last_batch_number": batch_number}, f
        )


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
        event.get("actor", {}).get("admin") or event.get("actor", {}).get("user") or {}
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

    body = {
        "time": {
            "start_time": f"{date_str}T00:00:00Z",
            "end_time": f"{date_str}T23:59:59Z",
        }
    }
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

        logging.info(
            f"[{date_str}] Fetched {len(events)} events for batch {batch_number}"
        )

        current_batch = []
        for event in events:
            try:
                event_type = event.get("event_type", {}).get(".tag")

                # Track all event types, not just relevant ones
                with event_type_lock:
                    if event_type not in ALL_EVENT_TYPES:
                        ALL_EVENT_TYPES.add(event_type)
                        logging.info(f"[{date_str}] New event type found: {event_type}")

                if event_type not in RELEVANT_EVENT_TYPES:
                    logging.info(
                        f"[{date_str}] Skipping irrelevant event type: {event_type}"
                    )
                    continue

                asset = next(
                    (
                        a
                        for a in event.get("assets", [])
                        if a.get(".tag") in {"file", "folder"}
                    ),
                    {},
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

        if current_batch:
            date_dir = os.path.join(EXTRACTED_DIR, date_str)
            os.makedirs(date_dir, exist_ok=True)
            filename = os.path.join(
                date_dir, f"{date_str}_batch_{batch_number}_{uuid.uuid4().hex[:6]}.json"
            )
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(current_batch, f, indent=2)
            logging.info(
                f"[{date_str}] Saved batch {batch_number} ({len(current_batch)} events)"
            )

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

    # Save updated event types only if new ones were added
    if os.path.exists(EVENT_TYPES_FILE):
        with open(EVENT_TYPES_FILE, "r") as f:
            existing_types = set(json.load(f))
    else:
        existing_types = set()

    new_types = ALL_EVENT_TYPES - existing_types
    if new_types:
        combined = sorted(existing_types.union(new_types))
        with open(EVENT_TYPES_FILE, "w") as f:
            json.dump(combined, f, indent=2)
        logging.info(f"Updated event types. Added {len(new_types)} new types.")
    else:
        logging.info("No new event types to update.")


if __name__ == "__main__":
    start = datetime.strptime("2022-07-10", "%Y-%m-%d")
    end = datetime.strptime("2025-04-30", "%Y-%m-%d")
    run_parallel_fetch(start, end)
