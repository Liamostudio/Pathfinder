"""Client for the local SAM 3.1 worker (scripts/sam_worker.py). Standard library only; loopback addresses only."""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request

from .sam_masks import Detection

DEFAULT_URL = "http://127.0.0.1:8767"


class SamWorkerError(RuntimeError):
    """The worker is unreachable, refused the request or sent something unusable."""


class SamClient:
    def __init__(self, url: str = DEFAULT_URL, timeout_s: float = 30.0, opener=None):
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "http" or parts.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("the SAM worker must be a local http address (127.0.0.1)")
        self.url, self.timeout_s = url.rstrip("/"), timeout_s
        self._open = opener or urllib.request.urlopen

    def _call(self, path: str, body: dict | None = None) -> dict:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.url + path, data=data, headers={"Content-Type": "application/json"} if data else {})
        try:
            with self._open(request, timeout=self.timeout_s) as reply:
                return json.loads(reply.read(8_000_000))
        except urllib.error.HTTPError as exc:
            raise SamWorkerError(f"SAM worker refused the request ({exc.code})") from None
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise SamWorkerError(f"SAM worker not reachable: {getattr(exc, 'reason', exc)}") from None
        except (json.JSONDecodeError, ValueError):
            raise SamWorkerError("SAM worker sent a reply that is not JSON") from None

    def health(self) -> dict:
        return self._call("/health")

    def segment(self, jpeg: bytes, prompt: str, max_objects: int = 5) -> list[Detection]:
        reply = self._call("/segment", {"image_b64": base64.b64encode(jpeg).decode(), "prompt": prompt, "max_objects": max_objects})
        try:
            return [Detection(float(d["score"]), tuple(float(v) for v in d["box"]), tuple(float(v) for v in d["centroid"]),
                              tuple(float(v) for v in d["bottom"]), int(d["area"])) for d in reply["detections"]]
        except (KeyError, TypeError, ValueError):
            raise SamWorkerError("SAM worker sent detections in an unexpected shape") from None
