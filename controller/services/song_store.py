import asyncio
import logging

import yaml

from controller.api.models import BeatSheet
from controller.config import K8S_NAMESPACE, SONG_POLL_INTERVAL
from controller.services.k8s import K8sClient

log = logging.getLogger(__name__)

SONG_LABEL = "blinkstick.octolet.int/type"
SOURCE_LABEL = "blinkstick.octolet.int/source"
SONG_LABEL_SELECTOR = f"{SONG_LABEL}=song"
CM_PREFIX = "blinkstick-song-"
DATA_KEY = "beat-sheet.yaml"


class SongStore:
    def __init__(self, k8s: K8sClient):
        self._k8s = k8s
        self._songs: dict[str, BeatSheet] = {}
        self._sources: dict[str, str] = {}
        self._raw_yaml: dict[str, str] = {}
        self._poll_task: asyncio.Task | None = None

    async def start(self):
        await self._refresh()
        self._poll_task = asyncio.create_task(self._poll_loop())
        log.info("Song store started (%d songs loaded)", len(self._songs))

    async def stop(self):
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass

    def list_songs(self) -> list[dict]:
        result = []
        for name, sheet in sorted(self._songs.items()):
            result.append({
                "name": name,
                "title": sheet.metadata.title,
                "author": sheet.metadata.author,
                "bpm": sheet.timing.bpm,
                "loop": sheet.timing.loop,
                "source": self._sources.get(name, "unknown"),
            })
        return result

    def get_song(self, name: str) -> BeatSheet | None:
        return self._songs.get(name)

    def get_source(self, name: str) -> str | None:
        return self._sources.get(name)

    async def save_song(self, sheet: BeatSheet) -> bool:
        name = sheet.metadata.name
        yaml_str = yaml.dump(sheet.model_dump(), default_flow_style=False)
        cm_name = f"{CM_PREFIX}{name}"
        labels = {SONG_LABEL: "song", SOURCE_LABEL: "runtime"}
        data = {DATA_KEY: yaml_str}

        existing = await self._k8s.get_configmap(K8S_NAMESPACE, cm_name)
        if existing:
            result = await self._k8s.update_configmap(K8S_NAMESPACE, cm_name, data, labels)
        else:
            result = await self._k8s.create_configmap(K8S_NAMESPACE, cm_name, data, labels)

        if result:
            self._songs[name] = sheet
            self._sources[name] = "runtime"
            self._raw_yaml[name] = yaml_str
            log.info("Saved song: %s", name)
            return True
        return False

    async def delete_song(self, name: str) -> bool:
        cm_name = f"{CM_PREFIX}{name}"
        ok = await self._k8s.delete_configmap(K8S_NAMESPACE, cm_name)
        if ok:
            self._songs.pop(name, None)
            self._sources.pop(name, None)
            self._raw_yaml.pop(name, None)
            log.info("Deleted song: %s", name)
        return ok

    def export_yaml(self, name: str) -> str | None:
        if name in self._raw_yaml:
            return self._raw_yaml[name]
        sheet = self._songs.get(name)
        if sheet:
            return yaml.dump(sheet.model_dump(), default_flow_style=False)
        return None

    async def _refresh(self):
        cms = await self._k8s.list_configmaps(K8S_NAMESPACE, SONG_LABEL_SELECTOR)
        songs = {}
        sources = {}
        raw = {}
        for cm in cms:
            try:
                yaml_str = cm.get("data", {}).get(DATA_KEY, "")
                if not yaml_str:
                    continue
                parsed = yaml.safe_load(yaml_str)
                sheet = BeatSheet(**parsed)
                name = sheet.metadata.name
                songs[name] = sheet
                sources[name] = cm.get("metadata", {}).get("labels", {}).get(SOURCE_LABEL, "unknown")
                raw[name] = yaml_str
            except Exception:
                cm_name = cm.get("metadata", {}).get("name", "?")
                log.exception("Failed to parse song from ConfigMap %s", cm_name)
        self._songs = songs
        self._sources = sources
        self._raw_yaml = raw

    async def _poll_loop(self):
        while True:
            try:
                await asyncio.sleep(SONG_POLL_INTERVAL)
                await self._refresh()
            except asyncio.CancelledError:
                return
            except Exception:
                log.exception("Song store poll failed")
