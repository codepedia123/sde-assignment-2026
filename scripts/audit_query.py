#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3


def main() -> None:
    parser = argparse.ArgumentParser(description="Explain controller decisions for a workflow and time range")
    parser.add_argument("database")
    parser.add_argument("workflow_id")
    parser.add_argument("--from-minute", type=int, default=-10**9)
    parser.add_argument("--to-minute", type=int, default=10**9)
    args = parser.parse_args()

    db = sqlite3.connect(args.database)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT sim_minute,ts,decision_type,summary,evidence_json,action_id FROM audit "
        "WHERE workflow_id=? AND sim_minute BETWEEN ? AND ? ORDER BY sim_minute,id",
        (args.workflow_id, args.from_minute, args.to_minute),
    )
    for row in rows:
        print(json.dumps({**dict(row), "evidence": json.loads(row["evidence_json"])}, indent=2))


if __name__ == "__main__":
    main()

