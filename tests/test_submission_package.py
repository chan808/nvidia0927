from zipfile import ZipFile

from scripts.package_submission import create_package


def test_submission_contains_working_pages_inputs_and_fixture_manifests():
    with ZipFile(create_package("verification")) as archive:
        names = archive.namelist()
    for expected in (
        "pages/2_Report_Agent.py", "docs/archive/plans/competition-mvp.md", "examples/report_events.json",
        "examples/agolive_error.log", "tests/fixtures/agolive_repo/backend/build.gradle.kts",
        "tests/fixtures/agolive_repo/realtime/handler/ws.go", "tests/fixtures/agolive_repo/realtime/go.mod",
        "tracebridge/nemo_ocr.py", "README.md", "docs/README.md",
        "docs/guides/getting-started.md", "docs/guides/usage.md",
        "docs/validation/main-integration.md", "docs/competition/submission-draft.md",
        "docs/validation/assets/local-gui/applied-reopened.png",
        "docs/validation/assets/local-gui/reconnected.png",
    ):
        assert expected in names
    assert ".env" not in names
    assert not any(name.startswith((".codex-remote-attachments/", "output/", ".venv/")) for name in names)
