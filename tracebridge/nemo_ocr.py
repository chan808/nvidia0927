"""Bounded screenshot text extraction through the NeMo Retriever OCR microservice."""

from __future__ import annotations

import base64
import hashlib
from io import BytesIO
import math
import os
import time
from urllib.parse import urlparse
import warnings

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

from .project_sources import redact
from .deadline import DeadlineExceeded, check_deadline, remaining_timeout


OCR_ENDPOINT = "https://ai.api.nvidia.com/v1/cv/nvidia/nemotron-ocr-v2"
OCR_MODEL = "nvidia/nemotron-ocr-v2"
MAX_IMAGE_BYTES = 8_000_000
MAX_IMAGE_PIXELS = 20_000_000
MAX_BASE64_LENGTH = 180_000


def prepare_image(raw: bytes) -> tuple[str, str]:
    """Verify, remove metadata and bound the inline image before any network call."""
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("사진은 8 MB 이하의 PNG 또는 JPEG여야 합니다")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as original:
                if original.format not in {"PNG", "JPEG"} or getattr(original, "n_frames", 1) != 1:
                    raise ValueError("PNG 또는 JPEG 사진 한 장을 첨부해 주세요")
                if original.width * original.height > MAX_IMAGE_PIXELS:
                    raise ValueError("사진은 2천만 픽셀 이하여야 합니다")
                image = ImageOps.exif_transpose(original).convert("RGBA")
                background = Image.new("RGBA", image.size, "white")
                background.alpha_composite(image)
                image = background.convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("사진 파일을 읽을 수 없습니다. PNG 또는 JPEG로 다시 첨부해 주세요") from exc
    image.thumbnail((1600, 1600))
    for size in (1600, 1280, 1024, 800):
        image.thumbnail((size, size))
        encoded = BytesIO()
        image.save(encoded, format="JPEG", quality=85, optimize=True)
        payload = base64.b64encode(encoded.getvalue()).decode("ascii")
        if len(payload) < MAX_BASE64_LENGTH:
            return "data:image/jpeg;base64," + payload, hashlib.sha256(raw).hexdigest()
    raise ValueError("사진이 너무 복잡해 전송 크기를 제한하지 못했습니다. 오류 부분을 잘라 첨부해 주세요")


class NemoRetrieverOCR:
    def __init__(self, api_key: str | None, *, endpoint: str | None = None, client: httpx.Client | None = None):
        self.endpoint = endpoint or os.getenv("TRACEBRIDGE_OCR_URL") or OCR_ENDPOINT
        parsed = urlparse(self.endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("OCR endpoint must be an HTTP(S) URL without credentials")
        if parsed.hostname == "ai.api.nvidia.com" and parsed.scheme != "https":
            raise ValueError("Hosted NVIDIA OCR requires HTTPS")
        self.api_key = api_key if parsed.hostname == "ai.api.nvidia.com" else os.getenv("TRACEBRIDGE_OCR_API_KEY")
        self.client = client

    def extract(self, raw: bytes, *, deadline: float | None = None) -> dict:
        check_deadline(deadline)
        data_url, image_hash = prepare_image(raw)
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {"input": [{"type": "image_url", "url": data_url}], "merge_levels": ["paragraph"]}
        started = time.monotonic()
        timeout = remaining_timeout(deadline, 35.0)
        try:
            post = self.client.post if self.client else httpx.post
            response = post(self.endpoint, headers=headers, json=payload, timeout=timeout)
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException:
            raise DeadlineExceeded("ocr_http") from None
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(f"사진 문자 추출 서비스 오류 (HTTP {exc.response.status_code})") from None
        except (httpx.HTTPError, ValueError):
            raise RuntimeError("사진 문자 추출 서비스에 연결하지 못했습니다") from None
        if not isinstance(data, dict) or not isinstance(data.get("data"), list):
            raise RuntimeError("사진 문자 추출 결과 형식을 확인할 수 없습니다")
        detections = []
        for page in data["data"][:1]:
            if not isinstance(page, dict) or not isinstance(page.get("text_detections"), list):
                raise RuntimeError("사진 문자 추출 결과 형식을 확인할 수 없습니다")
            for item in page["text_detections"][:100]:
                prediction = item.get("text_prediction", {}) if isinstance(item, dict) else {}
                if not isinstance(prediction, dict):
                    continue
                text, confidence = prediction.get("text"), prediction.get("confidence")
                if not isinstance(text, str) or type(confidence) not in {int, float}:
                    continue
                if not 0 <= confidence <= 1 or not math.isfinite(confidence):
                    continue
                box = item.get("bounding_box")
                points = box.get("points", []) if isinstance(box, dict) else []
                points = points if isinstance(points, list) else []
                x = min((p["x"] for p in points if isinstance(p, dict) and isinstance(p.get("x"), (int, float))), default=0)
                y = min((p["y"] for p in points if isinstance(p, dict) and isinstance(p.get("y"), (int, float))), default=len(detections))
                detections.append((y, x, redact(text[:1000]), float(confidence)))
        detections.sort(key=lambda item: (item[0], item[1]))
        lines = [
            {"id": f"I{index}", "text": text, "confidence": confidence}
            for index, (_, _, text, confidence) in enumerate(detections[:40], 1)
        ]
        return {
            "service": "NeMo Retriever OCR NIM",
            "model": data.get("model", OCR_MODEL),
            "endpoint": self.endpoint,
            "status": "success",
            "image_sha256": image_hash,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "timeout_seconds": timeout,
            "request_id": response.headers.get("x-request-id"),
            "lines": lines,
            "usable_text": "\n".join(line["text"] for line in lines if line["confidence"] >= 0.75)[:6000],
        }
