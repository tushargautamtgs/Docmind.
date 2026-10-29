from fastapi import FastAPI
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