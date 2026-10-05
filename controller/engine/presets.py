from controller.api.models import BeatSheet, BeatSheetMetadata, BeatSheetTiming


PRESETS = {
    "chase": {"title": "Chase", "description": "Light sweeps left to right"},
    "alternate": {"title": "Alternate", "description": "Odd and even nodes toggle"},
    "rainbow": {"title": "Rainbow", "description": "Rotating color wheel"},
    "flash": {"title": "Flash", "description": "All on, all off strobe"},
    "police": {"title": "Police", "description": "Red and blue alternating"},
}


def hex_to_upper(hex_color: str) -> str:
    return hex_color.lstrip("#").upper()[:6]


def generate_preset(
    name: str, node_order: list[str], bpm: int,
    color: str = "#00ff00", color2: str = "#0000ff",
) -> BeatSheet:
    n = len(node_order)
    if n == 0:
        node_order = ["node-0"]
        n = 1

    c1 = hex_to_upper(color)
    c2 = hex_to_upper(color2)

    palette = {"X": f"#{c1}", ".": "#000000", "Y": f"#{c2}"}
    beats: list[str | dict] = []

    if name == "chase":
        for i in range(n):
            beats.append("." * i + "X" + "." * (n - i - 1))

    elif name == "alternate":
        on = "".join("X" if i % 2 == 0 else "." for i in range(n))
        off = "".join("." if i % 2 == 0 else "X" for i in range(n))
        beats = [on, off, on, off, on, off, on, off]

    elif name == "rainbow":
        palette = {}
        hues = ["#FF0000", "#FF8800", "#FFFF00", "#00FF00", "#0088FF", "#8800FF"]
        for i, h in enumerate(hues):
            palette[str(i)] = h
        palette["."] = "#000000"
        for offset in range(len(hues)):
            beat = ""
            for j in range(n):
                beat += str((j + offset) % len(hues))
            beats.append(beat)

    elif name == "flash":
        beats = ["X" * n, "." * n, "X" * n, "." * n, "X" * n, "." * n, "X" * n, "." * n]

    elif name == "police":
        palette["X"] = "#FF0000"
        palette["Y"] = "#0000FF"
        left = "X" * (n // 2) + "." * (n - n // 2)
        right = "." * (n // 2) + "Y" * (n - n // 2)
        beats = [left, right, left, right, left, right, left, right]

    else:
        beats = ["X" * n]

    return BeatSheet(
        metadata=BeatSheetMetadata(name=f"preset-{name}", title=PRESETS.get(name, {}).get("title", name)),
        timing=BeatSheetTiming(bpm=bpm, loop=True),
        on_end="status",
        palette=palette,
        node_order=node_order,
        beats=beats,
    )
