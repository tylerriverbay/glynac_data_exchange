import os
import json
import psycopg2
from psycopg2.extras import execute_values
from concurrent.futures import ThreadPoolExecutor
from more_itertools import chunked
import threading
import config

# Load DB config
DB_NAME = config.DB_NAME
DB_USER = config.DB_USER
DB_PASSWORD = config.DB_PASSWORD
DB_HOST = config.DB_HOST
DB_PORT = config.DB_PORT

THREAD_COUNT = 4  # Adjust for performance

def connect_db():
    try:
        return psycopg2.connect(
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            host=DB_HOST,
            port=DB_PORT,
            sslmode="require"
        )
    except Exception as e:
        print(f"[ERROR] Database connection error: {e}")
        return None

def create_tables():
    conn = connect_db()
    if conn is None:
        return
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS document_file (
                    item_id TEXT PRIMARY KEY,
                    file_name TEXT,
                    file_type TEXT,
                    is_folder BOOLEAN,
                    file_size_bytes TEXT,
                    last_modified TIMESTAMP,
                    last_modified_by TEXT,
                    user_upn TEXT,
                    extracted_at TIMESTAMP
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS document_activity (
                    activity_id TEXT PRIMARY KEY,
                    item_id TEXT REFERENCES document_file(item_id) ON DELETE CASCADE,
                    action_type TEXT,
                    performed_by TEXT,
                    user_upn TEXT,
                    email TEXT,
                    timestamp TIMESTAMP
                );
            """)
        conn.commit()
        print("✅ Tables created successfully.")
    except Exception as e:
        conn.rollback()
        print(f"❌ Error creating tables: {e}")
    finally:
        conn.close()

def insert_batch(file_batch, activity_batch):
    conn = connect_db()
    if conn is None:
        return
    thread_id = threading.current_thread().name

    try:
        with conn.cursor() as cursor:
            if file_batch:
                execute_values(cursor, """
                    INSERT INTO document_file (
                        item_id, file_name, file_type, is_folder, file_size_bytes,
                        last_modified, last_modified_by, user_upn, extracted_at
                    ) VALUES %s
                    ON CONFLICT (item_id) DO UPDATE SET
                        file_name = EXCLUDED.file_name,
                        file_type = EXCLUDED.file_type,
                        is_folder = EXCLUDED.is_folder,
                        file_size_bytes = EXCLUDED.file_size_bytes,
                        last_modified = EXCLUDED.last_modified,
                        last_modified_by = EXCLUDED.last_modified_by,
                        user_upn = EXCLUDED.user_upn,
                        extracted_at = EXCLUDED.extracted_at;
                """, file_batch)
                print(f"[{thread_id}] ✅ Inserted {len(file_batch)} files")

            if activity_batch:
                execute_values(cursor, """
                    INSERT INTO document_activity (
                        activity_id, item_id, action_type, performed_by,
                        user_upn, email, timestamp
                    ) VALUES %s
                    ON CONFLICT (activity_id) DO NOTHING;
                """, activity_batch)
                print(f"[{thread_id}] ✅ Inserted {len(activity_batch)} activities")

        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"[{thread_id}] ❌ Error in batch insert: {e}")
    finally:
        conn.close()

def load_jsons_from_directory(directory):
    all_files_data = []
    all_activities_data = []
    seen_item_ids = set()
    unique_files = {}

    total_file_count = 0      # Includes duplicates
    total_folder_count = 0    # Includes duplicates
    total_activity_count = 0

    for filename in os.listdir(directory):
        if filename.endswith(".json") and not filename.endswith("_error.json"):
            path = os.path.join(directory, filename)
            with open(path, "r", encoding="utf-8") as f:
                try:
                    file_docs = json.load(f)
                except Exception as e:
                    print(f"❌ Failed to parse {filename}: {e}")
                    continue

            file_count = 0
            folder_count = 0
            activity_count = 0

            for file in file_docs:
                item_id = file.get("itemId")
                is_folder = file.get("isFolder", False)

                # Count every file/folder, regardless of duplicates
                if is_folder:
                    folder_count += 1
                    total_folder_count += 1
                else:
                    file_count += 1
                    total_file_count += 1

                if item_id and item_id not in seen_item_ids:
                    seen_item_ids.add(item_id)
                    unique_files[item_id] = is_folder

                    all_files_data.append((
                        item_id,
                        file.get("fileName"),
                        file.get("fileType"),
                        is_folder,
                        file.get("fileSizeBytes"),
                        file.get("lastModified"),
                        file.get("lastModifiedBy"),
                        file.get("userPrincipalName"),
                        file.get("extractedAt"),
                    ))

                for activity in file.get("activities", []):
                    activity_count += 1
                    total_activity_count += 1
                    all_activities_data.append((
                        activity.get("activityId"),
                        item_id,
                        activity.get("actionType"),
                        activity.get("performedBy"),
                        activity.get("userPrincipalName"),
                        activity.get("email"),
                        activity.get("timestamp")
                    ))

            print(f"📄 {filename} ➜ {file_count} files, {folder_count} folders, {activity_count} activities")

    # Unique counts
    unique_folder_count = sum(1 for v in unique_files.values() if v)
    unique_file_count = sum(1 for v in unique_files.values() if not v)

    print("\n📊 Total Summary:")
    print(f"   🗃️ Total Files (raw count): {total_file_count}")
    print(f"   📁 Total Folders (raw count): {total_folder_count}")
    print(f"   📝 Total Activities: {total_activity_count}")

    print("\n📊 Unique Summary:")
    print(f"   🗃️ Unique Files: {unique_file_count}")
    print(f"   📁 Unique Folders: {unique_folder_count}")
    print(f"   📦 Unique Total Items: {unique_file_count + unique_folder_count}")

    return all_files_data, all_activities_data

def insert_files_in_threads(all_files_data):
    insert_threads = []
    with ThreadPoolExecutor(max_workers=THREAD_COUNT) as executor:
        for file_chunk in chunked(all_files_data, 100):
            future = executor.submit(insert_batch, file_chunk, [])
            insert_threads.append(future)
    for t in insert_threads:
        t.result()

def insert_activities_in_threads(all_activities_data):
    insert_threads = []
    with ThreadPoolExecutor(max_workers=THREAD_COUNT) as executor:
        for activity_chunk in chunked(all_activities_data, 100):
            future = executor.submit(insert_batch, [], activity_chunk)
            insert_threads.append(future)
    for t in insert_threads:
        t.result()

if __name__ == "__main__":
    create_tables()
    all_files_data, all_activities_data = load_jsons_from_directory("document_json")

    print("\n🚀 Starting file inserts...")
    insert_files_in_threads(all_files_data)
    print("✅ All file inserts done.")

    print("\n🚀 Starting activity inserts...")
    insert_activities_in_threads(all_activities_data)
    print("✅ All activity inserts done.")
