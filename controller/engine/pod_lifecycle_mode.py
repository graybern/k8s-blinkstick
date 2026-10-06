import asyncio
import logging

from controller.config import POD_LIFECYCLE_POLL_INTERVAL, OVERLAY_POD_LIFECYCLE_ENABLED, POD_LIFECYCLE_NAMESPACES

log = logging.getLogger(__name__)


class PodLifecycleOverlay:
    name = "pod-lifecycle"

    def __init__(self, engine, k8s):
        self._engine = engine
        self._k8s = k8s
        self._poll_task: asyncio.Task | None = None
        self._known_pods: set[str] = set()
        self._initialized = False

    async def start(self):
        if not OVERLAY_POD_LIFECYCLE_ENABLED:
            log.info("Pod lifecycle overlay disabled")
            return
        if not self._k8s:
            log.warning("Pod lifecycle overlay enabled but no K8s client")
            return
        self._poll_task = asyncio.create_task(self._poll_loop())
        log.info("Pod lifecycle overlay started (poll every %ds)", POD_LIFECYCLE_POLL_INTERVAL)

    async def stop(self):
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        log.info("Pod lifecycle overlay stopped")

    async def _poll_loop(self):
        while True:
            try:
                pods = await self._k8s.list_pods()
                self._process_pods(pods)
                await asyncio.sleep(POD_LIFECYCLE_POLL_INTERVAL)
            except asyncio.CancelledError:
                return
            except Exception:
                log.exception("Pod lifecycle overlay poll failed")
                await asyncio.sleep(POD_LIFECYCLE_POLL_INTERVAL)

    def _process_pods(self, pods: list[dict]):
        current: set[str] = set()
        pod_meta: dict[str, tuple[str, str]] = {}
        for pod in pods:
            meta = pod.get("metadata", {})
            name = meta.get("name", "")
            ns = meta.get("namespace", "")
            if not name:
                continue
            if POD_LIFECYCLE_NAMESPACES and ns not in POD_LIFECYCLE_NAMESPACES:
                continue
            key = f"{ns}/{name}"
            current.add(key)
            pod_meta[key] = (ns, name)

        if not self._initialized:
            self._known_pods = current
            self._initialized = True
            return

        added = current - self._known_pods
        removed = self._known_pods - current

        for key in added:
            ns, name = pod_meta.get(key, (key, key))
            self._engine.trigger_overlay(
                "pod-lifecycle",
                reason=f"Created: {ns}/{name}",
                priority=1,
                duration=3.0,
                color=(0, 255, 100),
                effect="solid",
                params={},
            )

        for key in removed:
            self._engine.trigger_overlay(
                "pod-lifecycle",
                reason=f"Deleted: {key}",
                priority=1,
                duration=3.0,
                color=(255, 140, 0),
                effect="blink",
                params={"delay": 0.3},
            )

        self._known_pods = current
