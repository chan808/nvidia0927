"""Tiny disposable service and DB used only to prove the two demo failures."""

from __future__ import annotations

import os
import sqlite3


def frontend_signup_payload() -> dict[str, str]:
    key = "userId" if os.getenv("TRACEBRIDGE_CANDIDATE_FIX") == "1" else "user_id"
    return {key: "demo-42", "name": "Demo User"}


def make_connection(apply_v12: bool) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT NOT NULL)")
    if apply_v12:
        connection.execute("ALTER TABLE users ADD COLUMN phone TEXT")
    return connection


def signup(payload: dict[str, str], connection: sqlite3.Connection) -> int:
    if not payload.get("userId") or not payload.get("name"):
        return 422
    try:
        if payload.get("phone"):
            connection.execute(
                "INSERT INTO users (id, name, phone) VALUES (?, ?, ?)",
                (payload["userId"], payload["name"], payload["phone"]),
            )
        else:
            connection.execute(
                "INSERT INTO users (id, name) VALUES (?, ?)",
                (payload["userId"], payload["name"]),
            )
        connection.commit()
    except sqlite3.OperationalError:
        return 500
    return 201
