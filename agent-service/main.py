"""
cortex41 FastAPI backend.
Serves REST endpoints and WebSocket connections for real-time agent control.
"""

import asyncio
import json
import os

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.agent.cortex41_agent import Cortex41AgentRunner
from backend.agent.session_manager import get_session_manager
from backend.skills.skill_store import SkillStore

app = FastAPI(title="cortex41", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve the minimal prototype UI
_static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=_static_dir), name="static")

session_manager = get_session_manager()


@app.websocket("/ping")
async def ws_ping(websocket: WebSocket):
    await websocket.accept()
    await websocket.send_text("pong")
    await websocket.close()


@app.websocket("/test-agent")
async def ws_test_agent(websocket: WebSocket):
    """Diagnostic: test agent runner creation without full browser launch."""
    import traceback
    await websocket.accept()
    try:
        await websocket.send_text(json.dumps({"type": "info", "message": "accepted"}))
        agent = Cortex41AgentRunner(session_id="diag", websocket_send_fn=None)
        await websocket.send_text(json.dumps({"type": "info", "message": "runner created"}))
        await agent.initialize()
        await websocket.send_text(json.dumps({"type": "info", "message": "browser launched"}))
        url = await agent.browser.get_page_url()
        await websocket.send_text(json.dumps({"type": "success", "message": f"ready url={url}"}))
        await agent.cleanup()
    except Exception as e:
        await websocket.send_text(json.dumps({"type": "error", "message": f"{type(e).__name__}: {e}"}))
        traceback.print_exc()


@app.get("/")
async def root():
    return FileResponse(os.path.join(_static_dir, "index.html"))


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "agent": "cortex41",
        "active_sessions": session_manager.active_count(),
    }


@app.get("/skills/{user_id}")
async def list_skills(user_id: str):
    """Return all skills for a user — powers the Skill Library UI panel."""
    store = SkillStore()
    skills = await store.list_all_skills(user_id)
    return {"skills": skills, "count": len(skills)}


@app.delete("/skills/{user_id}/{skill_id}")
async def delete_skill(user_id: str, skill_id: str):
    """Disable a skill (user feedback it's wrong or outdated)."""
    store = SkillStore()
    await store.disable_skill(skill_id)
    return {"status": "disabled", "skill_id": skill_id}


@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    """
    Main WebSocket endpoint.

    Messages FROM frontend:
      { "type": "goal",        "text": "...", "user_id": "..." }
      { "type": "audio_chunk", "data": "<base64 PCM>" }
      { "type": "audio_end" }
      { "type": "interrupt",   "new_goal": "..." }
      { "type": "ping" }

    Messages TO frontend:
      { "type": "thinking"|"action"|"success"|"error"|"info",  "message": "...", "data": {...} }
      { "type": "plan",        "message": "...", "data": {sub_tasks: [...]} }
      { "type": "subtask",     "message": "..." }
      { "type": "subtask_done","message": "..." }
      { "type": "screenshot",  "data": "<base64>" }
      { "type": "stats",       "message": "...", "data": {...} }
      { "type": "audio_response", "data": "<base64 audio>" }
      { "type": "pong" }
    """
    await websocket.accept()
    await asyncio.sleep(0.1)  # Brief buffer for client to register acceptance
    print(f"[WS] Session {session_id} accepted")

    async def send_to_client(payload: dict):
        try:
            await websocket.send_text(json.dumps(payload))
        except Exception:
            pass

    # Create agent runner for this session
    print(f"[WS] Creating agent runner...")
    await send_to_client({"type": "info", "message": "Starting agent..."})
    try:
        # Run constructor in a thread — it calls firestore.AsyncClient() which does
        # synchronous credential checks (~1s each × 3 classes) and would block the
        # event loop, causing the browser to close the idle WebSocket.
        agent = await asyncio.to_thread(
            lambda: Cortex41AgentRunner(
                session_id=session_id,
                websocket_send_fn=send_to_client,
            )
        )
    except Exception as exc:
        import traceback
        print(f"[WS] AgentRunner init CRASHED:\n{traceback.format_exc()}")
        await send_to_client({"type": "error", "message": f"Agent init failed: {exc}"})
        return

    print(f"[WS] Agent runner created, launching browser...")
    await send_to_client({"type": "info", "message": "Connecting to Chrome..."})
    try:
        await agent.initialize()
        print(f"[WS] Browser launched OK")
        await send_to_client({"type": "info", "message": "Browser ready. Send a goal to start."})
    except Exception as exc:
        import traceback
        print(f"[Agent init] Failed:\n{traceback.format_exc()}")
        await send_to_client({"type": "error", "message": f"Agent init failed: {exc}"})
    session_manager.register(session_id, agent)

    # Import voice handler here to avoid circular import issues at startup
    from backend.voice.live_api_handler import LiveAPIHandler

    async def on_goal(goal: str):
        asyncio.create_task(agent.run_goal(goal))

    async def on_interrupt(new_goal: str | None):
        await agent.interrupt(new_goal)

    async def on_audio_response(audio_b64: str):
        await send_to_client({"type": "audio_response", "data": audio_b64})

    voice_handler = LiveAPIHandler(on_goal, on_interrupt, on_audio_response)

    # Start voice session in background — wrap so failures don't kill the WS
    async def _voice_safe():
        try:
            await voice_handler.start_session()
        except Exception as e:
            print(f"[Voice] Session error (non-fatal): {e}")
    voice_task = asyncio.create_task(_voice_safe())

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            msg_type = msg.get("type")

            if msg_type == "goal":
                goal = msg.get("text", "")
                user_id = msg.get("user_id", "default")
                if goal.strip():
                    async def _run_safe(g=goal, u=user_id):
                        try:
                            await agent.run_goal(g, u)
                        except Exception as exc:
                            import traceback
                            print(f"[Agent] Fatal error:\n{traceback.format_exc()}")
                            await send_to_client({"type": "error", "message": f"Agent crashed: {exc}"})
                    asyncio.create_task(_run_safe())

            elif msg_type == "audio_chunk":
                await voice_handler.send_audio_chunk(msg.get("data", ""))

            elif msg_type == "audio_end":
                await voice_handler.signal_end_of_turn()

            elif msg_type == "interrupt":
                new_goal = msg.get("new_goal")
                await agent.interrupt(new_goal)

            elif msg_type == "ping":
                await send_to_client({"type": "pong"})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[WS] Session {session_id} error: {e}")
    finally:
        voice_task.cancel()
        await agent.cleanup()
        session_manager.remove(session_id)
