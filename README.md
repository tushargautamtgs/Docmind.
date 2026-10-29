# DocMind

A local FastAPI backend for learning to build PDF question answering step by step.
This initial skeleton provides only `/` and `/health`. Health reports that the API
process is running; it does not check models or documents. No embedding model is
loaded at startup.

## Run locally

The separate virtual environment was created with Homebrew ARM64 Python 3.15.2.
In a terminal, activate it and install dependencies:

```sh
cd /Users/tushar/Desktop/docmind/backend
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Start the development server from the same `backend/` directory:

```sh
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000/docs for interactive API documentation.
Open http://127.0.0.1:8000/health or check it in another terminal:

```sh
curl http://127.0.0.1:8000/health
```

Expected response: `{"status":"ok"}`. The root URL returns
`{"name":"DocMind","message":"Backend is running"}`.
The generated API contract is at http://127.0.0.1:8000/openapi.json.
Stop the server with Control-C. If port 8000 is occupied, choose an available port
with `--port 8001` and use that port in the URLs.

## Structure

- `backend/app/main.py` creates the application and defines its initial routes,
  similar to a small Spring Boot application with controller methods.
- `backend/app/api/` is reserved for future route modules (controllers).
- `backend/app/services/` is reserved for future processing and AI logic (services).
- `backend/data/uploads/` and `backend/data/indexes/` will hold local documents
  and generated search data. Git ignores their contents except `.gitkeep` files,
  which preserve the empty folders.
- `backend/requirements.txt` lists dependencies; `backend/.venv/` isolates them
  from system Python and the practice project.

PDF processing, retrieval, and AI endpoints are future steps. Ollama and its
existing model require no changes for this skeleton.

## Initial setup status

The project files and isolated virtual environment are created. The initial
dependency installation could not reach `pypi.org` because DNS resolution failed
in the setup session. Run the installation command above from a terminal with
network access before starting the server. Live checks of `/`, `/health`, `/docs`,
and `/openapi.json` remain pending until dependencies are installed.
