"""Local stand-ins for Jev, Groq, and Cartesia, so the code can be tested with no keys.

Their answers come from simple keyword rules, so runs against them say nothing about real
accuracy or latency. Their request checks follow the documented API shapes.
"""

import base64
import json
import re

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse, StreamingResponse

from agent.decisions import INTENT_CRITERIA

app = FastAPI()
INTENTS = list(INTENT_CRITERIA)


def _latest(state) -> str:
    if isinstance(state, dict) and "messages" in state:
        return state["messages"][-1]["text"].lower()
    return str(state).lower()


def _intent(text: str) -> str:
    if re.search(r"balance|my bill|owe", text):
        return "account_lookup"
    if "weather" in text:
        return "out_of_scope"
    if "manager" in text:
        return "human_request"
    if "thanks" in text:
        return "chitchat"
    if "change my plan" in text:
        return "plan_change"
    return "plan_info"


def _answer(name: str, question: dict, text: str) -> dict:
    kind = question["type"]
    if kind == "choice":
        options = list(question["criteria"])
        choice = _intent(text) if name == "intent" else options[0]
        rest = (1 - 0.9) / max(1, len(options) - 1)
        return {
            "type": "choice",
            "choice": choice,
            "confidence": 0.9,
            "probabilities": {o: (0.9 if o == choice else rest) for o in options},
        }
    if kind == "score":
        levels = question["criteria"]
        return {
            "type": "score",
            "score": 0.2,
            "confidence": 0.8,
            "legend": {str(i): v for i, v in enumerate(levels)},
            "probabilities": {str(i): (0.8 if i == 0 else 0.2 / (len(levels) - 1)) for i in range(len(levels))},
        }
    value = 0.1
    if name == "needs_human" and "manager" in text:
        value = 0.95
    if name == "needs_account_access" and _intent(text) == "account_lookup":
        value = 0.95
    if name == "requests_account_change" and "change my plan" in text:
        value = 0.95
    if name == "about_another_person" and "my wife" in text:
        value = 0.95
    if name == "answerable_from_kb":
        value = 0.05 if "weather" in text else 0.9
    return {"type": "noul", "noul": value}


@app.post("/v1/systemone")
async def systemone(request: Request):
    if not request.headers.get("authorization", "").startswith("Bearer "):
        raise HTTPException(401)
    body = await request.json()
    for field in ("model", "state", "questions"):
        if field not in body:
            return JSONResponse({"detail": f"missing {field}"}, status_code=422)
    text = _latest(body["state"])
    return {
        "model": body["model"],
        "answers": {name: _answer(name, q, text) for name, q in body["questions"].items()},
        "usage": {"input_tokens": 200, "output_tokens": 10},
    }


@app.get("/openai/v1/models")
async def models():
    return {"data": [{"id": "llama-3.3-70b-versatile"}, {"id": "llama-3.1-8b-instant"}, {"id": "whisper-large-v3-turbo"}]}


@app.post("/openai/v1/audio/transcriptions")
async def transcriptions(request: Request):
    form = await request.form()
    if "file" not in form or "model" not in form:
        raise HTTPException(422)
    return {"text": "How much is the Go Smart plan?"}


IDENTITY_JSON = {
    "my number": {
        "phone": "01995550101",
        "full_name": "Mona Adel",
        "national_id_last4": "4471",
        "date_of_birth": "",
    },
    "wrong details": {
        "phone": "01995550101",
        "full_name": "Mona Adel",
        "national_id_last4": "0000",
        "date_of_birth": "",
    },
    "someone else": {
        "phone": "01995550104",
        "full_name": "Mona Adel",
        "national_id_last4": "4471",
        "date_of_birth": "",
    },
    "partial number": {"phone": "0199", "full_name": "", "national_id_last4": "", "date_of_birth": ""},
    "rest of the number": {"phone": "5550101", "full_name": "", "national_id_last4": "", "date_of_birth": ""},
    "born on": {"phone": "", "full_name": "", "national_id_last4": "", "date_of_birth": "1990-05-12"},
}


def _json_reply(messages: list[dict]) -> str:
    system = messages[0]["content"]
    user = messages[-1]["content"].lower()
    if system.startswith("Extract identity"):
        for trigger, details in IDENTITY_JSON.items():
            if trigger in user:
                return json.dumps(details)
        return json.dumps({"phone": "", "full_name": "", "national_id_last4": "", "date_of_birth": ""})
    if system.startswith("You grade"):
        return json.dumps({"pass": True, "reason": "Mock judge."})
    return json.dumps({
        "intent": _intent(user), "sentiment": "calm", "urgency": "low", "needs_human": False, "answerable_from_kb": True,
    })


@app.post("/openai/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    messages = body["messages"]
    # Lets the tests see exactly what reached the model.
    app.state.last_prompts.append(messages)
    if not body.get("stream"):
        content = _json_reply(messages)
        return {
            "id": "mock",
            "object": "chat.completion",
            "created": 0,
            "model": body["model"],
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        }

    async def events():
        for piece in ["It costs one hundred ", "thirty pounds. ", "You get eight gigabytes."]:
            chunk = {
                "id": "mock",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": body["model"],
                "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(chunk)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


@app.websocket("/tts/websocket")
async def tts(socket: WebSocket):
    # Pipecat sends the version in a header, and agent/providers/tts.py sends it in the query string.
    has_version = socket.headers.get("cartesia-version") or socket.query_params.get("cartesia_version")
    if not socket.headers.get("x-api-key") or not has_version:
        await socket.close(code=4401)
        return
    await socket.accept()
    silence = base64.b64encode(b"\0\0" * 2400).decode()
    while True:
        message = json.loads(await socket.receive_text())
        if message.get("cancel"):
            continue
        for field in ("model_id", "transcript", "voice", "output_format", "context_id"):
            assert field in message, field
        context_id = message["context_id"]
        await socket.send_text(json.dumps({"type": "chunk", "data": silence, "done": False, "context_id": context_id}))
        await socket.send_text(json.dumps({"type": "done", "done": True, "context_id": context_id}))


app.state.last_prompts = []
