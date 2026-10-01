import asyncio
import logging
import threading
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from controller.config import DEFAULT_MODE, LOG_LEVEL
from controller.services.mqtt_client import MQTTClient
from controller.services.prometheus import PrometheusClient
from controller.services.k8s import K8sClient
from controller.engine.mode_engine import ModeEngine
from controller.api.routes import router

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("controller")


def _run_engine_loop(engine: ModeEngine, default_mode: str):
    loop = asyncio.new_event_loop()
    engine.set_loop(loop)
    try:
        loop.run_until_complete(engine.start(default_mode))
        loop.run_forever()
    except Exception:
        log.exception("Engine loop crashed")
    finally:
        loop.run_until_complete(engine.stop())
        loop.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    mqtt_client = MQTTClient()
    prometheus = PrometheusClient()
    k8s = K8sClient()
    engine = ModeEngine(mqtt_client, prometheus)

    app.state.mqtt_client = mqtt_client
    app.state.prometheus = prometheus
    app.state.k8s = k8s
    app.state.engine = engine

    mqtt_client.connect()
    log.info("MQTT client connected")

    engine_thread = threading.Thread(
        target=_run_engine_loop,
        args=(engine, DEFAULT_MODE),
        daemon=True,
    )
    engine_thread.start()
    app.state.engine_thread = engine_thread
    log.info("Engine thread started")

    yield

    log.info("Shutting down...")
    engine.request_stop()
    engine_thread.join(timeout=5.0)
    mqtt_client.disconnect()
    await prometheus.close()
    await k8s.close()
    log.info("Controller stopped")


app = FastAPI(title="BlinkStick Controller", lifespan=lifespan)
app.include_router(router)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/readyz")
async def readyz():
    mqtt = app.state.mqtt_client
    if not mqtt.is_connected():
        return JSONResponse(
            status_code=503,
            content={"status": "not ready", "reason": "MQTT not connected"},
        )
    return {"status": "ready"}


if __name__ == "__main__":
    uvicorn.run(
        "controller.main:app",
        host="0.0.0.0",
        port=8000,
    )
