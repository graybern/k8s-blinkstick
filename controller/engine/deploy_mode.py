import asyncio
import logging

from controller.config import ARGOCD_POLL_INTERVAL, OVERLAY_DEPLOY_ENABLED

log = logging.getLogger(__name__)


class DeployOverlay:
    name = "deploy-wave"

    def __init__(self, engine, k8s):
        self._engine = engine
        self._k8s = k8s
        self._poll_task: asyncio.Task | None = None
        self._last_sync_status: dict[str, str] = {}
        self._initialized = False

    async def start(self):
        if not OVERLAY_DEPLOY_ENABLED:
            log.info("Deploy overlay disabled")
            return
        if not self._k8s:
            log.warning("Deploy overlay enabled but no K8s client")
            return
        self._poll_task = asyncio.create_task(self._poll_loop())
        log.info("Deploy overlay started (poll every %ds)", ARGOCD_POLL_INTERVAL)

    async def stop(self):
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        log.info("Deploy overlay stopped")

    async def _poll_loop(self):
        while True:
            try:
                apps = await self._k8s.list_applications()
                self._process_apps(apps)
                await asyncio.sleep(ARGOCD_POLL_INTERVAL)
            except asyncio.CancelledError:
                return
            except Exception:
                log.exception("Deploy overlay poll failed")
                await asyncio.sleep(ARGOCD_POLL_INTERVAL)

    def _process_apps(self, apps: list[dict]):
        current: dict[str, str] = {}
        for app in apps:
            name = app.get("metadata", {}).get("name", "")
            if not name:
                continue
            sync_status = app.get("status", {}).get("sync", {}).get("status", "")
            health_status = app.get("status", {}).get("health", {}).get("status", "")
            current[name] = sync_status

            if not self._initialized:
                continue

            prev = self._last_sync_status.get(name)
            if sync_status == "Synced" and health_status == "Healthy" and prev != "Synced":
                self._engine.trigger_overlay(
                    "deploy-wave",
                    reason=f"Synced: {name}",
                    priority=2,
                    duration=5.0,
                    color=(0, 100, 255),
                    effect="solid",
                    params={},
                )

        self._last_sync_status = current
        self._initialized = True
