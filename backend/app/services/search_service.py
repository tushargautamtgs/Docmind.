import json
from pathlib import Path

import numpy as np

from app.services.indexing_service import (
    INDEX_DIR,
    MODEL_NAME,
    get_embedding_model
)


def search_document(
    document_id: str,
    question: str,
    top_k: int
) -> list[dict]:
    index_path = INDEX_DIR / f"{document_id}.npz"

    with np.load(index_path, allow_pickle=False) as data:
        embeddings = data["embeddings"]
        metadata = json.loads(data["metadata"].item())

    if metadata["model"] != MODEL_NAME:
        raise ValueError("Embedding model changed. Re-index the document.")

    records = metadata["records"]

    if len(records) != len(embeddings) or not records:
        raise ValueError("Invalid index. Re-index the document.")

    model = get_embedding_model()

    question_embedding = model.encode(
        question,
        normalize_embeddings=True,
        convert_to_numpy=True
    )

    scores = embeddings @ question_embedding
    top_indices = scores.argsort()[::-1][:top_k]

    matches = []

    for index in top_indices:
        record = records[int(index)]

        matches.append({
            "chunk_id": record["chunk_id"],
            "document_id": record["document_id"],
            "page": record["page"],
            "text": record["text"],
            "score": float(scores[index])
        })

    return matches