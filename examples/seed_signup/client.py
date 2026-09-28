"""SEEDED_DEVELOPMENT: a deliberately broken caller, never Agolive code."""


def build_signup_request(user_id: str, display_name: str) -> dict[str, str]:
    return {"user_id": user_id, "displayName": display_name}
