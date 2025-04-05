import os
import json
import concurrent.futures
from extract_messages import extract_messages
from fetch_users import get_analyzable_users
import signal
import sys
import logging
import time
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Define a global flag for graceful exit
SHUTDOWN_FLAG = False

# Configure the maximum number of worker threads from an environment variable
DEFAULT_MAX_WORKERS = 16 # Default value if not set in .env
MAX_WORKERS = int(os.getenv("MAX_WORKERS", DEFAULT_MAX_WORKERS))
logging.info(f"Using MAX_WORKERS: {MAX_WORKERS}")

def signal_handler(sig, frame):
    """Handles interrupt signals for graceful shutdown."""
    global SHUTDOWN_FLAG
    logging.info("\nScript interrupted. Setting shutdown flag...")
    SHUTDOWN_FLAG = True
    sys.exit(0) # Immediately exit for now, can be refined

signal.signal(signal.SIGINT, signal_handler)

# Folder to save Teams JSON files
os.makedirs("teams_json", exist_ok=True)

def save_messages_to_json(user_upn):
    """Fetches and saves Teams messages for a given user to a JSON file."""
    if SHUTDOWN_FLAG:
        logging.info(f"Skipping {user_upn} due to shutdown signal.")
        return

    try:
        logging.info(f"Fetching Teams messages for {user_upn}...")
        messages = extract_messages(user_upn)

        if SHUTDOWN_FLAG:
            logging.info(f"Stopping processing for {user_upn} after message fetch due to shutdown signal.")
            return

        if not messages:
            logging.info(f"No Teams messages found for {user_upn}.")
            return

        file_path = os.path.join("teams_json", f"teams_{user_upn.replace('@', '_at_')}.json")
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(messages, f, indent=2)

        logging.info(f"Saved {len(messages)} messages for {user_upn} to {file_path}")

    except Exception as e:
        logging.error(f"Error processing {user_upn}: {e}", exc_info=True)

def main():
    """Main function to orchestrate fetching and saving Teams messages for analyzable users."""
    users = get_analyzable_users()

    if not users:
        logging.info("No analyzable users found.")
        return

    logging.info(f"Found {len(users)} analyzable users.")

    # Use threads to fetch in parallel with better control
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_user = {executor.submit(save_messages_to_json, user): user for user in users}
        for future in concurrent.futures.as_completed(future_to_user):
            user = future_to_user[future]
            try:
                future.result()  # Raise any exceptions that occurred in the thread
            except Exception as exc:
                logging.error(f"Thread for {user} generated an exception: {exc}", exc_info=True)
                # Optionally decide whether to continue processing other users

    logging.info("Finished processing analyzable users.")

if __name__ == "__main__":
    main()