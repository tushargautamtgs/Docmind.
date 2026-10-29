import json
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

import numpy as np
from sentence_transformers import SentenceTransformer

from app.services.chunking_service import split_text
from app.services.pdf_service import extract_pages


MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

BACKEND_DIR = Path(__file__).resolve().parents[2]
INDEX_DIR = BACKEND_DIR / "data" / "indexes"


@lru_cache(maxsize=1)
def get_embedding_model():
    return SentenceTransformer(MODEL_NAME)


def index_document(file_path: Path, document_id: str) -> dict:
    pages = extract_pages(file_path)
    records = []

    for page in pages:
        chunks = split_text(
            page["text"],
            chunk_size=100,
            overlap=20
        )

        for chunk in chunks:
            records.append({
                "chunk_id": len(records) + 1,
                "document_id": document_id,
                "page": page["page"],
                "text": chunk
            })

    if not records:
        raise ValueError("PDF has no readable text to index.")

    model = get_embedding_model()
    texts = [record["text"] for record in records]

    embeddings = model.encode(
        texts,
        normalize_embeddings=True,
        convert_to_numpy=True
    )

    metadata = {
        "document_id": document_id,
        "model": MODEL_NAME,
        "chunk_size": 100,
        "overlap": 20,
        "total_pages": len(pages),
        "records": records
    }

    INDEX_DIR.mkdir(parents=True, exist_ok=True)

    destination = INDEX_DIR / f"{document_id}.npz"
    temporary = INDEX_DIR / f"{document_id}.{uuid4().hex}.tmp"

    try:
        with temporary.open("xb") as file:
            np.savez_compressed(
                file,
                embeddings=embeddings,
                metadata=np.array(json.dumps(metadata))
            )

        temporary.replace(destination)

    finally:
        temporary.unlink(missing_ok=True)

    return {
        "document_id": document_id,
        "status": "indexed",
        "total_pages": len(pages),
        "total_chunks": len(records),
        "embedding_dimensions": int(embeddings.shape[1])
    }