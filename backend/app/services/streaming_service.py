import json

import httpx
from starlette.concurrency import run_in_threadpool

from app.services import diagnostics
from app.services.answer_service import (
    MODEL_NAME,
    OLLAMA_URL,
    SYSTEM_PROMPT,
    generate_answer,
)
from app.services.calculation_plan_service import route_question


async def stream_answer(question: str, matches: list[dict]):
    # Calculations use the existing workflow.
    if route_question(question) != "direct" or not matches:
        yield await run_in_threadpool(generate_answer, question, matches)
        return

    token = diagnostics.begin()

    try:
        diagnostics.record("selected_route", route="direct")
        diagnostics.record(
            "retrieved_chunks",
            chunk_ids=[match["chunk_id"] for match in matches],
        )

        ordered = sorted(
            matches,
            key=lambda item: (item["page"], item["chunk_id"]),
        )

        context = "\n\n".join(
            f"[Page {match['page']}, Chunk {match['chunk_id']}]\n"
            f"{match['text']}"
            for match in ordered
        )

        payload = {
            "model": MODEL_NAME,
            "stream": True,
            "think": True,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"DOCUMENT EXCERPTS:\n{context}\n\n"
                        f"QUESTION:\n{question}"
                    ),
                },
            ],
            "options": {
                "temperature": 0,
                "num_ctx": 4096,
                "num_predict": 2048,
            },
        }

        pieces = []
        finished = False

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(300.0, connect=10.0)
        ) as client:
            async with client.stream(
                "POST", OLLAMA_URL, json=payload
            ) as response:
                response.raise_for_status()

                async for line in response.aiter_lines():
                    if not line.strip():
                        continue

                    event = json.loads(line)

                    if not isinstance(event, dict) or event.get("error"):
                        raise ValueError("Invalid or failed model stream.")

                    message = event.get("message", {})

                    if not isinstance(message, dict):
                        raise ValueError("Invalid model message.")

                    text = message.get("content", "")

                    if not isinstance(text, str):
                        raise ValueError("Invalid answer chunk.")

                    # Thinking text is not forwarded.
                    if text:
                        pieces.append(text)
                        yield text

                    if event.get("done") is True:
                        if event.get("done_reason") == "length":
                            raise ValueError(
                                "Model output reached its limit."
                            )

                        finished = True
                        break

        if not finished or not "".join(pieces).strip():
            raise ValueError(
                "Model stream ended without a complete answer."
            )

        diagnostics.record(
            "raw_direct_answer",
            response={"message": {"content": "".join(pieces)}},
        )

    except Exception as error:
        diagnostics.record(
            "failure",
            error_type=type(error).__name__,
            detail=str(error),
        )
        raise

    finally:
        diagnostics.request_id.reset(token)