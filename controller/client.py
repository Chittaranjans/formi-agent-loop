"""Thin HTTP client for the simulator with retries and idempotent error handling."""
from __future__ import annotations

import json
import time
from typing import Any

import httpx


class SimClient:
    def __init__(self, base_url: str, timeout: float = 180.0):
        self.base_url = base_url.rstrip("/")
        self._c = httpx.Client(base_url=self.base_url, timeout=timeout)

    def close(self) -> None:
        self._c.close()

    def _req(self, method: str, path: str, **kw) -> httpx.Response:
        for attempt in range(6):
            try:
                r = self._c.request(method, path, **kw)
                if r.status_code >= 500 and attempt < 5:
                    time.sleep(0.25 * (2**attempt))
                    continue
                return r
            except (httpx.TransportError, httpx.TimeoutException):
                if attempt >= 5:
                    raise
                time.sleep(0.25 * (2**attempt))
        raise RuntimeError("unreachable")

    def get_json(self, path: str, **params) -> Any:
        r = self._req("GET", path, params=params or None)
        r.raise_for_status()
        return r.json()

    def post_json(self, path: str, body: dict | None = None) -> tuple[int, Any]:
        r = self._req("POST", path, json=body or {})
        try:
            data = r.json()
        except Exception:
            data = {"raw": r.text}
        return r.status_code, data

    def put_json(self, path: str, body: dict) -> tuple[int, Any]:
        r = self._req("PUT", path, json=body)
        try:
            data = r.json()
        except Exception:
            data = {"raw": r.text}
        return r.status_code, data

    def get_text(self, path: str) -> str:
        r = self._req("GET", path)
        r.raise_for_status()
        return r.text

    def health(self) -> dict:
        return self.get_json("/health")

    def state(self) -> dict:
        return self.get_json("/sim/state")

    def advance(self, minutes: int) -> dict:
        code, data = self.post_json("/sim/advance", {"minutes": minutes})
        if code == 409:
            data["finished"] = True
            return data
        if code >= 400:
            raise RuntimeError(f"advance failed {code}: {data}")
        return data

    def list_chunks(self, kind: str, after: int) -> list[dict]:
        return self.get_json("/events/chunks", kind=kind, after=after)

    def iter_chunk_events(self, chunk: int):
        text = self.get_text(f"/events/chunks/{chunk}")
        for line in text.splitlines():
            if line.strip():
                yield json.loads(line)

    def workflows(self) -> list[dict]:
        return self.get_json("/workflows")

    def workflow(self, wid: str) -> dict:
        return self.get_json(f"/workflows/{wid}")

    def config(self, wid: str) -> dict:
        return self.get_json(f"/workflows/{wid}/config")

    def config_history(self, wid: str) -> list[dict]:
        return self.get_json(f"/workflows/{wid}/config/history")

    def request_approval(self, body: dict) -> tuple[int, Any]:
        return self.post_json("/approvals", body)

    def get_approval(self, aid: str) -> dict:
        return self.get_json(f"/approvals/{aid}")

    def list_approvals(self, workflow_id: str | None = None, status: str | None = None) -> list[dict]:
        params = {}
        if workflow_id:
            params["workflow_id"] = workflow_id
        if status:
            params["status"] = status
        return self.get_json("/approvals", **params)

    def apply(self, wid: str, approval_id: str) -> tuple[int, Any]:
        return self.post_json(f"/workflows/{wid}/apply", {"approval_id": approval_id})

    def auto_apply(self, wid: str, body: dict) -> tuple[int, Any]:
        return self.put_json(f"/workflows/{wid}/config", body)

    def suggest_fixes(self, workflow_id: str, problem: str) -> tuple[int, Any]:
        return self.post_json("/llm/suggest-fixes", {"workflow_id": workflow_id, "problem": problem})

    def validate(self, workflow_id: str, prompt_version: str, n: int = 200) -> tuple[int, Any]:
        return self.post_json(
            "/validation/runs",
            {"workflow_id": workflow_id, "prompt_version": prompt_version, "n": n},
        )
