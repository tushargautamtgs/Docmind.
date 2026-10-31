"""Public upload demo with anonymous browser workspaces."""

import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from app.api.documents import upload_document as local_upload
from app.main import app as local_app, safe_request_validation
from app.services.document_service import list_documents


class WorkspaceStore:
    def __init__(self, data_dir):
        self.directory = Path(data_dir) / "public_workspaces"
        self.directory.mkdir(parents=True, exist_ok=True)

        self.uploads = Path(data_dir) / "uploads"
        self.file = self.directory / "owners.json"
        self.lock = threading.RLock()
        self.pending = set()

        secret_file = self.directory / "secret.bin"

        try:
            fd = os.open(
                secret_file,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError:
            self.secret = secret_file.read_bytes()
        else:
            self.secret = secrets.token_bytes(32)

            with os.fdopen(fd, "wb") as output:
                output.write(self.secret)

        if len(self.secret) != 32:
            raise RuntimeError("Public workspace key is invalid.")

    def issue(self):
        nonce = secrets.token_urlsafe(24)
        signature = hmac.new(
            self.secret,
            nonce.encode(),
            hashlib.sha256,
        ).hexdigest()

        return f"{nonce}.{signature}"

    def authenticate(self, authorization):
        scheme, _, token = authorization.partition(" ")

        if scheme.lower() != "bearer":
            return None

        if not re.fullmatch(
            r"[A-Za-z0-9_-]{32}\.[a-f0-9]{64}",
            token,
        ):
            return None

        nonce, signature = token.split(".")

        expected = hmac.new(
            self.secret,
            nonce.encode(),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(signature, expected):
            return None

        return hashlib.sha256(nonce.encode()).hexdigest()

    def _load(self):
        if not self.file.exists():
            return {}

        owners = json.loads(
            self.file.read_text(encoding="utf-8")
        )

        if not isinstance(owners, dict):
            raise ValueError("Invalid workspace registry.")

        return {
            document_id: owner
            for document_id, owner in owners.items()
            if re.fullmatch(r"[a-f0-9]{32}", document_id)
            and isinstance(owner, str)
            and (
                self.uploads / f"{document_id}.pdf"
            ).is_file()
        }

    def _save(self, owners):
        temporary = self.directory / f"{uuid4().hex}.tmp"

        try:
            temporary.write_text(
                json.dumps(owners),
                encoding="utf-8",
            )
            temporary.replace(self.file)
        finally:
            temporary.unlink(missing_ok=True)

    def owned(self, owner):
        with self.lock:
            return {
                key
                for key, value in self._load().items()
                if value == owner
            }

    def reserve(self, owner):
        with self.lock:
            owners = self._load()

            if self.pending:
                raise HTTPException(
                    429,
                    "Another PDF is uploading. Try again shortly.",
                )

            if sum(value == owner for value in owners.values()) >= 20:
                raise HTTPException(
                    429,
                    "This demo allows five PDFs per browser workspace.",
                )

            if len(owners) >= 100:
                raise HTTPException(
                    429,
                    "The demo document limit has been reached.",
                )

            self.pending.add(owner)

    def register(self, owner, document_id):
        with self.lock:
            owners = self._load()
            document_id = UUID(str(document_id)).hex

            if document_id in owners:
                raise ValueError("Document ownership already exists.")

            if not (
                self.uploads / f"{document_id}.pdf"
            ).is_file():
                raise ValueError("Uploaded PDF was not saved.")

            owners[document_id] = owner
            self._save(owners)

    def release(self, owner):
        with self.lock:
            self.pending.discard(owner)


store = WorkspaceStore(
    Path(__file__).resolve().parents[1] / "data"
)

DOCUMENT_PATH = re.compile(
    r"^/documents/([^/]+)/(pages|chunks|index|search|ask|ask/stream)$"
)


class WorkspaceAccess:
    def __init__(self, app):
        self.app = app
        self.processing = asyncio.Lock()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({
                    "type": "websocket.close",
                    "code": 1008,
                })
            else:
                await self.app(scope, receive, send)
            return

        async def reject(status, detail):
            response = JSONResponse(
                {"detail": detail},
                status_code=status,
            )
            await response(scope, receive, send)

        path = scope["path"].rstrip("/") or "/"
        method = scope["method"]

        public_request = (
            method == "GET"
            and path in {"/", "/health"}
        ) or (
            method == "POST"
            and path == "/session"
        )

        if public_request:
            await self.app(scope, receive, send)
            return

        authorization = (
            dict(scope.get("headers", []))
            .get(b"authorization", b"")
            .decode("latin-1")
        )

        owner = store.authenticate(authorization)

        if owner is None:
            await reject(
                401,
                "Browser workspace unavailable. "
                "Reload this page to start a new session.",
            )
            return

        scope.setdefault("state", {})["workspace_owner"] = owner

        allowed = (
            method == "GET"
            and path == "/documents"
        ) or (
            method == "POST"
            and path == "/documents/upload"
        )

        processing = False
        match = DOCUMENT_PATH.fullmatch(path)

        if match:
            try:
                document_id = UUID(match.group(1)).hex
            except ValueError:
                await reject(
                    404,
                    "Document not found in this workspace.",
                )
                return

            try:
                ids = await run_in_threadpool(
                    store.owned,
                    owner,
                )
            except (OSError, ValueError):
                await reject(
                    503,
                    "Workspace storage is unavailable.",
                )
                return

            operation = match.group(2)

            allowed = document_id in ids and (
                (
                    method == "GET"
                    and operation in {"pages", "chunks"}
                )
                or (
                    method == "POST"
                    and operation in {
                        "index",
                        "search",
                        "ask",
                        "ask/stream",
                    }
                )
            )

            processing = allowed and method == "POST"

        if not allowed:
            await reject(
                404,
                "Document not found in this workspace.",
            )
            return

        if processing:
            if self.processing.locked():
                await reject(
                    429,
                    "Another document is processing. "
                    "Please try again shortly.",
                )
                return

            await self.processing.acquire()

        try:
            await self.app(scope, receive, send)
        finally:
            if processing:
                self.processing.release()


app = FastAPI(
    title="DocMind Upload Demo",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.add_exception_handler(
    RequestValidationError,
    safe_request_validation,
)


@app.get("/")
def root():
    return {
        "name": "DocMind",
        "mode": "public_upload",
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/session")
def create_session():
    return JSONResponse(
        {"token": store.issue()},
        headers={"Cache-Control": "no-store"},
    )


@app.get("/documents")
@app.get("/documents/", include_in_schema=False)
def workspace_documents(request: Request):
    try:
        ids = store.owned(request.state.workspace_owner)
        documents = []

        for item in list_documents():
            try:
                document_id = UUID(
                    str(item.get("document_id", ""))
                ).hex
            except (ValueError, TypeError, AttributeError):
                continue

            if document_id in ids:
                documents.append(item)

    except (OSError, ValueError, KeyError) as error:
        raise HTTPException(
            503,
            "Workspace storage is unavailable.",
        ) from error

    return JSONResponse(
        {
            "total_documents": len(documents),
            "documents": documents,
        },
        headers={"Cache-Control": "no-store"},
    )


@app.post("/documents/upload", status_code=201)
async def workspace_upload(
    request: Request,
    file: UploadFile,
):
    owner = request.state.workspace_owner

    try:
        await run_in_threadpool(store.reserve, owner)
    except HTTPException:
        await file.close()
        raise
    except (OSError, ValueError) as error:
        await file.close()
        raise HTTPException(
            503,
            "Workspace storage is unavailable.",
        ) from error

    try:
        uploaded = await local_upload(file)

        await run_in_threadpool(
            store.register,
            owner,
            uploaded["document_id"],
        )

        return uploaded

    except (OSError, ValueError, KeyError) as error:
        raise HTTPException(
            503,
            "The upload could not be registered "
            "to this workspace.",
        ) from error

    finally:
        await run_in_threadpool(store.release, owner)


app.mount("/", local_app)

app.add_middleware(WorkspaceAccess)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://dockmindd.netlify.app",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=[
        "Content-Type",
        "Authorization",
        "ngrok-skip-browser-warning",
    ],
)