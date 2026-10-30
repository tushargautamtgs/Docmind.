from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.exception_handlers import request_validation_exception_handler
from app.api.documents import router as documents_router

app = FastAPI(
    title="DocMind API",
    description="Ask questions about your PDFs.",
    version="0.1.0"
)

app.include_router(documents_router)


@app.get("/")
def root():
    return {
        "name": "DocMind",
        "message": "Backend is running"
    }


@app.get("/health")
def health():
    return {"status": "ok"}

@app.exception_handler(RequestValidationError)
async def safe_request_validation(request: Request, error: RequestValidationError):
    if request.url.path.endswith("/ask"):
        # FastAPI's default errors echo submitted input and validator context.
        return JSONResponse(status_code=422, content={"detail": [
            {"type": item["type"], "loc": item["loc"], "msg": "Invalid request value."}
            for item in error.errors()
        ]})
    return await request_validation_exception_handler(request, error)
