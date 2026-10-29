from pathlib import Path
from uuid import uuid4
from app.services.indexing_service import index_document
from uuid import UUID

from app.services.chunking_service import split_text

from app.services.pdf_service import extract_pages

from fastapi import APIRouter, HTTPException, UploadFile

from pydantic import BaseModel, Field, field_validator

from app.services.search_service import search_document

import httpx

from app.services.answer_service import generate_answer

from app.services.document_service import list_documents, save_document_metadata

router = APIRouter(prefix="/documents", tags=["Documents"])

BACKEND_DIR = Path(__file__).resolve().parents[2]
UPLOAD_DIR = BACKEND_DIR / "data" / "uploads"

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB


class SearchRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=3, ge=1, le=10)

    @field_validator("question")
    @classmethod
    def validate_question(cls, value: str) -> str:
        value = value.strip()

        if not value:
            raise ValueError("Question cannot be blank.")

        return value


@router.post("/upload", status_code=201)
async def upload_document(file: UploadFile):
    filename = file.filename or ""

    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed.")

    document_id = uuid4().hex
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    destination = UPLOAD_DIR / f"{document_id}.pdf"
    size = 0
    completed = False

    try:
        # Basic PDF header check
        header = await file.read(5)

        if header != b"%PDF-":
            raise HTTPException(
                status_code=400, detail="File does not have a valid PDF header."
            )

        with destination.open("xb") as output:
            output.write(header)
            size = len(header)

            while chunk := await file.read(1024 * 1024):
                size += len(chunk)

                if size > MAX_FILE_SIZE:
                    raise HTTPException(
                        status_code=413, detail="PDF must be 10 MB or smaller."
                    )

                output.write(chunk)

            metadata = save_document_metadata(
                document_id=document_id, filename=filename, size_bytes=size
            )

        completed = True

    finally:
        await file.close()

        if not completed:
            destination.unlink(missing_ok=True)

    return {
        **metadata,
        "status": "uploaded"
    }
    


@router.get("/{document_id}/pages")
def get_document_pages(document_id: UUID):
    file_path = UPLOAD_DIR / f"{document_id.hex}.pdf"

    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Document not found.")

    try:
        pages = extract_pages(file_path)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    return {
        "document_id": document_id.hex,
        "total_pages": len(pages),
        "has_readable_text": any(page["text"] for page in pages),
        "pages": pages,
    }


@router.get("/{document_id}/chunks")
def get_document_chunks(document_id: UUID):
    file_path = UPLOAD_DIR / f"{document_id.hex}.pdf"

    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Document not found.")

    try:
        pages = extract_pages(file_path)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    records = []

    for page in pages:
        chunks = split_text(page["text"])

        for chunk in chunks:
            records.append(
                {
                    "chunk_id": len(records) + 1,
                    "document_id": document_id.hex,
                    "page": page["page"],
                    "text": chunk,
                    "word_count": len(chunk.split()),
                }
            )

    return {
        "document_id": document_id.hex,
        "total_pages": len(pages),
        "total_chunks": len(records),
        "chunks": records,
    }


@router.post("/{document_id}/index", status_code=201)
def create_document_index(document_id: UUID):
    file_path = UPLOAD_DIR / f"{document_id.hex}.pdf"

    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Document not found.")

    try:
        return index_document(file_path, document_id.hex)

    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/{document_id}/search")
def search_document_endpoint(document_id: UUID, request: SearchRequest):
    try:
        matches = search_document(
            document_id=document_id.hex, question=request.question, top_k=request.top_k
        )

    except FileNotFoundError as error:
        raise HTTPException(
            status_code=404, detail="Index not found. Index the document first."
        ) from error

    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    return {
        "document_id": document_id.hex,
        "question": request.question,
        "total_matches": len(matches),
        "matches": matches,
    }


@router.post("/{document_id}/ask")
def ask_document(document_id: UUID, request: SearchRequest):
    try:
        matches = search_document(
            document_id=document_id.hex, question=request.question, top_k=request.top_k
        )

    except FileNotFoundError as error:
        raise HTTPException(
            status_code=404, detail="Index not found. Index the document first."
        ) from error

    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    try:
        answer = generate_answer(request.question, matches)

    except httpx.TimeoutException as error:
        raise HTTPException(
            status_code=504, detail="Local model timed out. Try again."
        ) from error

    except httpx.RequestError as error:
        raise HTTPException(
            status_code=503, detail="Cannot reach Ollama. Check that it is running."
        ) from error

    except httpx.HTTPStatusError as error:
        raise HTTPException(
            status_code=502, detail="Ollama returned an error. Check its server logs."
        ) from error

    except (ValueError, KeyError, TypeError) as error:
        raise HTTPException(
            status_code=502, detail="The model returned an invalid response."
        ) from error

    return {
        "document_id": document_id.hex,
        "question": request.question,
        "answer": answer,
        "retrieved_sources": [
            {
                "document_id": match["document_id"],
                "chunk_id": match["chunk_id"],
                "page": match["page"],
                "score": match["score"],
                "text": match["text"],
            }
            for match in matches
        ],
    }

@router.get("")
def get_documents():
    documents = list_documents()

    return {
        "total_documents": len(documents),
        "documents": documents
    }