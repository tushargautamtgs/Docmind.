import json
import logging
import re
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from anyio import CancelScope
from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool

from app.services.answer_service import generate_answer
from app.services.chunking_service import split_text
from app.services.document_service import (
    list_documents,
    save_document_metadata,
)
from app.services.indexing_service import index_document
from app.services.pdf_service import extract_pages
from app.services.search_service import search_document
from app.services.streaming_service import stream_answer


router = APIRouter(
    prefix="/documents",
    tags=["Documents"],
)

BACKEND_DIR = Path(__file__).resolve().parents[2]
UPLOAD_DIR = BACKEND_DIR / "data" / "uploads"

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB


def get_short_reply(question: str) -> str | None:
    # Match the whole message so greetings do not hide a PDF question.
    text = re.sub(r"[^\w\s]", " ", question.casefold())
    text = re.sub(
        r"\b(?:bro|bhai|docmind|dockmind)\b",
        " ",
        text,
    )
    text = " ".join(text.split())

    if text in {"hello", "hi", "hey"}:
        return "Hello!"

    if text in {
        "thanks",
        "thank you",
        "thankyou",
        "thank u",
        "thanks a lot",
        "thank you so much",
        "thanks so much",
    }:
        return "You're welcome! Thanks for using DocMind."

    if text in {
        "ok",
        "okay",
        "ok done",
        "okay done",
        "done",
        "ok thanks",
        "okay thanks",
        "done thanks",
        "ok thank you",
        "okay thank you",
        "done thank you",
        "got it",
        "understood",
        "alright",
        "all right",
        "cool",
        "great",
        "nice",
        "perfect",
        "theek hai",
        "thik hai",
        "ho gaya",
        "hogaya",
    }:
        return "Thanks for using DocMind!"

    return None


async def short_reply_events(answer: str):
    for event in (
        {
            "type": "sources",
            "retrieved_sources": [],
        },
        {
            "type": "delta",
            "text": answer,
        },
        {
            "type": "done",
        },
    ):
        yield json.dumps(event, ensure_ascii=False) + "\n"


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
        raise HTTPException(
            status_code=400,
            detail="Only PDF files are allowed.",
        )

    document_id = uuid4().hex
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    destination = UPLOAD_DIR / f"{document_id}.pdf"
    size = 0
    completed = False

    try:
        header = await file.read(5)

        if header != b"%PDF-":
            raise HTTPException(
                status_code=400,
                detail="File does not have a valid PDF header.",
            )

        with destination.open("xb") as output:
            output.write(header)
            size = len(header)

            while chunk := await file.read(1024 * 1024):
                size += len(chunk)

                if size > MAX_FILE_SIZE:
                    raise HTTPException(
                        status_code=413,
                        detail="PDF must be 10 MB or smaller.",
                    )

                output.write(chunk)

            metadata = save_document_metadata(
                document_id=document_id,
                filename=filename,
                size_bytes=size,
            )

        completed = True

    finally:
        await file.close()

        if not completed:
            destination.unlink(missing_ok=True)

    return {
        **metadata,
        "status": "uploaded",
    }


@router.get("/{document_id}/pages")
def get_document_pages(document_id: UUID):
    file_path = UPLOAD_DIR / f"{document_id.hex}.pdf"

    if not file_path.is_file():
        raise HTTPException(
            status_code=404,
            detail="Document not found.",
        )

    try:
        pages = extract_pages(file_path)

    except ValueError as error:
        raise HTTPException(
            status_code=422,
            detail=str(error),
        ) from error

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
        raise HTTPException(
            status_code=404,
            detail="Document not found.",
        )

    try:
        pages = extract_pages(file_path)

    except ValueError as error:
        raise HTTPException(
            status_code=422,
            detail=str(error),
        ) from error

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
        raise HTTPException(
            status_code=404,
            detail="Document not found.",
        )

    try:
        return index_document(file_path, document_id.hex)

    except ValueError as error:
        raise HTTPException(
            status_code=422,
            detail=str(error),
        ) from error


@router.post("/{document_id}/search")
def search_document_endpoint(
    document_id: UUID,
    request: SearchRequest,
):
    try:
        matches = search_document(
            document_id=document_id.hex,
            question=request.question,
            top_k=request.top_k,
        )

    except FileNotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail="Index not found. Index the document first.",
        ) from error

    except ValueError as error:
        raise HTTPException(
            status_code=422,
            detail=str(error),
        ) from error

    return {
        "document_id": document_id.hex,
        "question": request.question,
        "total_matches": len(matches),
        "matches": matches,
    }


@router.post("/{document_id}/ask")
def ask_document(
    document_id: UUID,
    request: SearchRequest,
):
    short_reply = get_short_reply(request.question)

    if short_reply is not None:
        return {
            "document_id": document_id.hex,
            "question": request.question,
            "answer": short_reply,
            "retrieved_sources": [],
        }

    try:
        matches = search_document(
            document_id=document_id.hex,
            question=request.question,
            top_k=request.top_k,
        )

    except FileNotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail="Index not found. Index the document first.",
        ) from error

    except ValueError as error:
        raise HTTPException(
            status_code=422,
            detail=(
                "Search could not be completed. "
                "Check the local server logs."
            ),
        ) from error

    try:
        answer = generate_answer(request.question, matches)

    except httpx.TimeoutException as error:
        raise HTTPException(
            status_code=504,
            detail="Local model timed out. Try again.",
        ) from error

    except httpx.RequestError as error:
        raise HTTPException(
            status_code=503,
            detail="Cannot reach Ollama. Check that it is running.",
        ) from error

    except httpx.HTTPStatusError as error:
        raise HTTPException(
            status_code=502,
            detail="Ollama returned an error. Check its server logs.",
        ) from error

    except (ValueError, KeyError, TypeError) as error:
        raise HTTPException(
            status_code=502,
            detail="The model returned an invalid response.",
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
        "documents": documents,
    }


class AnswerStreamingResponse(StreamingResponse):
    async def stream_response(self, send):
        try:
            await super().stream_response(send)

        finally:
            with CancelScope(shield=True):
                await self.body_iterator.aclose()


@router.post("/{document_id}/ask/stream")
async def ask_document_stream(
    document_id: UUID,
    request: SearchRequest,
):
    short_reply = get_short_reply(request.question)

    if short_reply is not None:
        return AnswerStreamingResponse(
            short_reply_events(short_reply),
            media_type="application/x-ndjson",
            headers={
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",
            },
        )

    try:
        matches = await run_in_threadpool(
            search_document,
            document_id=document_id.hex,
            question=request.question,
            top_k=request.top_k,
        )

    except FileNotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail="Index not found. Index the document first.",
        ) from error

    except ValueError as error:
        raise HTTPException(
            status_code=422,
            detail="Search could not be completed.",
        ) from error

    sources = [
        {
            "document_id": match["document_id"],
            "chunk_id": match["chunk_id"],
            "page": match["page"],
            "score": float(match["score"]),
            "text": match["text"],
        }
        for match in matches
    ]

    def encode(event):
        return (
            json.dumps(
                event,
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n"
        )

    async def events():
        answer_stream = stream_answer(request.question, matches)

        try:
            yield encode(
                {
                    "type": "sources",
                    "retrieved_sources": sources,
                }
            )

            async for text in answer_stream:
                yield encode(
                    {
                        "type": "delta",
                        "text": text,
                    }
                )

            yield encode({"type": "done"})

        except Exception as error:
            logging.getLogger(__name__).exception(
                "DocMind answer stream failed"
            )

            if isinstance(error, httpx.TimeoutException):
                message = "Local model timed out. Try again."

            elif isinstance(error, httpx.RequestError):
                message = (
                    "Cannot reach Ollama. Check that it is running."
                )

            elif isinstance(error, httpx.HTTPStatusError):
                message = (
                    "Ollama returned an error. Check its server logs."
                )

            else:
                message = (
                    "Answer stream failed or ended early. "
                    "The answer may be incomplete."
                )

            yield encode(
                {
                    "type": "error",
                    "message": message,
                }
            )

        finally:
            with CancelScope(shield=True):
                await answer_stream.aclose()

    return AnswerStreamingResponse(
        events(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )