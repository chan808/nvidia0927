"""Synthetic incident evidence. No production data is used in this demo."""

from __future__ import annotations

from copy import deepcopy


CASES: dict[str, dict] = {
    "contract-001": {
        "title": "회원가입 요청 필드 불일치",
        "claim": "회원가입 API에서 500이 납니다",
        "reported_status": 500,
        "trace": {
            "trace_id": "contract-001",
            "environment": "dev",
            "service": "account-api",
            "operation": "회원가입",
            "method": "POST",
            "path": "/api/users",
            "request": {"user_id": "demo-42", "name": "Demo User"},
            "response_status": 422,
            "response": {"error": "userId is required"},
        },
        "contract": {"required": ["userId", "name"], "properties": {"userId": "string", "name": "string"}},
        "dto": {"userId": "str", "name": "str"},
        "logs": ["ValidationError: userId is required"],
        "migration": {"applied": "V12", "expected": "V12", "related_file": "V12__add_phone.sql"},
        "root_cause": "contract_mismatch",
    },
    "migration-002": {
        "title": "DB 마이그레이션 미적용",
        "claim": "회원가입 API에서 500이 납니다",
        "reported_status": 500,
        "trace": {
            "trace_id": "migration-002",
            "environment": "dev",
            "service": "account-api",
            "operation": "회원가입",
            "method": "POST",
            "path": "/api/users",
            "request": {"userId": "demo-43", "name": "Demo User", "phone": "010-0000-0000"},
            "response_status": 500,
            "response": {"error": "Internal Server Error"},
        },
        "contract": {"required": ["userId", "name"], "properties": {"userId": "string", "name": "string", "phone": "string"}},
        "dto": {"userId": "str", "name": "str", "phone": "str"},
        "logs": ["sqlite3.OperationalError: table users has no column named phone"],
        "migration": {"applied": "V11", "expected": "V12", "related_file": "V12__add_phone.sql"},
        "root_cause": "migration_missing",
    },
    "claim-003": {
        "title": "500 제보와 실제 응답 불일치",
        "claim": "회원가입 API에서 500이 납니다",
        "reported_status": 500,
        "trace": {
            "trace_id": "claim-003",
            "environment": "dev",
            "service": "account-api",
            "operation": "회원가입",
            "method": "POST",
            "path": "/api/users",
            "request": {"userId": "demo-44"},
            "response_status": 422,
            "response": {"error": "name is required"},
        },
        "contract": {"required": ["userId", "name"], "properties": {"userId": "string", "name": "string"}},
        "dto": {"userId": "str", "name": "str"},
        "logs": ["Request rejected: missing required field name"],
        "migration": {"applied": "V12", "expected": "V12", "related_file": "V12__add_phone.sql"},
        "root_cause": "expected_validation",
    },
}


def list_cases() -> list[tuple[str, str]]:
    return [(key, case["title"]) for key, case in CASES.items()]


def get_case(trace_id: str) -> dict | None:
    case = CASES.get(trace_id)
    return deepcopy(case) if case else None
