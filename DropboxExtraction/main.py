import json
import logging
from extract_dropbox import get_activity_log
from store_dropbox import insert_team_events

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

def main():
    logging.info("Starting Dropbox activity log extraction...")
    events = get_activity_log()
    
if __name__ == "__main__":
    main()
