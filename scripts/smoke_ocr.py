"""Verify the actual NeMo Retriever OCR call using a synthetic error screenshot."""

from io import BytesIO
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from dotenv import dotenv_values, load_dotenv
from PIL import Image, ImageDraw, ImageFont

from tracebridge.nemo_ocr import NemoRetrieverOCR


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")
    key = os.getenv("NVIDIA_API_KEY") or dotenv_values(root / ".env").get("NVIDIA_API_KEY")
    if not key:
        raise SystemExit("NVIDIA_API_KEY is not configured")
    image = Image.new("RGB", (900, 300), "white")
    draw = ImageDraw.Draw(image)
    draw.text((30, 30), "Room entry failed\nROOM_FULL\nrequestId=abc12345", fill="black", font=ImageFont.load_default(size=36))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    destination = root / "output" / "validation"
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "room_error.png").write_bytes(buffer.getvalue())
    loading = Image.new("RGB", (900, 600), "white")
    canvas = ImageDraw.Draw(loading)
    canvas.rectangle((0, 0, 899, 50), fill="#eef1f7")
    canvas.rectangle((0, 0, 899, 599), outline="#9ca3af", width=2)
    canvas.arc((410, 260, 490, 340), 20, 280, fill="#2563eb", width=8)
    loading.save(destination / "loading_screen.png")
    result = NemoRetrieverOCR(key).extract(buffer.getvalue())
    result["synthetic_input"] = True
    result["executed_at"] = datetime.now(timezone.utc).isoformat()
    result["expected_error_code_found"] = "ROOM_FULL" in result["usable_text"]
    result["expected_request_id_found"] = "abc12345" in result["usable_text"]
    (destination / "nemo_ocr.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("service", "model", "status", "elapsed_ms", "expected_error_code_found", "expected_request_id_found")}, ensure_ascii=False))
    if not result["expected_error_code_found"] or not result["expected_request_id_found"]:
        raise SystemExit("OCR returned a response but did not read the required synthetic error identifiers")


if __name__ == "__main__":
    main()
