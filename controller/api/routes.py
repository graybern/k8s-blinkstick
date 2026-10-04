import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

import yaml

from controller.services.metrics import get_metrics_response

from controller.api.models import (
    LEDCommand,
    ModeSwitch,
    ModeInfo,
    NodeStatus,
    NodeHealth,
    AgentInfo,
    LEDState,
    StatusResponse,
    SongSummary,
    SongDetail,
    SongCreate,
    BeatSheet,
    PlaybackState,
    PresetInfo,
    PresetPlayRequest,
)
from controller.engine.presets import PRESETS

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
            clock_skew_ms=mqtt.get_clock_skew(name),
        )
        for name, info in sorted(registry.items())
    ]


# ── Songs (order matters: fixed paths before {name}) ──

@router.get("/songs")
async def list_songs(request: Request) -> list[SongSummary]:
    store = request.app.state.song_store
    return [SongSummary(**s) for s in store.list_songs()]


@router.post("/songs")
async def create_song(request: Request, body: SongCreate) -> dict:
    store = request.app.state.song_store
    if body.beat_sheet:
        sheet = body.beat_sheet
    else:
        try:
            parsed = yaml.safe_load(body.yaml_content)
            sheet = BeatSheet(**parsed)
        except Exception as e:
            return JSONResponse(status_code=400, content={"error": f"Invalid YAML: {e}"})
    engine = request.app.state.engine
    ok = engine.run_coroutine(store.save_song(sheet))
    if ok:
        return {"saved": sheet.metadata.name}
    return JSONResponse(status_code=500, content={"error": "Failed to save"})


@router.post("/songs/stop")
async def stop_song(request: Request) -> dict:
    engine = request.app.state.engine
    music = engine.get_music_mode()
    if not music or not music._playing:
        return {"status": "not playing"}
    result = engine.run_coroutine(music.stop_playback())
    on_end = result.get("on_end", "status") if result else "status"
    engine.run_coroutine(engine.set_mode(on_end))
    return result or {"stopped": True}


@router.get("/songs/playing")
async def playing_status(request: Request) -> PlaybackState:
    engine = request.app.state.engine
    music = engine.get_music_mode()
    if music:
        return music.get_playback_state()
    return PlaybackState()


@router.get("/songs/{name}")
async def get_song(request: Request, name: str) -> SongDetail | dict:
    store = request.app.state.song_store
    sheet = store.get_song(name)
    if not sheet:
        return JSONResponse(status_code=404, content={"error": f"Song not found: {name}"})
    return SongDetail(
        name=sheet.metadata.name,
        title=sheet.metadata.title,
        author=sheet.metadata.author,
        bpm=sheet.timing.bpm,
        loop=sheet.timing.loop,
        source=store.get_source(name) or "unknown",
        beat_sheet=sheet,
    )


@router.delete("/songs/{name}")
async def delete_song(request: Request, name: str) -> dict:
    store = request.app.state.song_store
    engine = request.app.state.engine
    ok = engine.run_coroutine(store.delete_song(name))
    if ok:
        return {"deleted": name}
    return JSONResponse(status_code=404, content={"error": f"Song not found: {name}"})


@router.post("/songs/{name}/play")
async def play_song(request: Request, name: str) -> dict:
    engine = request.app.state.engine
    if engine.active_mode != "music":
        engine.run_coroutine(engine.set_mode("music"))
    music = engine.get_music_mode()
    if not music:
        return JSONResponse(status_code=500, content={"error": "Music mode not available"})
    result = engine.run_coroutine(music.play_song(name))
    if result and "error" in result:
        return JSONResponse(status_code=400, content=result)
    return result or {}


@router.get("/songs/{name}/export")
async def export_song(request: Request, name: str):
    store = request.app.state.song_store
    yaml_str = store.export_yaml(name)
    if not yaml_str:
        return JSONResponse(status_code=404, content={"error": f"Song not found: {name}"})
    return PlainTextResponse(content=yaml_str, media_type="text/yaml")


# ── Presets ──

@router.get("/presets")
async def list_presets(request: Request) -> list[PresetInfo]:
    return [PresetInfo(name=k, **v) for k, v in PRESETS.items()]


@router.post("/presets/{name}/play")
async def play_preset(request: Request, name: str, body: PresetPlayRequest = PresetPlayRequest()) -> dict:
    if name not in PRESETS:
        return JSONResponse(status_code=404, content={"error": f"Unknown preset: {name}"})
    engine = request.app.state.engine
    if engine.active_mode != "music":
        engine.run_coroutine(engine.set_mode("music"))
    music = engine.get_music_mode()
    if not music:
        return JSONResponse(status_code=500, content={"error": "Music mode not available"})
    result = engine.run_coroutine(music.play_preset(name, body.bpm, body.color, body.color2))
    if result and "error" in result:
        return JSONResponse(status_code=400, content=result)
    return result or {}


# ── Observability ──

@router.get("/events")
async def get_events(request: Request, limit: int = 50, type: str | None = None) -> list[dict]:
    event_log = request.app.state.event_log
    return event_log.get_recent(limit=limit, event_type=type)


@router.get("/mqtt/messages")
async def get_mqtt_messages(request: Request, limit: int = 50) -> list[dict]:
    mqtt = request.app.state.mqtt_client
    return mqtt.get_mqtt_messages(limit=limit)


@router.get("/metrics")
async def prometheus_metrics():
    body, content_type = get_metrics_response()
    return Response(content=body, media_type=content_type)
