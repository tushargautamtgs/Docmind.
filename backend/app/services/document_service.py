import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4


BACKEND_DIR = Path(__file__).resolve().parents[2]
UPLOAD_DIR = BACKEND_DIR / "data" / "uploads"
INDEX_DIR = BACKEND_DIR / "data" / "indexes"
METADATA_DIR = BACKEND_DIR / "data" / "metadata"


def save_document_metadata(
    document_id: str,
    filename: str,
    size_bytes: int
) -> dict:
    identifier = UUID(document_id)

    metadata = {
        "document_id": str(identifier),
        "filename": filename,
        "size_bytes": size_bytes,
        "uploaded_at": datetime.now(timezone.utc).isoformat()
    }

    METADATA_DIR.mkdir(parents=True, exist_ok=True)

    destination = METADATA_DIR / f"{identifier.hex}.json"
    temporary = METADATA_DIR / f"{uuid4().hex}.tmp"

    try:
        temporary.write_text(
            json.dumps(metadata, indent=4, ensure_ascii=False),
            encoding="utf-8"
        )
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)

    return metadata


def list_documents() -> list[dict]:
    documents = []

    for file_path in sorted(UPLOAD_DIR.glob("*.pdf")):
        try:
            identifier = UUID(file_path.stem)
        except ValueError:
            continue

        metadata_path = METADATA_DIR / f"{identifier.hex}.json"

        if metadata_path.is_file():
            metadata = json.loads(
                metadata_path.read_text(encoding="utf-8")
            )
        else:
            # Older uploads did not save original filenames.
            metadata = {
                "document_id": str(identifier),
                "filename": None,
                "size_bytes": file_path.stat().st_size,
                "uploaded_at": None
            }

        metadata["stored_filename"] = file_path.name
        metadata["status"] = (
            "indexed"
            if (INDEX_DIR / f"{identifier.hex}.npz").is_file()
            else "uploaded"
        )

        documents.append(metadata)

    return documents