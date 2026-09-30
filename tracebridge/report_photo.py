"""Bounded private screenshot transport; images never become server observations."""
import base64

from .nemo_ocr import MAX_BASE64_LENGTH, prepare_image


def encode_report_image(raw: bytes) -> str:
    url, _ = prepare_image(raw)
    return url.split(",", 1)[1]


def decode_report_image(encoded: str | None) -> bytes | None:
    if encoded is None:
        return None
    if not isinstance(encoded, str) or not encoded or len(encoded) >= MAX_BASE64_LENGTH:
        raise ValueError("사진의 전송 크기를 확인해 주세요")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("사진 파일 형식을 확인해 주세요") from exc
    prepare_image(raw)  # Same byte, pixel and file-type guards as local intake.
    return raw
