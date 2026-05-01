import json
import os
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct


def load_config():
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def ensure_collection(target_client, collection_name, source_client):
    source_info = source_client.get_collection(collection_name)
    source_vectors = source_info.config.params.vectors
    if target_client.collection_exists(collection_name):
        target_info = target_client.get_collection(collection_name)
        target_vectors = target_info.config.params.vectors
        if extract_vector_size(target_vectors) == extract_vector_size(source_vectors):
            return
        target_client.delete_collection(collection_name)
    target_client.create_collection(
        collection_name=collection_name,
        vectors_config=source_vectors
    )


def extract_vector_size(vectors_config):
    if isinstance(vectors_config, dict):
        if not vectors_config:
            return None
        first_key = next(iter(vectors_config))
        vector_params = vectors_config[first_key]
        return getattr(vector_params, "size", None)
    return getattr(vectors_config, "size", None)


def migrate_collection(source_client, target_client, collection_name):
    ensure_collection(target_client, collection_name, source_client)
    target_info = target_client.get_collection(collection_name)
    expected_size = extract_vector_size(target_info.config.params.vectors)
    offset = None
    total = 0
    skipped = 0
    while True:
        records, offset = source_client.scroll(
            collection_name=collection_name,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=True
        )
        if not records:
            break
        points = []
        for record in records:
            vector = record.vector
            if expected_size is not None and isinstance(vector, list) and len(vector) != expected_size:
                skipped += 1
                continue
            points.append(PointStruct(id=record.id, vector=vector, payload=record.payload or {}))
        if points:
            target_client.upsert(collection_name=collection_name, points=points)
            total += len(points)
        if offset is None:
            break
    return total, skipped


def main():
    config = load_config()
    qdrant_setting = config.get("Qdrant_Setting", {})
    source_path = os.environ.get("QDRANT_SOURCE_PATH") or qdrant_setting.get("local_data_path", "./qdrant_data")
    host = os.environ.get("QDRANT_TARGET_HOST") or qdrant_setting.get("host", "127.0.0.1")
    port = int(os.environ.get("QDRANT_TARGET_PORT") or qdrant_setting.get("port", 6333))
    if not os.path.isabs(source_path):
        source_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), source_path)

    source_client = QdrantClient(path=source_path)
    target_client = QdrantClient(host=host, port=port)
    try:
        collections = source_client.get_collections().collections
        if not collections:
            print("没有可迁移的集合。")
            return
        for collection in collections:
            name = collection.name
            total, skipped = migrate_collection(source_client, target_client, name)
            print(f"{name}: 已迁移 {total} 条数据, 跳过 {skipped} 条")
    finally:
        source_client.close()
        target_client.close()


if __name__ == "__main__":
    main()
