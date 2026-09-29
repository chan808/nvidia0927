"""Typed control operations shared by SQLite and PostgreSQL, without SQL rewriting."""
from contextlib import contextmanager, closing
from pathlib import Path
import time

from sqlalchemy import and_, case, create_engine, delete, event, func, insert, or_, select, update
from sqlalchemy.pool import NullPool

from . import schema as s


class ControlTransaction:
    def __init__(self, connection):
        self.connection = connection

    def one(self, table, *, lock=False, **values):
        stmt = select(table).where(*(table.c[key] == value for key, value in values.items()))
        if lock:
            stmt = stmt.with_for_update()
        return self.connection.execute(stmt).mappings().first()

    def insert(self, table, **values):
        return self.connection.execute(insert(table).values(**values))

    def ping(self):
        return self.connection.execute(select(1)).scalar_one()

    def bind_project(self, project_id, runner_id, manifest):
        # A transaction-scoped advisory lock also serializes the first binding.
        if self.connection.dialect.name == "postgresql":
            self.connection.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(project_id, 0))))
        old = self.one(s.bindings, project_id=project_id, lock=True)
        if old and old["runner_id"] != runner_id:
            registered = self.one(s.runners, id=old["runner_id"])
            if registered and not registered["revoked"]:
                return False
        values = {"runner_id": runner_id, "manifest": manifest}
        if old:
            self.update(s.bindings, values, project_id=project_id)
        else:
            self.insert(s.bindings, project_id=project_id, **values)
        return True

    def update(self, table, values, **where):
        return self.connection.execute(update(table).where(*(table.c[key] == value for key, value in where.items())).values(**values)).rowcount

    def delete(self, table, **where):
        return self.connection.execute(delete(table).where(*(table.c[key] == value for key, value in where.items()))).rowcount

    def runner(self, digest, now):
        return self.connection.execute(select(s.runners).where(s.runners.c.digest == digest, s.runners.c.revoked == 0, s.runners.c.expires > now)).mappings().first()

    def active(self, job_id, runner_id, epoch, now):
        return self.connection.execute(select(s.jobs).where(s.jobs.c.id == job_id, s.jobs.c.runner_id == runner_id,
            s.jobs.c.epoch == epoch, s.jobs.c.state == "RUNNING", s.jobs.c.lease_until > now).with_for_update()).mappings().first()

    def expire(self, now):
        self.connection.execute(update(s.jobs).where(s.jobs.c.state == "QUEUED", s.jobs.c.expires < now).values(state="EXPIRED"))
        self.connection.execute(update(s.jobs).where(s.jobs.c.state.in_(["RUNNING", "CANCEL_REQUESTED"]), s.jobs.c.lease_until < now).values(state="RECOVERY_REQUIRED"))

    def revoke(self, runner_id):
        self.update(s.runners, {"revoked": 1}, id=runner_id)
        self.connection.execute(update(s.jobs).where(s.jobs.c.runner_id == runner_id, s.jobs.c.state == "RUNNING").values(state="CANCEL_REQUESTED"))

    def projects(self):
        return self.connection.execute(select(s.bindings, s.runners.c.last_seen, s.runners.c.revoked, s.runners.c.expires)
            .join(s.runners, s.runners.c.id == s.bindings.c.runner_id)).mappings().all()

    def latest_job(self, project_id, incident_id):
        return self.connection.execute(select(s.jobs).where(s.jobs.c.project_id == project_id, s.jobs.c.incident_id == incident_id)
            .order_by(s.jobs.c.created.desc(), s.jobs.c.id.desc()).limit(1)).mappings().first()

    def list_jobs(self, project_id, limit, cursor=None):
        stmt = select(s.jobs).where(s.jobs.c.project_id == project_id)
        if cursor:
            stmt = stmt.where(or_(s.jobs.c.created < cursor["created"], and_(s.jobs.c.created == cursor["created"], s.jobs.c.id < cursor["id"])))
        return self.connection.execute(stmt.order_by(s.jobs.c.created.desc(), s.jobs.c.id.desc()).limit(limit)).mappings().all()

    def counts(self, project_id):
        return dict(self.connection.execute(select(s.jobs.c.state, func.count()).where(s.jobs.c.project_id == project_id).group_by(s.jobs.c.state)).all())

    def events(self, job_id, after, limit):
        return self.connection.execute(select(s.job_events).where(s.job_events.c.job_id == job_id, s.job_events.c.id > after).order_by(s.job_events.c.id).limit(limit)).mappings().all()

    def claim(self, runner_id, now):
        # Every claimant locks the same runner before testing its active jobs.
        runner = self.one(s.runners, id=runner_id, lock=True)
        if not runner or runner["revoked"] or runner["expires"] <= now:
            return None
        self.connection.execute(update(s.jobs).where(s.jobs.c.runner_id == runner_id, s.jobs.c.state == "QUEUED", s.jobs.c.expires < now).values(state="EXPIRED"))
        self.connection.execute(update(s.jobs).where(s.jobs.c.runner_id == runner_id,
            s.jobs.c.state.in_(["RUNNING", "CANCEL_REQUESTED"]), s.jobs.c.lease_until < now).values(state="RECOVERY_REQUIRED"))
        busy = self.connection.execute(select(s.jobs.c.id).where(s.jobs.c.runner_id == runner_id,
            s.jobs.c.state.in_(["RUNNING", "CANCEL_REQUESTED", "RECOVERY_REQUIRED"])).limit(1)).first()
        if busy:
            return None
        row = self.connection.execute(select(s.jobs).where(s.jobs.c.runner_id == runner_id, s.jobs.c.state == "QUEUED")
            .order_by(s.jobs.c.priority, s.jobs.c.created, s.jobs.c.id).limit(1).with_for_update(skip_locked=True)).mappings().first()
        if row:
            self.update(s.jobs, {"state": "RUNNING", "epoch": row["epoch"] + 1, "lease_until": now + 60}, id=row["id"])
        return row

    def cancel(self, job_id):
        self.connection.execute(update(s.jobs).where(s.jobs.c.id == job_id, s.jobs.c.state.in_(["QUEUED", "RUNNING", "RECOVERY_REQUIRED"]))
            .values(state=case((s.jobs.c.state.in_(["QUEUED", "RECOVERY_REQUIRED"]), "CANCELLED"), else_="CANCEL_REQUESTED")))

    def model_count(self, job_id):
        return self.connection.execute(select(func.count()).select_from(s.model_steps).where(s.model_steps.c.job_id == job_id)).scalar_one()

    def memory_count(self, job_id):
        return self.connection.execute(select(func.count()).select_from(s.job_events).where(s.job_events.c.job_id == job_id,
            s.job_events.c.kind == "MEMORY_SEARCH")).scalar_one()


class ControlStore:
    def __init__(self, target):
        if not isinstance(target, (str, Path)) or not str(target).strip():
            raise ValueError("A PostgreSQL URL or SQLite file path is required")
        if isinstance(target, str) and "://" in target and not target.startswith(("postgresql://", "postgresql+psycopg://")):
            raise ValueError("Unsupported database URL; configure PostgreSQL or a SQLite file path")
        self.postgres = isinstance(target, str) and target.startswith(("postgresql://", "postgresql+psycopg://"))
        if self.postgres:
            url = target.replace("postgresql://", "postgresql+psycopg://", 1)
            self.engine = create_engine(url, pool_size=4, max_overflow=4, pool_pre_ping=True, hide_parameters=True)
            self.engine = self.engine.execution_options(schema_translate_map={None: "tracebridge"})
            self.incident_target = self
            from .migrations import upgrade
            try:
                upgrade(self.engine)
            except BaseException:
                self.engine.dispose()
                raise
        else:
            path = Path(target).resolve()
            if path.is_dir() or str(target) == ":memory:":
                raise ValueError("A persistent SQLite file path is required")
            path.parent.mkdir(parents=True, exist_ok=True)
            self.incident_target = path.with_name("server-incidents.sqlite3")
            # Existing SQLite shape/triggers are a backwards-compatible adapter.
            from ..control_plane import SCHEMA
            from ..work_management import initialize_work_management
            import sqlite3
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("PRAGMA foreign_keys=ON")
                db.executescript(SCHEMA)
                initialize_work_management(db)
            self.engine = create_engine("sqlite:///" + str(path), poolclass=NullPool, connect_args={"timeout": 5}, hide_parameters=True)
            @event.listens_for(self.engine, "connect")
            def sqlite_integrity(connection, record):
                connection.execute("PRAGMA foreign_keys=ON")
            with self.engine.begin() as connection:
                for table in (s.index_jobs, s.storage_settings, s.query_embeddings):
                    table.create(connection, checkfirst=True)
                connection.exec_driver_sql("""CREATE TRIGGER IF NOT EXISTS embedding_step_changed AFTER UPDATE OF state ON query_embeddings
                    WHEN old.state<>new.state BEGIN
                    INSERT INTO job_events(job_id,kind,epoch,occurred,metadata_json)
                    SELECT new.job_id,'QUERY_EMBEDDING',epoch,(julianday('now')-2440587.5)*86400,
                        json_object('query_hash',new.query_hash,'state',new.state) FROM jobs WHERE id=new.job_id;
                    END""")
                connection.exec_driver_sql("""CREATE TRIGGER IF NOT EXISTS embedding_step_requested AFTER INSERT ON query_embeddings BEGIN
                    INSERT INTO job_events(job_id,kind,epoch,occurred,metadata_json)
                    SELECT new.job_id,'QUERY_EMBEDDING',epoch,(julianday('now')-2440587.5)*86400,
                        json_object('query_hash',new.query_hash,'state',new.state) FROM jobs WHERE id=new.job_id;
                    END""")

    @contextmanager
    def transaction(self):
        with self.engine.connect() as connection:
            if not self.postgres:
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            else:
                connection.begin()
            try:
                yield ControlTransaction(connection)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def incidents(self, tx=None):
        if self.postgres:
            from .incidents import PostgresIncidentStore
            return PostgresIncidentStore(self.engine, connection=tx.connection if tx else None)
        from ..incident_memory import IncidentStore
        return IncidentStore(self.incident_target)

    def close(self):
        self.engine.dispose()


def open_control_store(target):
    return ControlStore(target)
