"""Opt-in local JSONL diagnostics. Never included in API responses."""
import json
import logging
import os
from contextvars import ContextVar
from uuid import uuid4

logger = logging.getLogger("docmind.diagnostics")
request_id = ContextVar("diagnostic_request_id", default=None)


def begin():
    return request_id.set(uuid4().hex)


def record(event, **fields):
    path = os.environ.get("DOCMIND_DIAGNOSTICS_PATH")
    if not path:
        return
    entry = dict(request_id=request_id.get(), event=event, **fields)
    try:
        # Quotes/raw extraction contain private data: explicit opt-in, owner-only file.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as output:
            output.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        logger.warning("Could not write local diagnostics", exc_info=False)
