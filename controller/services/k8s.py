import logging
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

SA_TOKEN_PATH = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
SA_CA_PATH = Path("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")
K8S_API_URL = "https://kubernetes.default.svc"


class K8sClient:
    def __init__(self):
        self._client: httpx.AsyncClient | None = None
        self._token: str | None = None

    def _read_token(self) -> str | None:
        try:
            return SA_TOKEN_PATH.read_text().strip()
        except FileNotFoundError:
            log.warning("SA token not found at %s (not running in cluster?)", SA_TOKEN_PATH)
            return None

    def _get_client(self) -> httpx.AsyncClient | None:
        if self._client:
            return self._client
        self._token = self._read_token()
        if not self._token:
            return None
        verify = str(SA_CA_PATH) if SA_CA_PATH.exists() else False
        self._client = httpx.AsyncClient(
            base_url=K8S_API_URL,
            headers={"Authorization": f"Bearer {self._token}"},
            verify=verify,
            timeout=10,
        )
        return self._client

    async def list_configmaps(self, namespace: str, label_selector: str = "") -> list[dict]:
        client = self._get_client()
        if not client:
            return []
        try:
            params = {}
            if label_selector:
                params["labelSelector"] = label_selector
            resp = await client.get(
                f"/api/v1/namespaces/{namespace}/configmaps",
                params=params,
            )
            resp.raise_for_status()
            return resp.json().get("items", [])
        except Exception:
            log.exception("Failed to list ConfigMaps in %s", namespace)
            return []

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None
