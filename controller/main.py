import asyncio
import logging
import threading

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

app = FastAPI(title="BlinkStick Controller")
app.include_router(router)


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


def _run_engine(engine: ModeEngine, default_mode: str):
    loop = asyncio.new_event_loop()
    engine.set_loop(loop)
    try:
        loop.run_until_complete(engine.start(default_mode))
        log.info("Engine running")
        loop.run_forever()
    except Exception:
        log.exception("Engine crashed")
    finally:
        loop.run_until_complete(engine.stop())
        loop.close()
        log.info("Engine stopped")


def main():
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
        target=_run_engine,
        args=(engine, DEFAULT_MODE),
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
