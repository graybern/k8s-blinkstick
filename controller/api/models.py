from __future__ import annotations

from pydantic import BaseModel, Field


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
