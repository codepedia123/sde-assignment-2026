from __future__ import annotations

import time
from typing import Any, Iterator

import httpx


class SimulatorError(RuntimeError):
    def __init__(self, status: int, payload: Any):
        self.status = status
        self.payload = payload
        super().__init__(f"simulator HTTP {status}: {payload}")


class SimulatorClient:
    """Small resilient client for the documented simulator contract."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.http = httpx.Client(base_url=self.base_url, timeout=httpx.Timeout(120, connect=10))

    def close(self) -> None:
        self.http.close()

    def _request(self, method: str, path: str, *, attempts: int = 4, **kwargs) -> httpx.Response:
        last: BaseException | None = None
        for attempt in range(attempts):
            try:
                response = self.http.request(method, path, **kwargs)
                if response.status_code >= 500 and attempt + 1 < attempts:
                    time.sleep(0.2 * (2**attempt))
                    continue
                return response
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last = exc
                if attempt + 1 == attempts:
                    raise
                time.sleep(0.2 * (2**attempt))
        raise RuntimeError("request failed") from last

    @staticmethod
    def _json(response: httpx.Response, expected: tuple[int, ...] = (200,)) -> Any:
        try:
            payload = response.json()
        except ValueError:
            payload = response.text
        if response.status_code not in expected:
            raise SimulatorError(response.status_code, payload)
        return payload

    def health(self) -> dict[str, Any]:
        return self._json(self._request("GET", "/health"))

    def state(self) -> dict[str, Any]:
        return self._json(self._request("GET", "/sim/state"))

    def workflows(self) -> list[dict[str, Any]]:
        return self._json(self._request("GET", "/workflows"))

    def config(self, workflow_id: str) -> dict[str, Any]:
        return self._json(self._request("GET", f"/workflows/{workflow_id}/config"))

    def config_history(self, workflow_id: str) -> list[dict[str, Any]]:
        return self._json(self._request("GET", f"/workflows/{workflow_id}/config/history"))

    def chunks(self, kind: str, after: int = -1) -> list[dict[str, Any]]:
        return self._json(self._request("GET", "/events/chunks", params={"kind": kind, "after": after}))

    def chunk_events(self, chunk: int) -> Iterator[dict[str, Any]]:
        import json

        with self.http.stream("GET", f"/events/chunks/{chunk}") as response:
            if response.status_code != 200:
                raise SimulatorError(response.status_code, response.text)
            for line in response.iter_lines():
                if line:
                    yield json.loads(line)

    def advance(self, minutes: int, before_minute: int) -> dict[str, Any]:
        """Advance once without blindly retrying the non-idempotent operation.

        If the response is lost, the state endpoint tells us whether the server
        already committed the advance.  New chunks are discovered separately.
        """
        try:
            response = self.http.post("/sim/advance", json={"minutes": minutes})
        except (httpx.TimeoutException, httpx.NetworkError):
            state = self.state()
            if int(state["sim_minute"]) >= before_minute + minutes or state.get("finished"):
                return state
            raise
        return self._json(response)

    def approvals(self, workflow_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        params = {k: v for k, v in {"workflow_id": workflow_id, "status": status}.items() if v is not None}
        return self._json(self._request("GET", "/approvals", params=params))

    def approval(self, approval_id: str) -> dict[str, Any]:
        return self._json(self._request("GET", f"/approvals/{approval_id}"))

    def request_approval(self, body: dict[str, Any]) -> dict[str, Any]:
        response = self._request("POST", "/approvals", json=body, attempts=1)
        return self._json(response)

    def apply_approval(self, workflow_id: str, approval_id: str) -> dict[str, Any]:
        response = self._request(
            "POST", f"/workflows/{workflow_id}/apply", json={"approval_id": approval_id}, attempts=1
        )
        return self._json(response)

    def auto_apply(self, workflow_id: str, body: dict[str, Any]) -> dict[str, Any]:
        response = self._request("PUT", f"/workflows/{workflow_id}/config", json=body, attempts=1)
        return self._json(response)

    def suggest_fixes(self, workflow_id: str, problem: str) -> dict[str, Any]:
        return self._json(
            self._request(
                "POST", "/llm/suggest-fixes", json={"workflow_id": workflow_id, "problem": problem}, attempts=1
            )
        )

    def validate_prompt(self, workflow_id: str, prompt_version: str, n: int) -> dict[str, Any]:
        return self._json(
            self._request(
                "POST", "/validation/runs",
                json={"workflow_id": workflow_id, "prompt_version": prompt_version, "n": n}, attempts=1,
            )
        )

