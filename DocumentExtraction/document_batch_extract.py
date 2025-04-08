import os
import sys
import json
import signal
import concurrent.futures
from datetime import datetime
import requests


sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from NewEmailExtraction.fetch_users import get_analyzable_users
from auth_token import get_access_token
from config import GRAPH_API_ENDPOINT


def signal_handler(sig, frame):
    print("\nInterrupted. Exiting...")
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)


os.makedirs("document_json", exist_ok=True)


def fetch_paginated_results(url, headers):
    all_results = []
    while url:
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            return []
        data = response.json()
        all_results.extend(data.get("value", []))
        url = data.get("@odata.nextLink")
    return all_results


def get_user_documents(user_upn):
    access_token = get_access_token()
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"{GRAPH_API_ENDPOINT}/users/{user_upn}/drive/root/children"
    return fetch_paginated_results(url, headers)


def save_document_json(user):
    try:
        if isinstance(user, str):
            user_upn = user
        else:
            user_upn = user.get("userPrincipalName")
            if user.get("status") != "active":
                print(f" {user_upn} is inactive.")
                return

        print(f" Extracting documents for {user_upn}")
        files = get_user_documents(user_upn)

        extracted_data = [
            {
                "Activity ID": file.get("id", "Unknown"),
                "File Name": file.get("name", "Unknown"),
                "File Type": file.get("file", {}).get("mimeType", "Unknown"),
                "User": file.get("lastModifiedBy", {}).get("user", {}).get("displayName", "Unknown"),
                "Timestamp": file.get("lastModifiedDateTime", "Unknown"),
                "Date Extracted": datetime.utcnow().isoformat()
            }
            for file in files
        ]

        filename = os.path.join("document_json", f"document_{user_upn.replace('@', '_at_')}.json")
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(extracted_data, f, indent=2)

        

    except Exception as e:
      
        error_path = os.path.join("document_json", f"document_{user_upn.replace('@', '_at_')}_error.json")
        with open(error_path, "w") as f:
            json.dump({"error": str(e)}, f, indent=2)


def main():
    users = get_analyzable_users()
    print(f"Total users fetched: {len(users)}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        executor.map(save_document_json, users)

if __name__ == "__main__":
    main()
