import os
import asyncio
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, WebSocket, Depends, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from routes.auth import get_current_user, get_current_user_ws
from agents.graph import run_task
from browser.agent import BrowserSession
from db.memory import log_task_run, update_task_run

app = FastAPI(title="Astrid - Multi-Agent RAG Browser Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.environ.get("FRONTEND_URL", "*")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok"}


_shared_browser_session: BrowserSession | None = None
_session_lock = asyncio.Lock()


async def get_or_create_browser(headless: bool = False) -> BrowserSession:
    global _shared_browser_session
    async with _session_lock:
        if _shared_browser_session is None or _shared_browser_session.is_closed():
            _shared_browser_session = BrowserSession()
            await _shared_browser_session.start(headless=headless)
        return _shared_browser_session


@app.on_event("shutdown")
async def shutdown_event():
    global _shared_browser_session
    if _shared_browser_session:
        try:
            await _shared_browser_session.close()
        except Exception:
            pass


@app.websocket("/ws/task")
async def task_socket(websocket: WebSocket, user_id: str = Depends(get_current_user_ws)):
    """
    Continuous interactive WebSocket:
    - Receives goals and runs autonomous multi-agent loops.
    - Receives manual interactions (click, type, scroll, navigate) and executes them immediately.
    - Streams live base64 screenshots and agent progress.
    """
    await websocket.accept()

    async def send_update(update: dict):
        try:
            await websocket.send_json(update)
        except Exception:
            pass

    active_agent_task: asyncio.Task | None = None

    try:
        while True:
            payload = await websocket.receive_json()
            event_type = payload.get("event")
            print(f"[WS] Received event: {event_type} | payload: {payload}", flush=True)

            # 0. PING / VIEWPORT REFRESH
            if event_type == "ping":
                if _shared_browser_session and not _shared_browser_session.is_closed():
                    frame = await _shared_browser_session.take_screenshot()
                    if frame:
                        await websocket.send_json({"event": "screenshot", "data": frame})
                continue

            # STOP RUNNING TASK
            elif event_type == "stop":
                if active_agent_task and not active_agent_task.done():
                    active_agent_task.cancel()
                    print("[WS] Active agent task cancelled by user stop!", flush=True)
                    await websocket.send_json({"event": "status", "data": "Task stopped by user."})
                continue

            # 1. RUN AUTONOMOUS AGENT TASK
            elif "goal" in payload or event_type == "goal":
                if active_agent_task and not active_agent_task.done():
                    active_agent_task.cancel()

                goal = payload.get("goal")
                headless = payload.get("headless", False)
                print(f"[WS] Launching task for goal: '{goal}' (headless={headless})...", flush=True)

                session = await get_or_create_browser(headless=headless)
                run_id = log_task_run(user_id, goal)

                async def _run_coro(r_id=run_id, g_text=goal, s_inst=session):
                    try:
                        print(f"[WS] Starting run_task loop for '{g_text}'...", flush=True)
                        res = await run_task(
                            user_id=user_id,
                            goal=g_text,
                            on_update=send_update,
                            headless=headless,
                            session=s_inst
                        )
                        update_task_run(r_id, status="done", result=str(res.get("evidence", ""))[:1000])
                        await websocket.send_json({"event": "complete", "data": res})
                        print("[WS] Task completed successfully!", flush=True)
                    except asyncio.CancelledError:
                        print(f"[WS] run_task for '{g_text}' was cancelled.", flush=True)
                        update_task_run(r_id, status="stopped", result="Stopped by user")
                        await websocket.send_json({"event": "status", "data": "Task stopped."})
                    except Exception as err:
                        print(f"[WS] Task error: {err}", flush=True)
                        update_task_run(r_id, status="failed", result=str(err))
                        await websocket.send_json({"event": "error", "data": str(err)})

                active_agent_task = asyncio.create_task(_run_coro())

            # 2. MANUAL CLICK FROM USER (User clicks on the live preview)
            elif event_type == "manual_click":
                x = payload.get("x", 0)
                y = payload.get("y", 0)
                session = await get_or_create_browser(headless=False)
                await session.manual_click(x, y)
                frame = await session.take_screenshot()
                if frame:
                    await websocket.send_json({"event": "screenshot", "data": frame})

            # 3. MANUAL TYPE FROM USER (User types in manual input bar)
            elif event_type == "manual_type":
                text = payload.get("text", "")
                press_enter = payload.get("press_enter", False)
                session = await get_or_create_browser(headless=False)
                await session.manual_type(text, press_enter=press_enter)
                frame = await session.take_screenshot()
                if frame:
                    await websocket.send_json({"event": "screenshot", "data": frame})

            # 4. MANUAL NAVIGATE FROM USER
            elif event_type == "manual_navigate":
                url = payload.get("url", "https://www.youtube.com")
                session = await get_or_create_browser(headless=False)
                await session.manual_navigate(url)
                frame = await session.take_screenshot()
                if frame:
                    await websocket.send_json({"event": "screenshot", "data": frame})

            # 5. MANUAL SCROLL FROM USER
            elif event_type == "manual_scroll":
                dy = payload.get("dy", 400)
                if _shared_browser_session and _shared_browser_session.page:
                    await _shared_browser_session.manual_scroll(dy)
                    frame = await _shared_browser_session.take_screenshot()
                    if frame:
                        await websocket.send_json({"event": "screenshot", "data": frame})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"WebSocket session exception: {e}")

