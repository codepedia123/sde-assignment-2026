from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workflows (
    workflow_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    spec_json TEXT NOT NULL,
    current_version INTEGER NOT NULL,
    config_json TEXT NOT NULL,
    actor TEXT,
    safe_prompt TEXT,
    updated_minute INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS chunks (
    kind TEXT NOT NULL,
    chunk INTEGER NOT NULL,
    first_event_id INTEGER,
    last_event_id INTEGER,
    processed_at_minute INTEGER NOT NULL,
    PRIMARY KEY (kind, chunk)
);

CREATE TABLE IF NOT EXISTS leads (
    lead_id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    lang TEXT,
    city_tier TEXT,
    age_band TEXT,
    value_band TEXT,
    created_event_id INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS calls (
    event_id INTEGER PRIMARY KEY,
    sim_minute INTEGER NOT NULL,
    ts TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    lead_id TEXT NOT NULL,
    person_id TEXT NOT NULL,
    attempt_no INTEGER NOT NULL,
    config_version INTEGER NOT NULL,
    cohort_id TEXT NOT NULL,
    welcome_message TEXT NOT NULL,
    voice TEXT NOT NULL,
    caller_id TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    connected INTEGER NOT NULL,
    engaged INTEGER NOT NULL,
    hallucination_flag INTEGER NOT NULL,
    flow_adherence REAL
);

CREATE TABLE IF NOT EXISTS config_events (
    event_id INTEGER PRIMARY KEY,
    sim_minute INTEGER NOT NULL,
    ts TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    actor TEXT NOT NULL,
    changed_dims_json TEXT NOT NULL,
    proposal_id TEXT,
    approval_id TEXT
);

CREATE TABLE IF NOT EXISTS approval_events (
    event_id INTEGER PRIMARY KEY,
    sim_minute INTEGER NOT NULL,
    ts TEXT NOT NULL,
    approval_id TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    status TEXT NOT NULL,
    reasons_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS actions (
    action_id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL UNIQUE,
    workflow_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    created_minute INTEGER NOT NULL,
    base_version INTEGER NOT NULL,
    target_config_json TEXT NOT NULL,
    request_json TEXT NOT NULL,
    approval_id TEXT,
    applied_version INTEGER,
    error_json TEXT,
    updated_minute INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sim_minute INTEGER NOT NULL,
    ts TEXT NOT NULL,
    workflow_id TEXT,
    decision_type TEXT NOT NULL,
    summary TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    action_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS signals (
    workflow_id TEXT NOT NULL,
    signal_key TEXT NOT NULL,
    consecutive INTEGER NOT NULL,
    last_minute INTEGER NOT NULL,
    evidence_json TEXT NOT NULL,
    PRIMARY KEY (workflow_id, signal_key)
);

CREATE TABLE IF NOT EXISTS incidents (
    incident_id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    detected_minute INTEGER NOT NULL,
    source_version INTEGER,
    source_prompt TEXT,
    safe_prompt TEXT,
    evidence_json TEXT NOT NULL,
    action_id TEXT,
    resolved_minute INTEGER
);

CREATE TABLE IF NOT EXISTS validation_runs (
    validation_run_id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    n INTEGER NOT NULL,
    pass_rate REAL NOT NULL,
    sim_minute INTEGER NOT NULL,
    response_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_calls_workflow_minute
ON calls(workflow_id, sim_minute);
CREATE INDEX IF NOT EXISTS idx_calls_workflow_config
ON calls(workflow_id, config_version);
CREATE INDEX IF NOT EXISTS idx_calls_lead
ON calls(lead_id);
CREATE INDEX IF NOT EXISTS idx_calls_workflow_caller
ON calls(workflow_id, caller_id, sim_minute);
CREATE INDEX IF NOT EXISTS idx_actions_workflow_status
ON actions(workflow_id, status);
CREATE INDEX IF NOT EXISTS idx_audit_workflow_minute
ON audit(workflow_id, sim_minute);
"""


def dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def loads(value: str | None, default: Any = None) -> Any:
    return default if value is None else json.loads(value)


class Store:
    """SQLite-backed durable state and audit store.

    A complete event chunk and its checkpoint are committed atomically.  If the
    controller is killed while reading a chunk, the transaction rolls back and
    the same chunk is safely replayed on restart.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=60, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=60000")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield self.db
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        else:
            self.db.execute("COMMIT")

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key: str, value: Any, conn: sqlite3.Connection | None = None) -> None:
        target = conn or self.db
        target.execute(
            "INSERT INTO metadata(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )

    def reset_runtime(self) -> None:
        """Reset a database that belongs to an older simulator world."""
        with self.transaction() as db:
            for table in (
                "metadata", "workflows", "chunks", "leads", "calls",
                "config_events", "approval_events", "actions", "audit",
                "signals", "incidents", "validation_runs",
            ):
                db.execute(f"DELETE FROM {table}")

    def chunk_done(self, kind: str, chunk: int) -> bool:
        return self.db.execute(
            "SELECT 1 FROM chunks WHERE kind=? AND chunk=?", (kind, chunk)
        ).fetchone() is not None

    def ingest_chunk(
        self,
        meta: dict[str, Any],
        events: Iterable[dict[str, Any]],
        to_minute,
        processed_at_minute: int,
    ) -> dict[str, int]:
        counts = {"lead": 0, "call": 0, "config_changed": 0, "approval_decided": 0, "other": 0}
        leads: list[tuple[Any, ...]] = []
        calls: list[tuple[Any, ...]] = []
        configs: list[tuple[Any, ...]] = []
        approvals: list[tuple[Any, ...]] = []

        def flush(db: sqlite3.Connection) -> None:
            if leads:
                db.executemany(
                    "INSERT OR IGNORE INTO leads(lead_id,person_id,workflow_id,campaign_id,lang,city_tier,"
                    "age_band,value_band,created_event_id) VALUES(?,?,?,?,?,?,?,?,?)",
                    leads,
                )
                leads.clear()
            if calls:
                db.executemany(
                    "INSERT OR IGNORE INTO calls(event_id,sim_minute,ts,workflow_id,campaign_id,lead_id,"
                    "person_id,attempt_no,config_version,cohort_id,welcome_message,voice,caller_id,"
                    "prompt_version,connected,engaged,hallucination_flag,flow_adherence) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    calls,
                )
                calls.clear()
            if configs:
                db.executemany(
                    "INSERT OR IGNORE INTO config_events(event_id,sim_minute,ts,workflow_id,version,actor,"
                    "changed_dims_json,proposal_id,approval_id) VALUES(?,?,?,?,?,?,?,?,?)",
                    configs,
                )
                configs.clear()
            if approvals:
                db.executemany(
                    "INSERT OR IGNORE INTO approval_events(event_id,sim_minute,ts,approval_id,workflow_id,"
                    "status,reasons_json) VALUES(?,?,?,?,?,?,?)",
                    approvals,
                )
                approvals.clear()

        with self.transaction() as db:
            for ev in events:
                typ = ev.get("type")
                event_id = int(ev["event_id"])
                ts = ev.get("ts", "")
                minute = to_minute(ts)
                if typ == "lead":
                    f = ev.get("features") or {}
                    leads.append((
                        ev["lead_id"], ev["person_id"], ev["workflow_id"], ev["campaign_id"],
                        f.get("lang"), f.get("city_tier"), f.get("age_band"), f.get("value_band"), event_id,
                    ))
                    counts[typ] += 1
                elif typ == "call":
                    v = ev.get("variant") or {}
                    qa = ev.get("qa") or {}
                    calls.append((
                        event_id, minute, ts, ev["workflow_id"], ev["campaign_id"], ev["lead_id"],
                        ev["person_id"], int(ev.get("attempt_no", 0)), int(ev["config_version"]),
                        ev.get("cohort_id") or "default", v.get("welcome_message", ""), v.get("voice", ""),
                        v.get("caller_id", ""), v.get("prompt_version", ""), int(bool(ev.get("connected"))),
                        int(bool(ev.get("engaged"))), int(bool(qa.get("hallucination_flag"))),
                        qa.get("flow_adherence"),
                    ))
                    counts[typ] += 1
                elif typ == "config_changed":
                    configs.append((
                        event_id, minute, ts, ev["workflow_id"], int(ev["version"]), ev["actor"],
                        dumps(ev.get("changed_dims") or []), ev.get("proposal_id"), ev.get("approval_id"),
                    ))
                    counts[typ] += 1
                elif typ == "approval_decided":
                    approvals.append((
                        event_id, minute, ts, ev["approval_id"], ev["workflow_id"], ev["status"],
                        dumps(ev.get("reasons") or []),
                    ))
                    counts[typ] += 1
                else:
                    counts["other"] += 1
                if len(leads) + len(calls) + len(configs) + len(approvals) >= 10_000:
                    flush(db)
            flush(db)
            db.execute(
                "INSERT OR IGNORE INTO chunks(kind,chunk,first_event_id,last_event_id,processed_at_minute) "
                "VALUES(?,?,?,?,?)",
                (
                    meta["kind"], int(meta["chunk"]), meta.get("first_event_id"),
                    meta.get("last_event_id"), processed_at_minute,
                ),
            )
            self.set_meta("last_event_id", meta.get("last_event_id", 0), db)
        return counts

    def upsert_workflow(
        self,
        spec: dict[str, Any],
        config_response: dict[str, Any],
        minute: int,
    ) -> tuple[dict[str, Any] | None, sqlite3.Row | None]:
        wid = spec["workflow_id"]
        old = self.db.execute("SELECT * FROM workflows WHERE workflow_id=?", (wid,)).fetchone()
        old_config = loads(old["config_json"]) if old else None
        prompt = config_response["config"]["prompt_version"]
        safe_prompt = old["safe_prompt"] if old and old["safe_prompt"] else prompt
        with self.transaction() as db:
            db.execute(
                "INSERT INTO workflows(workflow_id,tenant_id,spec_json,current_version,config_json,actor,"
                "safe_prompt,updated_minute) VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(workflow_id) DO UPDATE SET tenant_id=excluded.tenant_id,spec_json=excluded.spec_json,"
                "current_version=excluded.current_version,config_json=excluded.config_json,actor=excluded.actor,"
                "updated_minute=excluded.updated_minute",
                (
                    wid, spec["tenant_id"], dumps(spec), int(config_response["version"]),
                    dumps(config_response["config"]), config_response.get("actor"), safe_prompt, minute,
                ),
            )
        return old_config, old

    def workflows(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM workflows ORDER BY workflow_id").fetchall()

    def workflow(self, wid: str) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM workflows WHERE workflow_id=?", (wid,)).fetchone()
        if not row:
            raise KeyError(wid)
        return row

    def set_safe_prompt(self, wid: str, prompt: str) -> None:
        self.db.execute("UPDATE workflows SET safe_prompt=? WHERE workflow_id=?", (prompt, wid))

    def audit(
        self,
        minute: int,
        ts: str,
        decision_type: str,
        summary: str,
        evidence: dict[str, Any] | list[Any] | None = None,
        workflow_id: str | None = None,
        action_id: str | None = None,
    ) -> None:
        self.db.execute(
            "INSERT INTO audit(sim_minute,ts,workflow_id,decision_type,summary,evidence_json,action_id) "
            "VALUES(?,?,?,?,?,?,?)",
            (minute, ts, workflow_id, decision_type, summary, dumps(evidence or {}), action_id),
        )

    def create_action(self, action: dict[str, Any]) -> bool:
        try:
            self.db.execute(
                "INSERT INTO actions(action_id,proposal_id,workflow_id,kind,mode,status,created_minute,"
                "base_version,target_config_json,request_json,updated_minute) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    action["action_id"], action["proposal_id"], action["workflow_id"], action["kind"],
                    action["mode"], action["status"], action["created_minute"], action["base_version"],
                    dumps(action["target_config"]), dumps(action["request"]), action["created_minute"],
                ),
            )
            return True
        except sqlite3.IntegrityError:
            return False

    def update_action(self, action_id: str, minute: int, **fields: Any) -> None:
        allowed = {"status", "approval_id", "applied_version", "error_json", "request_json"}
        parts, values = [], []
        for key, value in fields.items():
            if key not in allowed:
                raise ValueError(key)
            parts.append(f"{key}=?")
            values.append(dumps(value) if key.endswith("_json") and not isinstance(value, str) else value)
        parts.append("updated_minute=?")
        values.extend([minute, action_id])
        self.db.execute(f"UPDATE actions SET {','.join(parts)} WHERE action_id=?", values)

    def action(self, action_id: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM actions WHERE action_id=?", (action_id,)).fetchone()

    def action_by_proposal(self, proposal_id: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM actions WHERE proposal_id=?", (proposal_id,)).fetchone()

    def open_actions(self) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM actions WHERE status IN ('prepared','sending','pending','approved','applying') "
            "ORDER BY created_minute"
        ).fetchall()

    def workflow_has_open_action(self, wid: str) -> bool:
        return self.db.execute(
            "SELECT 1 FROM actions WHERE workflow_id=? AND status IN "
            "('prepared','sending','pending','approved','applying') LIMIT 1", (wid,)
        ).fetchone() is not None

    def latest_action(self, wid: str, kind: str) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT * FROM actions WHERE workflow_id=? AND kind=? ORDER BY created_minute DESC LIMIT 1",
            (wid, kind),
        ).fetchone()

    def set_signal(
        self,
        wid: str,
        key: str,
        active: bool,
        minute: int,
        evidence: dict[str, Any],
    ) -> int:
        old = self.db.execute(
            "SELECT consecutive,last_minute FROM signals WHERE workflow_id=? AND signal_key=?", (wid, key)
        ).fetchone()
        is_adjacent = old is not None and minute - int(old["last_minute"]) <= 90
        consecutive = (int(old["consecutive"]) + 1) if active and is_adjacent else (1 if active else 0)
        self.db.execute(
            "INSERT INTO signals(workflow_id,signal_key,consecutive,last_minute,evidence_json) VALUES(?,?,?,?,?) "
            "ON CONFLICT(workflow_id,signal_key) DO UPDATE SET consecutive=excluded.consecutive,"
            "last_minute=excluded.last_minute,evidence_json=excluded.evidence_json",
            (wid, key, consecutive, minute, dumps(evidence)),
        )
        return consecutive

    def create_incident(self, incident: dict[str, Any]) -> bool:
        try:
            self.db.execute(
                "INSERT INTO incidents(incident_id,workflow_id,kind,status,detected_minute,source_version,"
                "source_prompt,safe_prompt,evidence_json) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    incident["incident_id"], incident["workflow_id"], incident["kind"], "open",
                    incident["detected_minute"], incident.get("source_version"), incident.get("source_prompt"),
                    incident.get("safe_prompt"), dumps(incident.get("evidence") or {}),
                ),
            )
            return True
        except sqlite3.IntegrityError:
            return False

    def open_incident(self, wid: str, kind: str) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT * FROM incidents WHERE workflow_id=? AND kind=? AND status!='resolved' "
            "ORDER BY detected_minute DESC LIMIT 1", (wid, kind)
        ).fetchone()

    def update_incident(self, incident_id: str, **fields: Any) -> None:
        allowed = {"status", "action_id", "resolved_minute", "evidence_json"}
        parts, vals = [], []
        for key, value in fields.items():
            if key not in allowed:
                raise ValueError(key)
            parts.append(f"{key}=?")
            vals.append(dumps(value) if key == "evidence_json" and not isinstance(value, str) else value)
        vals.append(incident_id)
        self.db.execute(f"UPDATE incidents SET {','.join(parts)} WHERE incident_id=?", vals)

    def save_validation(self, response: dict[str, Any], minute: int) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO validation_runs(validation_run_id,workflow_id,prompt_version,n,pass_rate,"
            "sim_minute,response_json) VALUES(?,?,?,?,?,?,?)",
            (
                response["validation_run_id"], response["workflow_id"], response["prompt_version"],
                int(response["n"]), float(response["pass_rate"]), minute, dumps(response),
            ),
        )

    def scalar(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        row = self.db.execute(sql, params).fetchone()
        return row[0] if row else None

    def rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        return self.db.execute(sql, params).fetchall()

