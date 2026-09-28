"""Trusted, in-process seed API contract; no network, database or credentials."""

CONTRACT = {"required": ["userId"], "properties": {"userId": "string", "displayName": "string"}}


def signup(payload: dict) -> int:
    if not isinstance(payload.get("userId"), str) or not payload["userId"].strip():
        return 422
    return 201
