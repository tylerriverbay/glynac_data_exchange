import os
import json
import psycopg2
import config
import concurrent.futures
import logging
from dotenv import load_dotenv

load_dotenv()
DB_INSERT_BATCH_SIZE = int(os.getenv("DB_INSERT_BATCH_SIZE", 100))
MAX_DB_WORKERS = int(os.getenv("MAX_DB_WORKERS", 16))
SAVED_CHAT_ID_FILE = "saved_chat_ids.json"

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

TEAMS_JSON_FOLDER = "teams_json"

def connect_db():
    return psycopg2.connect(
        host=config.DB_HOST,
        dbname=config.DB_NAME,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        port=config.DB_PORT,
        sslmode="require"
    )

def load_saved_chat_ids():
    """Loads previously saved chat IDs from a JSON file."""
    if os.path.exists(SAVED_CHAT_ID_FILE):
        with open(SAVED_CHAT_ID_FILE, "r") as f:
            try:
                return set(json.load(f))
            except json.JSONDecodeError:
                logging.warning(f"Error decoding {SAVED_CHAT_ID_FILE}. Starting with an empty set.")
                return set()
    return set()

def save_saved_chat_ids(saved_ids):
    """Saves the set of processed chat IDs to a JSON file."""
    with open(SAVED_CHAT_ID_FILE, "w") as f:
        json.dump(list(saved_ids), f)

def load_messages_from_json(folder, saved_chat_ids):
    all_messages = []
    file_chat_counts = {}
    total_chats = 0
    for file_name in os.listdir(folder):
        if file_name.endswith(".json"):
            file_path = os.path.join(folder, file_name)
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    count = len(data)
                    file_chat_counts[file_name] = count
                    total_chats += count
                    for message in data:
                        if message.get("chat_id") not in saved_chat_ids:
                            all_messages.append(message)
            except Exception as e:
                logging.error(f"Error loading {file_name}: {e}", exc_info=True)

    for file, count in file_chat_counts.items():
        logging.info(f"File '{file}' contains {count} chats.")

    logging.info(f"Total chats found in JSON files: {total_chats}")
    logging.info(f"Loaded {len(all_messages)} new messages from JSON files.")
    return all_messages

def insert_batch(batch):
    """Inserts a batch of messages into the database."""
    if not batch:
        logging.info("No messages to insert in this batch.")
        return
    conn = None
    try:
        conn = connect_db()
        with conn.cursor() as cur:
            insert_query = """
            INSERT INTO chat_data_field_test (
                platform,
                chat_id,
                chat_from,
                channel,
                message,
                thread_id,
                timestamp,
                mentioned_users,
                date_extracted
            )
            VALUES (%(platform)s, %(chat_id)s, %(chat_from)s, %(channel)s,
                    %(message)s, %(thread_id)s, %(timestamp)s,
                    %(mentioned_users)s, %(date_extracted)s)
            ON CONFLICT (chat_id) DO NOTHING;
            """
            cur.executemany(insert_query, batch)
            conn.commit()
            logging.info(f"Inserted {len(batch)} messages into database.")
            return [item['chat_id'] for item in batch]  # Return the chat_ids of the inserted batch
    except Exception as e:
        if conn:
            conn.rollback()
        logging.error(f"Failed to insert batch of messages: {e}", exc_info=True)
        return []
    finally:
        if conn:
            conn.close()

def main():
    logging.info("Starting Teams message import from JSON with batching, threading, and ID tracking...")
    saved_chat_ids = load_saved_chat_ids()
    logging.info(f"Loaded {len(saved_chat_ids)} previously saved chat IDs.")

    all_new_messages = load_messages_from_json(TEAMS_JSON_FOLDER, saved_chat_ids)
    num_new_messages = len(all_new_messages)
    logging.info(f"Found {num_new_messages} new messages to process.")

    if not all_new_messages:
        logging.info("No new messages to process.")
        return

    # Create batches of messages
    batches = [all_new_messages[i:i + DB_INSERT_BATCH_SIZE] for i in range(0, num_new_messages, DB_INSERT_BATCH_SIZE)]
    num_batches = len(batches)
    logging.info(f"Created {num_batches} batches of size {DB_INSERT_BATCH_SIZE}.")

    inserted_chat_ids = set()
    # Use a thread pool to insert batches in parallel
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_DB_WORKERS) as executor:
        futures = [executor.submit(insert_batch, batch) for batch in batches]
        for future in concurrent.futures.as_completed(futures):
            try:
                result = future.result()
                if result:
                    inserted_chat_ids.update(result)
            except Exception as exc:
                logging.error(f"Thread generated an exception: {exc}", exc_info=True)

    # Update the saved chat IDs with the newly inserted ones
    saved_chat_ids.update(inserted_chat_ids)
    save_saved_chat_ids(saved_chat_ids)
    logging.info(f"Successfully processed {len(inserted_chat_ids)} new messages. Total saved chat IDs: {len(saved_chat_ids)}.")
    logging.info("Finished importing Teams messages into the database.")

if __name__ == "__main__":
    main()