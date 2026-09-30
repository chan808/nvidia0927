import json
from zipfile import ZipFile

from scripts.package_submission import create_package


def test_submission_contains_working_pages_inputs_and_fixture_manifests():
    with ZipFile(create_package("verification")) as archive:
        names = archive.namelist()
        manifest = json.loads(archive.read("DRAFT_PACKAGE_MANIFEST.json"))
    for expected in (
        "pages/2_Report_Agent.py", "docs/archive/plans/competition-mvp.md", "examples/report_events.json",
        "examples/agolive_error.log", "tests/fixtures/agolive_repo/backend/build.gradle.kts",
        "tests/fixtures/agolive_repo/realtime/handler/ws.go", "tests/fixtures/agolive_repo/realtime/go.mod",
        "tracebridge/nemo_ocr.py", "README.md", "docs/README.md",
        "docs/guides/getting-started.md", "docs/guides/usage.md",
        "docs/validation/main-integration.md", "docs/competition/submission-draft.md",
        "docs/validation/assets/local-gui/applied-reopened.png",
        "docs/validation/assets/local-gui/reconnected.png",
        "tracebridge/public_service.py", "tracebridge/public_ui.py", "tracebridge/service_workflow.py",
        "tracebridge/storage/alembic/versions/20260929_04_public_service.py",
        "docs/guides/basic-service-rollout.md", "docs/validation/basic-service.md",
        "docs/validation/basic-service.json", "docs/validation/assets/basic-service/public-flow.png",
        "tracebridge/project_connection_ui.py", "tests/test_project_connection.py", "docs/guides/project-connection.md",
        "docs/validation/project-connection.md", "docs/validation/project-connection.json",
        "docs/validation/assets/project-connection/path-settings.png",
        "docs/validation/assets/project-connection/connection-diagnosis.png",
        "pages/3_Remote_Projects.py", "tracebridge/project_setup_ui.py", "tracebridge/report_photo.py",
        "tests/test_simple_project_flow.py", "docs/guides/simple-project.md",
        "docs/validation/simple-project.md", "docs/validation/simple-project.json",
        "docs/validation/assets/project-connection/simple-dashboard.png",
        "tracebridge/knowledge_review_ui.py", "scripts/evaluate_response_quality.py",
        "tests/test_knowledge_review.py", "docs/guides/knowledge-review.md",
        "docs/validation/knowledge-review.md", "docs/validation/knowledge-review.json",
        "deploy/Dockerfile", "deploy/Caddyfile.control-plane", ".github/workflows/regression.yml",
    ):
        assert expected in names
    assert "docs/validation/basic-service.md" in manifest["verification_documents"]
    assert "docs/validation/project-connection.md" in manifest["verification_documents"]
    assert "docs/validation/simple-project.md" in manifest["verification_documents"]
    assert "docs/validation/knowledge-review.md" in manifest["verification_documents"]
    assert not manifest["source_changed_during_packaging"]
    assert ".env" not in names
    assert not any(name.startswith((".codex-remote-attachments/", "output/", ".venv/")) for name in names)
