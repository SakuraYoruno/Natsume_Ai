"""Network-visible deterministic candidate used by the NE-05 Docker Lab."""

from __future__ import annotations

from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from typing import Any


VERSION = str(os.environ.get("NE05_LAB_VERSION") or "unversioned").strip()


def _canonical_digest(payload: Any) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(raw).hexdigest()


class Handler(BaseHTTPRequestHandler):
    server_version = "NatsumeNE05Lab/1"

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/health":
            self._send(404, {"status": "NOT_FOUND"})
            return
        self._send(
            200,
            {
                "schema_version": "natsume.ne05.skill-lab.health.v1",
                "status": "READY",
                "candidate_version": VERSION,
                "pid": os.getpid(),
            },
        )

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/canary":
            self._send(404, {"status": "NOT_FOUND"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise TypeError("payload must be an object")
            challenge = str(payload.get("challenge") or "").strip()
            if not challenge:
                raise ValueError("challenge is required")
        except Exception as exc:
            self._send(
                400,
                {"status": "REJECTED", "reason": f"{type(exc).__name__}: {exc}"},
            )
            return
        result = {
            "schema_version": "natsume.ne05.skill-lab.canary.v1",
            "status": "PASS",
            "candidate_version": VERSION,
            "challenge_sha256": sha256(challenge.encode("utf-8")).hexdigest(),
            "input_digest": _canonical_digest(payload),
            "candidate_result": sha256(
                f"{VERSION}\n{challenge}".encode("utf-8")
            ).hexdigest(),
            "external_effects": 0,
        }
        self._send(200, result)

    def log_message(self, _format: str, *_args: Any) -> None:
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
