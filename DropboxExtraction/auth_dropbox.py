import requests
from base64 import b64encode
import config

def get_access_token():
    """Always fetch a fresh Dropbox access token using the refresh token."""
    auth_string = f"{config.DROPBOX_APP_KEY}:{config.DROPBOX_APP_SECRET}"
    headers = {
        "Authorization": "Basic " + b64encode(auth_string.encode()).decode(),
        "Content-Type": "application/x-www-form-urlencoded"
    }

    data = {
        "grant_type": "refresh_token",
        "refresh_token": config.DROPBOX_REFRESH_TOKEN
    }

    response = requests.post("https://api.dropbox.com/oauth2/token", headers=headers, data=data)
    response.raise_for_status()
    return response.json()["access_token"]

if __name__ == "__main__":
    try:
        token = get_access_token()
        print(f"Access token: {token[:10]}...")
    except Exception as e:
        print(f"Error: {e}")
