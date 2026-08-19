"""Load seed CSV data into Firestore for demo purposes."""

from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.services.csv_ingest import ingest_csv
from src.services.firestore_state import FirestoreState

load_dotenv()

SEED_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "seed")

SEED_FILES = [
    ("facilities", "facilities.csv"),
    ("rooms", "rooms.csv"),
    ("people", "people.csv"),
    ("resources", "resources.csv"),
    ("routes", "routes.csv"),
]

FACILITY_ID = "facility-001"


async def main() -> None:
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        print("ERROR: Set GOOGLE_CLOUD_PROJECT environment variable")
        sys.exit(1)

    state = FirestoreState(project=project)

    for csv_type, filename in SEED_FILES:
        filepath = os.path.join(SEED_DIR, filename)
        if not os.path.exists(filepath):
            print(f"SKIP: {filename} not found")
            continue

        with open(filepath) as f:
            content = f.read()

        result = await ingest_csv(state, csv_type, content, facility_id=FACILITY_ID)
        print(f"OK: {csv_type} -> {result}")

    print("\nSeed data loaded successfully.")


if __name__ == "__main__":
    asyncio.run(main())
