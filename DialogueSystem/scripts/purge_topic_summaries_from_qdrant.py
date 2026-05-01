"""One-off script: delete all topic_summary points from the memory Qdrant collection.

Run from project root:
    python -m DialogueSystem.scripts.purge_topic_summaries_from_qdrant
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue, FilterSelector
from project_config import get_qdrant_collection_config, get_project_config


def main():
    config = get_project_config()
    collection_config = get_qdrant_collection_config("memory", config)
    collection_name = collection_config["name"]

    qdrant_setting = config.get("Qdrant_Setting", {})
    host = qdrant_setting.get("host", "localhost")
    port = int(qdrant_setting.get("port", 6333))

    print(f"Connecting to Qdrant at {host}:{port}, collection: {collection_name}")
    client = QdrantClient(host=host, port=port)

    count_filter = Filter(must=[
        FieldCondition(key="memory_kind", match=MatchValue(value="topic_summary"))
    ])

    before_count = client.count(collection_name=collection_name, count_filter=count_filter).count
    print(f"Found {before_count} topic_summary points to delete")

    if before_count == 0:
        print("Nothing to delete.")
        return

    client.delete(
        collection_name=collection_name,
        points_selector=FilterSelector(filter=count_filter),
    )

    after_count = client.count(collection_name=collection_name, count_filter=count_filter).count
    print(f"Deleted. Remaining topic_summary points: {after_count}")
    total = client.count(collection_name=collection_name).count
    print(f"Total points remaining in collection: {total}")


if __name__ == "__main__":
    main()
