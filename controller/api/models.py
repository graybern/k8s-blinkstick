from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field, model_validator


class LEDColor(BaseModel):
    index: int = Field(ge=0)
    r: int = Field(ge=0, le=255)
    g: int = Field(ge=0, le=255)
    b: int = Field(ge=0, le=255)


class LEDCommand(BaseModel):
    action: str = "set"
    leds: list[LEDColor] = []
    effect: str = "solid"
    params: dict = {}
    node: str | None = None


class ClusterCommand(BaseModel):
    action: str = "set"
    nodes: dict[str, list[LEDColor]]
    effect: str = "solid"
    params: dict = {}


class ModeSwitch(BaseModel):
    mode: str


class NodeHealth(BaseModel):
    name: str
    up: bool = True
    cpu_usage: float = 0.0
    memory_usage: float = 0.0
    disk_usage: float = 0.0
    k8s_ready: bool = True
    severity: str = "unknown"
    color: list[int] = [0, 0, 0]
    effect: str = "solid"
    effect_params: dict = {}


class AgentInfo(BaseModel):
    name: str
    present: bool = False
    serial: str | None = None
    leds: int = 0
    online: bool = False
    last_seen: float | None = None
    clock_skew_ms: float | None = None


class LEDState(BaseModel):
    index: int
    r: int
    g: int
    b: int


class NodeStatus(BaseModel):
    name: str
    online: bool = False
    present: bool = False
    device: AgentInfo | None = None
    leds: list[LEDState] = []
    health: NodeHealth | None = None


class ModeInfo(BaseModel):
    name: str
    layer: str
    led_strategy: str
    active: bool = False


class StatusResponse(BaseModel):
    nodes: list[NodeStatus]
    active_mode: str


RESERVED_SONG_NAMES = {"stop", "playing"}


class BeatSheetTiming(BaseModel):
    bpm: int = Field(ge=20, le=300)
    time_signature: str = "4/4"
    loop: bool | int = False
    default_transition: str = "solid"


class BeatSheetMetadata(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9\-]{0,61}[a-z0-9]$")
    title: str = ""
    author: str = ""

    @model_validator(mode="after")
    def check_reserved(self):
        if self.name in RESERVED_SONG_NAMES:
            raise ValueError(f"Reserved name: {self.name}")
        return self


class BeatSheet(BaseModel):
    apiVersion: str = "blinkstick.octolet.int/v1"
    kind: str = "BeatSheet"
    metadata: BeatSheetMetadata
    timing: BeatSheetTiming
    on_end: str = "status"
    palette: dict[str, str] = {}
    node_order: list[str] = []
    sections: dict[str, list[str]] = {}
    beats: list[str | dict] = []

    @model_validator(mode="after")
    def validate_beats(self):
        known_chars = set(self.palette.keys())
        for beat in self.beats:
            if isinstance(beat, str):
                for ch in beat:
                    if ch not in known_chars:
                        raise ValueError(f"Unknown palette char '{ch}' in beat '{beat}'")
            elif isinstance(beat, dict):
                if "section" in beat and beat["section"] not in self.sections:
                    raise ValueError(f"Unknown section '{beat['section']}'")
                if "repeat_section" in beat and beat["repeat_section"] not in self.sections:
                    raise ValueError(f"Unknown section '{beat['repeat_section']}'")
        return self


class SongSummary(BaseModel):
    name: str
    title: str = ""
    author: str = ""
    bpm: int = 120
    loop: bool | int = False
    source: str = "runtime"


class SongDetail(SongSummary):
    beat_sheet: BeatSheet


class PlaybackState(BaseModel):
    playing: bool = False
    song: str | None = None
    started_at: float | None = None
    beat_index: int = 0
    total_beats: int = 0
    elapsed: float = 0.0
    clock_sync: dict[str, float] = {}


class PresetInfo(BaseModel):
    name: str
    title: str
    description: str


class PresetPlayRequest(BaseModel):
    bpm: int = Field(default=120, ge=20, le=300)
    color: str = "#00ff00"
    color2: str = "#0000ff"
    duration: int = Field(default=0, ge=0)


class SongCreate(BaseModel):
    yaml_content: str | None = None
    beat_sheet: BeatSheet | None = None

    @model_validator(mode="after")
    def one_required(self):
        if not self.yaml_content and not self.beat_sheet:
            raise ValueError("Provide yaml_content or beat_sheet")
        if self.yaml_content and self.beat_sheet:
            raise ValueError("Provide yaml_content or beat_sheet, not both")
        return self
