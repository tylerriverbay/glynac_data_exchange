import requests
from auth_token import get_access_token
import config
from datetime import datetime
from bs4 import BeautifulSoup
import re
import logging
import traceback
import os

# --- Setup Logging ---
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] [%(threadName)s] %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "calendar_extraction.log"), mode='a', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# --- Helpers ---

def fetch_paginated_results(url, headers):
    """Fetch all paginated results from a Microsoft Graph API endpoint."""
    all_results = []

    while url:
        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code != 200:
                logger.error(f"Error fetching data ({response.status_code}): {response.text}")
                return []
            data = response.json()
            all_results.extend(data.get("value", []))
            url = data.get("@odata.nextLink")  # Next page
        except Exception as e:
            logger.exception(f"Exception during paginated fetch: {e}")
            return []

    return all_results

def html_to_text(html_content):
    """Convert HTML to clean plain text."""
    soup = BeautifulSoup(html_content, "html.parser")
    text = soup.get_text(separator="\n")

    text = re.sub(r"[_]{5,}", "", text)
    text = re.sub(r"\n\s*\n", "\n", text)
    text = re.sub(r"\s{2,}", " ", text.strip())

    return text.strip()

# --- Core Functions ---

def check_calendars(user_upn):
    """Returns all calendars for a user, excluding system-generated ones."""
    try:
        logger.info(f"Checking calendars for user: {user_upn}")
        access_token = get_access_token()
        url = f"{config.GRAPH_API_ENDPOINT}/users/{user_upn}/calendars"
        headers = {"Authorization": f"Bearer {access_token}"}

        calendars = fetch_paginated_results(url, headers)
        excluded = {"United States holidays", "Birthdays", "Holidays"}

        filtered = [calendar["name"] for calendar in calendars if calendar["name"] not in excluded]
        logger.info(f"Found {len(filtered)} user calendars for {user_upn}")
        return filtered

    except Exception as e:
        logger.exception(f"Error checking calendars for {user_upn}: {e}")
        return []

def get_calendar_events(user_upn, calendar_name="Calendar"):
    """Extracts structured calendar event data from a user's calendar."""
    try:
        logger.info(f"Fetching events for user: {user_upn}, calendar: {calendar_name}")
        access_token = get_access_token()
        url = f"{config.GRAPH_API_ENDPOINT}/users/{user_upn}/calendars/{calendar_name}/events"
        headers = {"Authorization": f"Bearer {access_token}"}

        events = fetch_paginated_results(url, headers)
        logger.info(f"Fetched {len(events)} raw events for {user_upn}")

        extracted_events = []
        for event in events:
            try:
                location = event.get("location", {}).get("displayName", "").strip()
                description = html_to_text(event.get("bodyPreview", "No description"))
                virtual = event.get("isOnlineMeeting", False)

                # Detect virtual meetings
                meeting_links = [
                    "zoom.us", "google.com", "meet.google", "teams.microsoft",
                    "interview-links.indeed", "employers.indeed.com", "virtual-interviews/upcoming"
                ]
                if any(link in location.lower() for link in meeting_links) or any(link in description.lower() for link in meeting_links):
                    virtual = True

                if not location or location.lower() == "virtual meeting (link in description)":
                    location = "Virtual Meeting (link in Description)"

                attendees = {
                    attendee["emailAddress"]["name"]: attendee["type"]
                    for attendee in event.get("attendees", [])
                }
                if not attendees:
                    attendees = {event["organizer"]["emailAddress"]["name"]: "Organizer"}

                extracted_events.append({
                    "Event ID": event["id"],
                    "Tenant ID": config.TENANT_ID,
                    "Organizer": event["organizer"]["emailAddress"]["name"],
                    "Title": event.get("subject") or "No title",
                    "Description": description,
                    "Location": location,
                    "Attendees": attendees,
                    "Virtual": virtual,
                    "Start": event["start"]["dateTime"],
                    "End": event["end"]["dateTime"],
                    "Date Extracted": datetime.utcnow().isoformat()
                })

            except Exception as parse_err:
                logger.warning(f"Failed to parse one event for {user_upn}: {parse_err}")

        logger.info(f"Extracted {len(extracted_events)} valid events for {user_upn}")
        return extracted_events

    except Exception as e:
        logger.exception(f"Error getting calendar events for {user_upn}: {e}")
        return []

