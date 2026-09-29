"""Owner assessments, priority scheduling and durable, payload-free job events."""
from __future__ import annotations

import json
import sqlite3
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .project_sources import redact


class WorkAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    priority: Literal["P0", "P1", "P2", "P3"] = "P2"
    difficulty: Literal["UNKNOWN", "SMALL", "MEDIUM", "LARGE"] = "UNKNOWN"
    risk: Literal["UNKNOWN", "LOW", "MEDIUM", "HIGH"] = "UNKNOWN"
    reason: str = Field(default="영향·난이도·변경 위험을 아직 평가하지 않았습니다.", max_length=500)

    def record(self) -> dict:
        return {**self.model_dump(), "reason": redact(self.reason)}


class AssessmentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    assessment: WorkAssessment


def investigation_seconds(assessment: WorkAssessment) -> int:
    return {"UNKNOWN": 90, "SMALL": 45, "MEDIUM": 90, "LARGE": 180}[assessment.difficulty]


def initialize_work_management(db: sqlite3.Connection) -> None:
    """Add columns without recreating existing jobs or inventing past events."""
    columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
    additions = {
        "priority": "INTEGER NOT NULL DEFAULT 2 CHECK(priority BETWEEN 0 AND 3)",
        "assessment_json": "TEXT NOT NULL DEFAULT '{}'",
        "assessment_revision": "INTEGER NOT NULL DEFAULT 1",
    }
    for name, declaration in additions.items():
        if name not in columns:
            db.execute(f"ALTER TABLE jobs ADD COLUMN {name} {declaration}")
    db.executescript("""
        CREATE INDEX IF NOT EXISTS jobs_dispatch ON jobs(runner_id,state,priority,created);
        CREATE INDEX IF NOT EXISTS jobs_project_created ON jobs(project_id,created,id);
        CREATE TABLE IF NOT EXISTS job_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL REFERENCES jobs(id), kind TEXT NOT NULL,
            from_state TEXT, to_state TEXT, epoch INTEGER NOT NULL,
            occurred REAL NOT NULL, metadata_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS events_job ON job_events(job_id,id);
        CREATE TRIGGER IF NOT EXISTS job_submitted AFTER INSERT ON jobs BEGIN
            INSERT INTO job_events(job_id,kind,to_state,epoch,occurred,metadata_json)
            VALUES(new.id,'SUBMITTED',new.state,new.epoch,
                (julianday('now')-2440587.5)*86400,
                json_object('actor','authenticated-owner','assessment',json(new.assessment_json)));
        END;
        CREATE TRIGGER IF NOT EXISTS job_state_changed AFTER UPDATE OF state ON jobs
        WHEN old.state<>new.state BEGIN
            INSERT INTO job_events(job_id,kind,from_state,to_state,epoch,occurred,metadata_json)
            VALUES(new.id,'STATE_CHANGED',old.state,new.state,new.epoch,
                (julianday('now')-2440587.5)*86400,
                json_object('actor','control-plane','runner_id',new.runner_id));
        END;
        CREATE TRIGGER IF NOT EXISTS job_assessment_changed AFTER UPDATE OF assessment_revision ON jobs
        WHEN old.assessment_revision<>new.assessment_revision BEGIN
            INSERT INTO job_events(job_id,kind,from_state,to_state,epoch,occurred,metadata_json)
            VALUES(new.id,'ASSESSMENT_CHANGED',old.state,new.state,new.epoch,
                (julianday('now')-2440587.5)*86400,
                json_object('actor','authenticated-owner','revision',new.assessment_revision,
                    'before',json(old.assessment_json),'after',json(new.assessment_json)));
        END;
        CREATE TRIGGER IF NOT EXISTS model_step_requested AFTER INSERT ON model_steps BEGIN
            INSERT INTO job_events(job_id,kind,epoch,occurred,metadata_json)
            SELECT new.job_id,'MODEL_STEP',epoch,(julianday('now')-2440587.5)*86400,
                json_object('step_id',new.step_id,'state',new.state) FROM jobs WHERE id=new.job_id;
        END;
        CREATE TRIGGER IF NOT EXISTS model_step_changed AFTER UPDATE OF state ON model_steps
        WHEN old.state<>new.state BEGIN
            INSERT INTO job_events(job_id,kind,epoch,occurred,metadata_json)
            SELECT new.job_id,'MODEL_STEP',epoch,(julianday('now')-2440587.5)*86400,
                json_object('step_id',new.step_id,'state',new.state) FROM jobs WHERE id=new.job_id;
        END;
    """)
    db.execute("""INSERT INTO job_events(job_id,kind,to_state,epoch,occurred,metadata_json)
        SELECT id,'MIGRATED_SNAPSHOT',state,epoch,(julianday('now')-2440587.5)*86400,
            '{"history_before_migration":"NOT_RECORDED"}' FROM jobs j
        WHERE NOT EXISTS(SELECT 1 FROM job_events e WHERE e.job_id=j.id)""")


def job_summary(row) -> dict:
    body = json.loads(row["body"])
    assessment = WorkAssessment.model_validate(json.loads(row["assessment_json"])).record()
    result = json.loads(row["result"]) if row["result"] else {}
    run = result.get("run", result)
    candidate = result.get("job", {})
    return {
        "id": row["id"], "project_id": row["project_id"], "incident_id": row["incident_id"],
        "state": row["state"], "kind": row["kind"], "epoch": row["epoch"],
        "created": row["created"], "service": body.get("service"),
        "assessment": assessment, "assessment_revision": row["assessment_revision"],
        "investigation_max_seconds": investigation_seconds(WorkAssessment.model_validate(assessment)),
        "investigation_status": run.get("run_status"), "candidate_status": candidate.get("status"),
        "candidate_fix_verified": candidate.get("candidate_fix_verified", False),
        "service_recovery": candidate.get("service_recovery", "NOT_VERIFIED"),
        "error_type": result.get("error_type"),
    }
