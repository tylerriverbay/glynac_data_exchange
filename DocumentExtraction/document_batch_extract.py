import os
import sys
import json
import signal
import logging
import concurrent.futures
from datetime import datetime
import requests
import threading
import time

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fetch_users import get_analyzable_users
from auth_token import get_access_token
from config import GRAPH_API_ENDPOINT

# ──────────────────────────────────────────────
# Setup logging
# ──────────────────────────────────────────────

LOG_FORMAT = "[%(asctime)s] [%(levelname)s] [%(threadName)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Graceful shutdown (Ctrl+C)
# ──────────────────────────────────────────────

def signal_handler(sig, frame):
    logger.warning("Interrupted. Exiting...")
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)

# ──────────────────────────────────────────────
# Ensure output directory
# ──────────────────────────────────────────────

os.makedirs("document_json", exist_ok=True)

# ──────────────────────────────────────────────
# Helpers for Graph API
# ──────────────────────────────────────────────

def fetch_paginated_results(url, headers, max_retries=3, backoff=2):
    all_results = []
    retries = 0

    while url:
        try:
            response = requests.get(url, headers=headers)
            if response.status_code == 200:
                data = response.json()
                all_results.extend(data.get("value", []))
                url = data.get("@odata.nextLink")
                retries = 0  # reset retry count after successful request
            elif response.status_code >= 500:
                if retries < max_retries:
                    logger.warning(f"Retrying ({retries+1}/{max_retries}) after 500 error on: {url}")
                    retries += 1
                    time.sleep(backoff ** retries)
                    continue
                else:
                    logger.error(f"Max retries reached for URL: {url} — Status code: {response.status_code}")
                    break
            else:
                logger.error(f"Failed to fetch URL: {url} — Status code: {response.status_code}")
                break
        except Exception as e:
            logger.error(f"Exception while fetching URL: {url} — {e}")
            break

    return all_results

def get_user_documents(user_upn, headers):
    url = f"{GRAPH_API_ENDPOINT}/users/{user_upn}/drive/root/children"
    return fetch_paginated_results(url, headers)

def get_user_shared_files(user_upn, headers):
    url = f"{GRAPH_API_ENDPOINT}/users/{user_upn}/drive/sharedWithMe"
    return fetch_paginated_results(url, headers)

def get_drive_item_activities(drive_id, item_id, headers):
    url = f"{GRAPH_API_ENDPOINT}/drives/{drive_id}/items/{item_id}/activities"
    return fetch_paginated_results(url, headers)

# ──────────────────────────────────────────────
# Document & Activity extraction
# ──────────────────────────────────────────────

def save_document_json(user):
    try:
        thread_name = threading.current_thread().name

        if isinstance(user, str):
            user_upn = user
        else:
            user_upn = user.get("userPrincipalName")
            if user.get("status") != "active":
                logger.info(f"{user_upn} is inactive. Skipping...")
                return

        logger.info(f"Started processing: {user_upn}")

        access_token = get_access_token()
        headers = {"Authorization": f"Bearer {access_token}"}

        personal_files = get_user_documents(user_upn, headers)
        shared_files = get_user_shared_files(user_upn, headers)
        all_files = personal_files + shared_files

        activity_data = []
        total_activities = 0

        for file in all_files:
            item_id = file.get("id")
            file_name = file.get("name")
            parent_reference = file.get("parentReference", {})
            drive_id = parent_reference.get("driveId")

            if not drive_id or not item_id:
                continue

            activities = get_drive_item_activities(drive_id, item_id, headers)
            is_folder = "folder" in file

            last_modified_time = file.get("lastModifiedDateTime", "Unknown")
            last_modified_by = file.get("lastModifiedBy", {}).get("user", {}).get("displayName", "Unknown")

            entry = {
                "userPrincipalName": user_upn,
                "fileName": file_name,
                "itemId": item_id,
                "isFolder": is_folder,
                "fileType": file.get("file", {}).get("mimeType", "Unknown") if not is_folder else "folder",
                "fileSizeBytes": file.get("size", "Unknown") if not is_folder else "N/A",
                "lastModified": last_modified_time,
                "lastModifiedBy": last_modified_by,
                "extractedAt": datetime.utcnow().isoformat(),
                "activityCount": len(activities),
                "activities": [],
            }

            for act in activities:
                if not isinstance(act, dict):
                    logger.warning(f"Invalid activity format in file {file_name}")
                    continue

                action = act.get("action")
                actor = act.get("actor", {})
                actor_user = actor.get("user", {}) if isinstance(actor, dict) else {}

                entry["activities"].append({
                    "activityId": act.get("id", "Unknown"),
                    "actionType": list(action.keys())[0] if isinstance(action, dict) and action else "Unknown",
                    "performedBy": actor_user.get("displayName", "Unknown"),
                    "userPrincipalName": actor_user.get("userPrincipalName", "Unknown"),
                    "email": actor_user.get("email", "Unknown"),
                    "timestamp": act.get("times", {}).get("recordedDateTime", "Unknown")
                })

            total_activities += len(entry["activities"])
            activity_data.append(entry)

        filename = os.path.join("document_json", f"document_{user_upn.replace('@', '_at_')}.json")
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(activity_data, f, indent=2)

        logger.info(f"✅ Completed: {user_upn} — Files: {len(all_files)}, Activities: {total_activities}")

    except Exception as e:
        logger.error(f"❌ Error processing {user_upn}: {e}", exc_info=True)
        error_path = os.path.join("document_json", f"document_{user_upn.replace('@', '_at_')}_error.json")
        with open(error_path, "w") as f:
            json.dump({"error": str(e)}, f, indent=2)

# ──────────────────────────────────────────────
# Entry Point
# ──────────────────────────────────────────────

def main():
    start_time = time.perf_counter()

    users = get_analyzable_users()
    logger.info(f"Total users fetched: {len(users)}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
        executor.map(save_document_json, users)

    end_time = time.perf_counter()
    elapsed_time = end_time - start_time
    logger.info(f"All users processed in {elapsed_time:.2f} seconds.")

if __name__ == "__main__":
    main()
    logger.info("Document extraction completed.")
    logger.info("Exiting...")
