"""Controlled synthetic caller defect. This module is read, never executed by sources."""


def build_payload(form: dict) -> dict:
    return {"account_id": form["accountId"], "name": form["name"]}
