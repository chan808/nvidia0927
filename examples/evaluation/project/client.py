"""Controlled caller defects. These inputs and mappings are entirely synthetic."""


def serialize_registration(values):
    # Controlled omission despite an account_input being captured at the UI.
    return {"displayName": values["name_input"]} if "name_input" in values else {}


def serialize_user(values):
    return {"user_id": values["account_input"], "displayName": values["name_input"]}


def serialize_account(values):
    return {"account_id": values["account_input"], "displayName": values["name_input"]}


def serialize_correct(values):
    return {key: values[source] for key, source in {"userId": "account_input", "displayName": "name_input"}.items()
            if source in values}
