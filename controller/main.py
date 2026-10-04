import asyncio
import logging
import os
import threading

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from controller.config import DEFAULT_MODE, LOG_LEVEL
from controller.services.mqtt_client import MQTTClient
from controller.services.prometheus import PrometheusClient
from controller.services.k8s import K8sClient
from controller.services.song_store import SongStore
from controller.services.event_log import EventLog
from controller.engine.mode_engine import ModeEngine
from controller.api.routes import router
from controller.api.web_routes import web_router
from controller.api.ws import ConnectionManager

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("controller")

app = FastAPI(title="BlinkStick Controller")
app.include_router(router)
app.include_router(web_router)

static_dir = os.path.join(os.path.dirname(__file__), "..", "web", "static")
if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

templates_dir = os.path.join(os.path.dirname(__file__), "templates")
if os.path.isdir(templates_dir):
    app.state.templates = Jinja2Templates(directory=templates_dir)

ws_manager = ConnectionManager()
app.state.ws_manager = ws_manager


@app.websocket("/ws/live")
async def ws_live(websocket: WebSocket):
    if hasattr(app.state, 'engine') and not app.state.engine._main_loop:
        app.state.engine.set_broadcast(ws_manager.broadcast, asyncio.get_event_loop())
    if not await ws_manager.connect(websocket):
        return
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await ws_manager.disconnect(websocket)
    except Exception:
        await ws_manager.disconnect(websocket)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/readyz")
async def readyz():
    if not hasattr(app.state, "mqtt_client"):
        return JSONResponse(status_code=503, content={"status": "not ready", "reason": "starting"})
    mqtt = app.state.mqtt_client
    if not mqtt.is_connected():
        return JSONResponse(status_code=503, content={"status": "not ready", "reason": "MQTT not connected"})
    return {"status": "ready"}


def _run_engine(engine: ModeEngine, song_store: SongStore, default_mode: str):
    loop = asyncio.new_event_loop()
    engine.set_loop(loop)
    try:
        loop.run_until_complete(song_store.start())
        log.info("Song store started")
        loop.run_until_complete(engine.start(default_mode))
        log.info("Engine running")
        loop.run_forever()
    except Exception:
        log.exception("Engine crashed")
    finally:
        loop.run_until_complete(song_store.stop())
        loop.run_until_complete(engine.stop())
        loop.close()
        log.info("Engine stopped")


def main():
    event_log = EventLog()
    mqtt_client = MQTTClient(event_log=event_log)
    prometheus = PrometheusClient()
    k8s = K8sClient()
    song_store = SongStore(k8s)
    engine = ModeEngine(mqtt_client, prometheus, song_store)

    app.state.event_log = event_log
    app.state.mqtt_client = mqtt_client
    app.state.prometheus = prometheus
    app.state.k8s = k8s
    app.state.song_store = song_store
    app.state.engine = engine

    mqtt_client.connect()
    log.info("MQTT client connected")

    engine_thread = threading.Thread(
        target=_run_engine,
        args=(engine, song_store, DEFAULT_MODE),
        daemon=True,
    )
    engine_thread.start()
    log.info("Engine thread started")

    log.info("Starting uvicorn...")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")

    log.info("Uvicorn exited, cleaning up...")
    engine.request_stop()
    engine_thread.join(timeout=5.0)
    mqtt_client.disconnect()
    log.info("Controller stopped")


if __name__ == "__main__":
    main()
