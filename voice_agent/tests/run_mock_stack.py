"""Runs the live server against the local mock services, with no keys: python -m tests.run_mock_stack

Timings and answers from this stack are meaningless. It only proves the audio pipeline is wired correctly.
"""

import os
import threading
from pathlib import Path

import uvicorn

MOCK_PORT = 9100
LIVE_PORT = int(os.environ.get("PORT", "7861"))
ROOT = Path(__file__).parent.parent

os.environ.update({
    "JEV_API_KEY": "mock", "GROQ_API_KEY": "mock", "CARTESIA_API_KEY": "mock",
    "JEV_BASE_URL": f"http://127.0.0.1:{MOCK_PORT}",
    "GROQ_BASE_URL": f"http://127.0.0.1:{MOCK_PORT}/openai/v1",
    "CARTESIA_WS_URL": f"ws://127.0.0.1:{MOCK_PORT}/tts/websocket",
    "RESULTS_DIR": str(ROOT / "results" / "mock"),
})

from live.server import app as live_app  # noqa: E402  The settings above must be in place first.
from tests.mock_services import app as mock_app  # noqa: E402

threading.Thread(
    target=lambda: uvicorn.run(mock_app, host="127.0.0.1", port=MOCK_PORT, log_level="warning"), daemon=True
).start()
uvicorn.run(live_app, host="127.0.0.1", port=LIVE_PORT)
