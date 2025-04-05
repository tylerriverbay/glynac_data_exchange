import requests
from auth_token import get_access_token
from config import GRAPH_API_ENDPOINT
from datetime import datetime
import re
from bs4 import BeautifulSoup
import concurrent.futures
import logging
import os
from dotenv import load_dotenv

load_dotenv()
MAX_GRAPH_WORKERS = int(os.getenv("MAX_GRAPH_WORKERS", 16))  # Default to 5 workers

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def fetch_paginated_results(url, headers):
    all_results = []
    while url:
        try:
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()  # Raise an exception for bad status codes
            data = response.json()
            all_results.extend(data.get("value", []))
            url = data.get("@odata.nextLink")
        except requests.exceptions.RequestException as e:
            logging.error(f"Error fetching URL {url}: {e}")
            return []
    return all_results

def html_to_text(html_content):
    """Convert HTML content to plain text."""
    if not html_content:
        return ""

    soup = BeautifulSoup(html_content, "html.parser")

    for tag in soup(["script", "style"]):
        tag.decompose()

    # <br> and <p> tags
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for p in soup.find_all("p"):
        p.insert_before("\n")

    # List items
    for li in soup.find_all("li"):
        li.insert_before("\n- ")
        li.insert_after("\n")  # Adds new line

    # Convert blockquote messages (e.g., replies in Teams)
    for blockquote in soup.find_all("blockquote"):
        blockquote.insert_before("\n> ")  # Adds quote formatting
        blockquote.insert_after("\n")

    # Handle links: Keep text + URL in brackets
    for a in soup.find_all("a", href=True):
        a.replace_with(f"{a.get_text()} ({a['href']})")

    # Handle code snippets
    for code in soup.find_all("pre"):
        code.insert_before("\n```\n")  # Markdown-style block
        code.insert_after("\n```\n")

    text = soup.get_text(separator=" ")

    # Clean up excessive newlines and whitespace
    text = re.sub(r'\s+', ' ', text).strip()

    return text

def extract_teams(user_upn):
    """Extract all teams the user is part of."""
    access_token = get_access_token()
    url = f"{GRAPH_API_ENDPOINT}/users/{user_upn}/joinedTeams"
    headers = {"Authorization": f"Bearer {access_token}"}
    return fetch_paginated_results(url, headers)

def extract_channels(team_id):
    """Extract channels in a specific team."""
    access_token = get_access_token()
    url = f"{GRAPH_API_ENDPOINT}/teams/{team_id}/channels"
    headers = {"Authorization": f"Bearer {access_token}"}
    return fetch_paginated_results(url, headers)

def _extract_channel_messages(team_id, channel_id, channel_name):
    """Internal function to extract messages from a specific channel."""
    access_token = get_access_token()
    url = f"{GRAPH_API_ENDPOINT}/teams/{team_id}/channels/{channel_id}/messages"  # ?$top=100"
    headers = {"Authorization": f"Bearer {access_token}"}
    messages = fetch_paginated_results(url, headers)
    logging.info(f"Fetched {len(messages)} messages for channel: {channel_name}")
    filtered_messages = []
    for message in messages:
        if message.get("messageType") in ["systemEventMessage", "unknownFutureValue"]:
            continue
        sender = message.get("from") or {}
        if "user" not in sender:
            continue
        mentions = message.get("mentions", [])
        mentioned_users = [m.get("user", {}).get("displayName") for m in mentions if "user" in m]
        body = html_to_text(message.get("body", {}).get("content", "No Content"))
        filtered_messages.append({
            "platform": "Teams",
            "chat_id": message.get("id"),
            "chat_from": sender.get("user", {}).get("displayName", "Unknown"),
            "channel": channel_name,
            "message": body,
            "thread_id": message.get("replyToId"),
            "timestamp": message.get("createdDateTime"),
            "mentioned_users": ", ".join(mentioned_users) if mentioned_users else None,
            "date_extracted": datetime.utcnow().isoformat()
        })
    return filtered_messages

def extract_messages_for_team(user_upn, team):
    """Extract messages for all channels in a given team."""
    team_id = team.get("id")
    team_name = team.get("displayName", "Unnamed Team")
    channels = extract_channels(team_id)
    all_channel_messages = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_GRAPH_WORKERS) as executor:
        futures = {executor.submit(_extract_channel_messages, team_id, channel.get("id"), f"{team_name} / {channel.get('displayName', 'Unnamed Channel')}"): channel for channel in channels}
        for future in concurrent.futures.as_completed(futures):
            channel = futures[future]
            try:
                messages = future.result()
                all_channel_messages.extend(messages)
            except Exception as e:
                logging.error(f"Error extracting messages for channel {channel.get('displayName', 'Unnamed Channel')} in team {team_name}: {e}", exc_info=True)
    return all_channel_messages

def extract_messages(user_upn):
    """Extract all Teams messages for a user, leveraging multithreading for teams."""
    final_messages = []
    teams = extract_teams(user_upn)
    if not teams:
        logging.info(f"No Teams found for {user_upn}")
        return []

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_GRAPH_WORKERS) as executor:
        futures = {executor.submit(extract_messages_for_team, user_upn, team): team for team in teams}
        for future in concurrent.futures.as_completed(futures):
            team = futures[future]
            try:
                messages = future.result()
                final_messages.extend(messages)
            except Exception as e:
                logging.error(f"Error extracting messages for team {team.get('displayName', 'Unnamed Team')} for user {user_upn}: {e}", exc_info=True)

    return final_messages