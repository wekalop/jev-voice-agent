"""Live voice mode: python -m live.server, then open http://localhost:7860

The browser connects over WebRTC to this machine, which calls the cloud APIs.
Voice activity detection (Silero) is the one small model that runs locally.
"""

import asyncio
import json
import os
import secrets
from pathlib import Path

import uvicorn
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.request_handler import (
    SmallWebRTCPatchRequest,
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

from agent import prompts
from agent.config import load_settings
from agent.jev import JevClient
from agent.kb import KnowledgeBase
from agent.providers.llm import make_llm
from agent.timing import TimingLog
from agent.turn import Agent
from live import access, ice
from live.factory import make_live_stt, make_live_tts
from live.monitor import monitor
from live.processors import BrainProcessor, FirstAudioTap, TurnTracker, VoiceCueProcessor

HERE = Path(__file__).parent
settings = load_settings()
kb = KnowledgeBase(settings.kb_dir)
log_path = settings.results_dir / "turns_live.jsonl"
# How long a silence ends a turn. This wait comes before t_end_of_speech, so it adds to what the caller feels.
VAD_STOP_SECS = float(os.environ.get("VAD_STOP_SECS", "0.5"))

# A limit per call, so that a forgotten open tab can't use up the free allowances.
MAX_CALL_SECONDS = int(os.environ.get("MAX_CALL_SECONDS", "600"))
CONNECT_TIMEOUT_SECONDS = 30

access.check_startup()
app = FastAPI()
app.middleware("http")(access.gate)
app.get("/login")(access.login_page)
app.post("/login")(access.login)
handler = SmallWebRTCRequestHandler()
# The free limits suit one caller, and the live view holds one conversation.
current_call = {"busy": False, "token": None}


async def run_call(connection: SmallWebRTCConnection) -> None:
    transport = SmallWebRTCTransport(
        webrtc_connection=connection,
        params=TransportParams(audio_in_enabled=True, audio_out_enabled=True),
    )
    jev = JevClient(settings.jev_api_key, settings.jev_base_url, settings.jev_model)
    agent = Agent(
        settings=settings,
        kb=kb,
        jev=jev,
        llm=make_llm(settings),
        extractor=make_llm(settings, "groq"),
        mode=settings.jev_mode,
    )
    tracker = TurnTracker(TimingLog(log_path))
    voice = VoiceCueProcessor()
    pipeline = Pipeline([
        transport.input(),
        VADProcessor(vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=VAD_STOP_SECS))),
        voice,
        make_live_stt(settings),
        BrainProcessor(agent, tracker, voice),
        make_live_tts(settings),
        FirstAudioTap(tracker),
        transport.output(),
    ])
    task = PipelineTask(pipeline, params=PipelineParams(audio_in_sample_rate=16000, audio_out_sample_rate=24000))

    connected = asyncio.Event()

    async def enforce_limits():
        try:
            await asyncio.wait_for(connected.wait(), CONNECT_TIMEOUT_SECONDS)
            await asyncio.sleep(MAX_CALL_SECONDS)
        except asyncio.TimeoutError:
            pass
        await task.cancel()

    limits = asyncio.create_task(enforce_limits())

    @transport.event_handler("on_client_connected")
    async def on_connected(_transport, _client):
        connected.set()
        monitor.start_call(prompts.GREETING)
        # The policy requires saying this is a virtual assistant at the start of the call.
        await task.queue_frames([TTSSpeakFrame(prompts.GREETING)])
        # Warms the connection so the first turn measures Jev rather than connection setup.
        asyncio.create_task(jev.warm_up())

    @transport.event_handler("on_client_disconnected")
    async def on_disconnected(_transport, _client):
        await task.cancel()

    try:
        await PipelineRunner(handle_sigint=False).run(task)
    finally:
        limits.cancel()
        monitor.end_call()
        current_call["busy"] = False
        await jev.close()


@app.get("/")
async def index():
    return FileResponse(HERE / "index.html")


@app.get("/guide")
async def guide():
    return FileResponse(HERE.parent / "project_guide.html")


@app.post("/api/offer")
async def offer(request: SmallWebRTCRequest, background_tasks: BackgroundTasks):
    new_call = not request.pc_id
    if new_call and current_call["busy"]:
        return JSONResponse({"detail": "Someone is already on a call."}, status_code=409)
    if new_call:
        current_call.update(busy=True, token=secrets.token_urlsafe(24))

    async def on_connection(connection: SmallWebRTCConnection):
        background_tasks.add_task(run_call, connection)

    try:
        handler.update_ice_servers(ice.server_side(await ice.browser_servers()) or None)
        answer = await handler.handle_web_request(request=request, webrtc_connection_callback=on_connection)
    except Exception:
        if new_call:
            current_call["busy"] = False
        raise
    return {**answer, "call_token": current_call["token"]}


@app.get("/api/ice")
async def ice_servers():
    return JSONResponse({"iceServers": await ice.browser_servers()})


@app.patch("/api/offer")
async def ice_candidate(request: SmallWebRTCPatchRequest):
    await handler.handle_patch_request(request)
    return {"status": "success"}


@app.get("/api/live")
async def live(request: Request):
    # Only the current caller can read the conversation. Anyone else just sees whether the line is busy.
    token = request.headers.get("x-call-token", "")
    if token and current_call["token"] and secrets.compare_digest(token, current_call["token"]):
        return JSONResponse(monitor.snapshot())
    return JSONResponse({"active": current_call["busy"], "state": "idle", "greeting": None, "turns": [], "summary": {}})


if __name__ == "__main__":
    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "7860")))
