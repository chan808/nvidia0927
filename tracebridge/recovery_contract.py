"""Owner-registered, bounded checks against an explicitly read-only local API."""
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Literal

from .project_profile import _health_url


class RecoveryCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    url: str = Field(max_length=500)
    method: Literal["GET", "POST"] = "GET"
    read_only: bool
    json_body: dict = Field(default_factory=dict, max_length=16)
    expected_status: int = Field(default=200, ge=200, le=299)
    expected_json: dict = Field(min_length=1, max_length=16)
    snapshot_header: str = Field(default="X-TraceBridge-Snapshot-SHA256", pattern=r"^[A-Za-z0-9-]{1,80}$")

    @model_validator(mode="after")
    def validate_request(self):
        _health_url(self.url)
        if self.read_only is not True:
            raise ValueError("Owner confirmation of a read-only request required")
        if self.method == "GET" and self.json_body:
            raise ValueError("GET checks cannot carry a body")
        for mapping in (self.json_body, self.expected_json):
            for key, value in mapping.items():
                if not isinstance(key, str) or not key or len(key) > 100:
                    raise ValueError("Bounded JSON field names required")
                if value is not None and type(value) not in (str, bool, int):
                    raise ValueError("Only primitive JSON expectations and inputs are supported")
                if isinstance(value, str) and len(value) > 200:
                    raise ValueError("JSON value is too long")
        return self

    @property
    def path(self):
        return urlparse(self.url).path or "/"


class RecoverySpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    checks: list[RecoveryCheck] = Field(min_length=2, max_length=4)
    journey_check_id: str = Field(default="journey", pattern=r"^[A-Za-z0-9_-]{1,64}$")
    regression_check_id: str = Field(default="regression", pattern=r"^[A-Za-z0-9_-]{1,64}$")
    samples: int = Field(default=2, ge=2, le=6)
    interval_seconds: int = Field(default=2, ge=1, le=10)
    timeout_seconds: int = Field(default=2, ge=1, le=5)

    @model_validator(mode="after")
    def distinct_checks(self):
        if len({check.id for check in self.checks}) != len(self.checks):
            raise ValueError("Unique recovery check IDs required")
        ids = {check.id for check in self.checks}
        if self.journey_check_id == self.regression_check_id or {self.journey_check_id, self.regression_check_id} - ids:
            raise ValueError("Separate registered journey and regression checks required")
        return self


def recovery_status(checks):
    if any(item["status"] == "FAILED" for item in checks):
        return "FAILED"
    if not checks or any(item["status"] == "INCONCLUSIVE" for item in checks):
        return "INCONCLUSIVE"
    return "PASSED"
