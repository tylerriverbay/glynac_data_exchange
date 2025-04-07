import psycopg2
import psycopg2.extras
import config
import json
import os
import threading
from queue import Queue

# --- Configuration ---
NUM_THREADS = 5           # Number of worker threads
BATCH_SIZE = 100          # Number of events per DB insert
EVENTS_DIR = "calendar_json"  # Folder containing JSON files

# --- DB Connection ---
def connect_db():
    try:
        conn = psycopg2.connect(
            dbname=config.DB_NAME,
            user=config.DB_USER,
            password=config.DB_PASSWORD,
            host=config.DB_HOST,
            port=config.DB_PORT,
            sslmode="require"
        )
        return conn
    except Exception as e:
        print(f"[DB] ❌ Connection error: {e}")
        return None

# --- Insert Batch of Events ---
def batch_insert_events(conn, events):
    inserted_count = 0
    try:
        with conn.cursor() as cursor:
            insert_query = """
            INSERT INTO calendar_events (
                event_id, tenant_id, organizer_name, title, description, location, attendees,
                virtual, start_time, end_time, date_extracted
            ) VALUES %s
            ON CONFLICT (event_id) DO NOTHING
            RETURNING event_id;
            """

            args = [
                (
                    event["Event ID"],
                    event["Tenant ID"],
                    event["Organizer"],
                    event["Title"],
                    event["Description"],
                    event["Location"],
                    json.dumps(event["Attendees"]),
                    event["Virtual"],
                    event["Start"],
                    event["End"],
                    event["Date Extracted"]
                )
                for event in events
            ]

            psycopg2.extras.execute_values(cursor, insert_query, args)
            returned = cursor.fetchall()
            inserted_count = len(returned)
            conn.commit()

            organizer_names = {event["Organizer"] for event in events}
            print(f"[Thread-{threading.current_thread().name}] Processed {len(events)} events. Inserted {inserted_count}. Organizers: {', '.join(organizer_names)}")

    except Exception as e:
        print(f"[Thread-{threading.current_thread().name}] ❌ Error inserting batch: {e}")
    return inserted_count

# --- Worker Thread Function ---
def worker(event_queue):
    conn = connect_db()
    if not conn:
        return

    processed = 0
    inserted = 0
    thread_name = threading.current_thread().name

    while True:
        events = event_queue.get()
        if events is None:
            break
        processed += len(events)
        inserted += batch_insert_events(conn, events)
        event_queue.task_done()

    print(f"[Thread-{thread_name}] ✅ Done. Total Processed: {processed}, Inserted: {inserted}")
    conn.close()

# --- Load JSON Events ---
def load_all_events_from_folder():
    all_events = []
    for file in os.listdir(EVENTS_DIR):
        if file.endswith(".json"):
            path = os.path.join(EVENTS_DIR, file)
            with open(path, "r", encoding="utf-8") as f:
                try:
                    data = json.load(f)
                    if isinstance(data, list):
                        all_events.extend(data)
                    else:
                        print(f"[File] Skipping non-list JSON in: {file}")
                except json.JSONDecodeError as e:
                    print(f"[File] ❌ Failed to parse {file}: {e}")
    return all_events

# --- Main Runner ---
def main():
    all_events = load_all_events_from_folder()
    print(f"[Main] 📦 Total events loaded: {len(all_events)}")

    event_queue = Queue()
    threads = []

    # Spawn worker threads
    for i in range(NUM_THREADS):
        thread = threading.Thread(target=worker, args=(event_queue,), name=f"{i+1}")
        thread.start()
        threads.append(thread)

    # Enqueue batches
    for i in range(0, len(all_events), BATCH_SIZE):
        event_queue.put(all_events[i:i + BATCH_SIZE])

    # Wait and signal termination
    event_queue.join()
    for _ in threads:
        event_queue.put(None)
    for t in threads:
        t.join()

    print("[Main] ✅ All events processed and inserted.")

# --- Entry Point ---
if __name__ == "__main__":
    main()
