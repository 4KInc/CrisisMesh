#!/usr/bin/env python3
"""Create Pub/Sub topics and subscriptions for CrisisMesh."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv

load_dotenv()

from google.cloud import pubsub_v1
from google.api_core.exceptions import AlreadyExists

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
if not PROJECT:
    print("ERROR: Set GOOGLE_CLOUD_PROJECT"); sys.exit(1)

TOPICS = [
    "crisismesh-incidents",
    "crisismesh-checkins",
    "crisismesh-tasks",
    "crisismesh-events",
]

publisher = pubsub_v1.PublisherClient()
subscriber = pubsub_v1.SubscriberClient()

for topic_id in TOPICS:
    topic_path = publisher.topic_path(PROJECT, topic_id)
    try:
        publisher.create_topic(request={"name": topic_path})
        print(f"  Created topic: {topic_path}")
    except AlreadyExists:
        print(f"  Exists: {topic_path}")

    sub_id = f"{topic_id}-sub"
    sub_path = subscriber.subscription_path(PROJECT, sub_id)
    try:
        subscriber.create_subscription(request={"name": sub_path, "topic": topic_path})
        print(f"  Created subscription: {sub_path}")
    except AlreadyExists:
        print(f"  Exists: {sub_path}")

print("\nPub/Sub setup complete.")
