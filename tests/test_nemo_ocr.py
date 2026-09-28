from io import BytesIO
import json

import httpx
from PIL import Image
import pytest

from tracebridge.nemo_ocr import NemoRetrieverOCR
from tracebridge.project_sources import log_evidence


def image_bytes():
    raw = BytesIO()
    Image.new("RGB", (100, 100), "white").save(raw, format="PNG")
    return raw.getvalue()


def test_ocr_uses_documented_contract_and_redacts_extracted_text():
    def handler(request):
        payload = json.loads(request.content)
        assert payload["input"][0]["url"].startswith("data:image/jpeg;base64,")
        assert payload["merge_levels"] == ["paragraph"]
        assert request.headers["Authorization"] == "Bearer unused-test-key"
        return httpx.Response(200, json={"model": "nvidia/nemotron-ocr-v2", "data": [{"text_detections": [
            {"text_prediction": {"text": "ROOM_FULL requestId=abc12345 email=user@example.invalid token=hidden", "confidence": 0.99}},
            {"text_prediction": {"text": "low confidence guess", "confidence": 0.2}},
        ]}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = NemoRetrieverOCR("unused-test-key", client=client).extract(image_bytes())
    assert "ROOM_FULL" in result["usable_text"] and "abc12345" in result["usable_text"]
    assert "low confidence" not in result["usable_text"]
    assert "user@example.invalid" not in str(result) and "token=hidden" not in str(result)
    assert "base64" not in str(result)


def test_self_hosted_ocr_does_not_receive_nvidia_credentials(monkeypatch):
    monkeypatch.delenv("TRACEBRIDGE_OCR_API_KEY", raising=False)

    def handler(request):
        assert "Authorization" not in request.headers
        return httpx.Response(200, json={"data": [{"text_detections": []}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = NemoRetrieverOCR("private-nvidia-key", endpoint="http://localhost:8000/v1/ocr", client=client).extract(image_bytes())
    assert result["usable_text"] == ""


def test_service_error_does_not_echo_response_payload_or_credentials():
    def handler(request):
        return httpx.Response(422, json={"echo": "private-nvidia-key base64-private-image"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="HTTP 422") as error:
            NemoRetrieverOCR("private-nvidia-key", client=client).extract(image_bytes())
    assert "private-nvidia-key" not in str(error.value)


def test_request_ids_do_not_correlate_prefixes_or_multiple_submitted_ids():
    evidence, _ = log_evidence("ERROR requestId=abc123456 ROOM_FULL", "requestId=abc12345 ROOM_FULL")
    assert evidence == []
    evidence, _ = log_evidence("ERROR requestId=abc12345 ROOM_FULL", "requestId=abc12345 or requestId=other ROOM_FULL")
    assert evidence and not any(item.correlated for item in evidence)


def test_ocr_skips_malformed_predictions_without_losing_valid_text():
    def handler(request):
        return httpx.Response(200, json={"data": [{"text_detections": [
            {"text_prediction": None},
            {"text_prediction": ["wrong shape"]},
            {"text_prediction": {"text": "ROOM_FULL", "confidence": 0.99}},
        ]}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = NemoRetrieverOCR("test-key", client=client).extract(image_bytes())
    assert result["usable_text"] == "ROOM_FULL"


def test_invalid_confidence_cannot_supply_a_request_identifier():
    def handler(request):
        return httpx.Response(200, json={"data": [{"text_detections": [
            {"text_prediction": {"text": "requestId=invented", "confidence": True}},
            {"text_prediction": {"text": "requestId=overflow", "confidence": 10 ** 400}},
            {"text_prediction": {"text": "valid symptom", "confidence": 0.9}},
        ]}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = NemoRetrieverOCR("test-key", client=client).extract(image_bytes())
    assert result["usable_text"] == "valid symptom"
