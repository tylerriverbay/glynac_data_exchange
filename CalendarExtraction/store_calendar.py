import psycopg2
import config
import json
import os
import threading
from queue import Queue

# --- Configuration ---
NUM_THREADS = 16          # Control how many threads to use
BATCH_SIZE = 100          # Control how many events per DB insert
EVENTS_DIR = "calendar_json"

# --- DB Setup ---
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
        print(f"Database connection error: {e}")
        return None

def create_calendar_events_table(conn):
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS calendar_events (
                event_id TEXT PRIMARY KEY,
                tenant_id UUID,
                organizer_name TEXT,
                title TEXT,
                description TEXT,
                location TEXT,
                attendees JSONB,
                virtual BOOLEAN,
                start_time TIMESTAMP,
                end_time TIMESTAMP,
                date_extracted TIMESTAMP
            );
            """)
            conn.commit()
            print("✅ Table 'calendar_events' ensured.")
    except Exception as e:
        print(f"❌ Error creating table: {e}")

def batch_insert_events(conn, events):
    try:
        with conn.cursor() as cursor:
            insert_query = """
            INSERT INTO calendar_events (
                event_id, tenant_id, organizer_name, title, description, location, attendees,
                virtual, start_time, end_time, date_extracted
            ) VALUES %s
            ON CONFLICT (event_id) DO NOTHING;
            """
            args_str = ','.join(cursor.mogrify("(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", (
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
            )).decode("utf-8") for event in events)

            cursor.execute(insert_query % args_str)
            conn.commit()
            print(f"[Thread-{threading.current_thread().name}] Inserted batch of {len(events)} events.")
    except Exception as e:
        print(f"[Thread-{threading.current_thread().name}] Error inserting batch: {e}")

# --- Worker Thread ---
def worker(event_queue):
    conn = connect_db()
    if not conn:
        return

    while True:
        events = event_queue.get()
        if events is None:
            break
        batch_insert_events(conn, events)
        event_queue.task_done()

    conn.close()

# --- Load Events from Files ---
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
                        print(f"Skipping non-list JSON in file: {file}")
                except json.JSONDecodeError as e:
                    print(f"Failed to parse {file}: {e}")
    return all_events

# --- Main Execution ---
def main():
    conn = connect_db()
    if not conn:
        print("❌ Cannot proceed without DB connection.")
        return

    create_calendar_events_table(conn)
    conn.close()

    all_events = load_all_events_from_folder()
    print(f"Total events loaded: {len(all_events)}")

    event_queue = Queue()
    threads = []

    # Start worker threads
    for i in range(NUM_THREADS):
        thread = threading.Thread(target=worker, args=(event_queue,), name=f"{i+1}")
        thread.start()
        threads.append(thread)

    # Divide into batches
    for i in range(0, len(all_events), BATCH_SIZE):
        event_queue.put(all_events[i:i+BATCH_SIZE])

    # Stop workers
    event_queue.join()
    for _ in threads:
        event_queue.put(None)
    for t in threads:
        t.join()

    print("✅ All events inserted successfully.")

if __name__ == "__main__":
    main()
