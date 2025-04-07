import os
import sys
import json
import signal
import concurrent.futures
import logging
import threading
import time
import traceback

# === Constants ===
MAX_WORKERS = 16
OUTPUT_DIR = "calendar_json"
LOG_FILE = "calendar_extraction.log"

# === Setup Path for Imports ===
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fetch_users import get_analyzable_users
from extract_calendars import get_calendar_events

# === Threading Lock for writing files/logging safely if needed ===
file_lock = threading.Lock()

# === Logging Configuration ===
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - [%(threadName)s] - %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# === Signal Handler for Ctrl+C graceful shutdown ===
def signal_handler(sig, frame):
    logger.info("Interrupted. Exiting gracefully...")
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)

# === Ensure Output Directory Exists ===
os.makedirs(OUTPUT_DIR, exist_ok=True)

# === Worker Function ===
def save_calendar_json(user_upn):
    start_time = time.time()
    try:
        logger.info(f"Fetching calendar events for {user_upn}")
        events = get_calendar_events(user_upn)

        file_path = os.path.join(OUTPUT_DIR, f"calendar_{user_upn.replace('@', '_at_')}.json")
        with file_lock:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(events or [], f, indent=2)

        logger.info(f"Saved {len(events) if events else 0} events for {user_upn} in {time.time() - start_time:.2f}s")

    except Exception as e:
        logger.error(f"Error processing {user_upn}: {e}")
        logger.debug(traceback.format_exc())

        error_path = os.path.join(OUTPUT_DIR, f"calendar_{user_upn.replace('@', '_at_')}_error.json")
        with file_lock:
            with open(error_path, "w", encoding="utf-8") as f:
                json.dump({"error": str(e), "trace": traceback.format_exc()}, f, indent=2)

# === Main Entry Point ===
def main():
    users = get_analyzable_users()
    if not users:
        logger.warning("No users found.")
        return

    logger.info(f"Starting calendar extraction for {len(users)} users with {MAX_WORKERS} threads.")

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="CalendarWorker") as executor:
        executor.map(save_calendar_json, users)

    logger.info("Calendar extraction complete.")

if __name__ == "__main__":
    main()
