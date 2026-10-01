import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from controller.api.models import (
    LEDCommand,
    ModeSwitch,
    ModeInfo,
    NodeStatus,
    NodeHealth,
    AgentInfo,
    LEDState,
    StatusResponse,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")


@router.get("/status")
async def get_status(request: Request) -> StatusResponse:
    engine = request.app.state.engine
    mqtt = request.app.state.mqtt_client

    registry = mqtt.get_node_registry()
    last_published = engine.get_last_published()
    status_mode = engine.get_status_mode()
    health_data = status_mode.get_node_health() if status_mode else {}

    nodes = []
    for name, info in sorted(registry.items()):
        leds = []
        published = last_published.get(name, {})
        for led in published.get("leds", []):
            leds.append(LEDState(
                index=led["index"], r=led["r"], g=led["g"], b=led["b"],
            ))

        health = None
        h = health_data.get(name)
        if h and status_mode:
            sm = status_mode
            if sm:
                sev, color, effect, params = sm._compute_state(name, h)
                health = NodeHealth(
                    name=name,
                    up=h.get("up", False),
                    cpu_usage=h.get("cpu", 0.0),
                    memory_usage=h.get("memory", 0.0),
                    disk_usage=h.get("disk", 0.0),
                    k8s_ready=h.get("k8s_ready", False),
                    severity=sev,
                    color=list(color),
                    effect=effect,
                    effect_params=params,
                )

        nodes.append(NodeStatus(
            name=name,
            online=info.get("online", False),
            present=info.get("present", False),
            device=AgentInfo(
                name=name,
                present=info.get("present", False),
                serial=info.get("serial"),
                leds=info.get("leds", 0),
                online=info.get("online", False),
                last_seen=info.get("last_seen"),
            ),
            leds=leds,
            health=health,
        ))

    return StatusResponse(nodes=nodes, active_mode=engine.active_mode)


@router.get("/modes")
async def get_modes(request: Request) -> list[ModeInfo]:
    engine = request.app.state.engine
    modes = engine.get_available_modes()
    return [
        ModeInfo(
            name=m["name"],
            layer=m["layer"],
            led_strategy=m["led_strategy"],
            active=m["name"] == engine.active_mode,
        )
        for m in modes
    ]


@router.get("/modes/active")
async def get_active_mode(request: Request) -> dict:
    engine = request.app.state.engine
    return {"mode": engine.active_mode}


@router.post("/modes/active")
async def set_active_mode(request: Request, body: ModeSwitch) -> dict:
    engine = request.app.state.engine
    available = [m["name"] for m in engine.get_available_modes()]
    if body.mode not in available:
        return JSONResponse(
            status_code=400,
            content={"error": f"Unknown mode: {body.mode}. Available: {available}"},
        )
    engine.run_coroutine(engine.set_mode(body.mode))
    return {"mode": engine.active_mode}


@router.post("/direct")
async def direct_control(request: Request, body: LEDCommand) -> dict:
    engine = request.app.state.engine
    if engine.active_mode != "direct":
        return JSONResponse(
            status_code=409,
            content={"error": "Direct control only available in direct mode. "
                     f"Current mode: {engine.active_mode}"},
        )
    payload = {
        "action": body.action,
        "leds": [led.model_dump() for led in body.leds],
        "effect": body.effect,
        "params": body.params,
    }
    if body.node:
        payload["node"] = body.node
    engine.run_coroutine(engine.direct_command(payload))
    return {"status": "sent", "node": body.node or "all"}


@router.get("/nodes")
async def get_nodes(request: Request) -> list[AgentInfo]:
    mqtt = request.app.state.mqtt_client
    registry = mqtt.get_node_registry()
    return [
        AgentInfo(
            name=name,
            present=info.get("present", False),
            serial=info.get("serial"),
            leds=info.get("leds", 0),
            online=info.get("online", False),
            last_seen=info.get("last_seen"),
        )
        for name, info in sorted(registry.items())
    ]
