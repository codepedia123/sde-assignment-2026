#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx


def wait_for_server(url: str, timeout: float = 60) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(url + "/health", timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError("simulator did not start")


def main() -> None:
    parser = argparse.ArgumentParser(description="Kill and restart the controller against one live simulator")
    parser.add_argument("--scenario", default="scenarios/public_01_dev.yaml")
    parser.add_argument("--port", type=int, default=8791)
    parser.add_argument("--output")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    python = sys.executable
    url = f"http://127.0.0.1:{args.port}"

    with tempfile.TemporaryDirectory(prefix="controller-crash-") as temp:
        temp_path = Path(temp)
        run_dir, state_dir = temp_path / "run", temp_path / "state"
        server_log = open(temp_path / "server.log", "w")
        controller_log = open(temp_path / "controller.log", "w")
        server = subprocess.Popen(
            [python, "-m", "sim", "serve", "--scenario", args.scenario, "--run-dir", str(run_dir),
             "--port", str(args.port), "--force"],
            cwd=root, stdout=server_log, stderr=subprocess.STDOUT,
        )
        first = None
        second = None
        try:
            wait_for_server(url)
            env = {**os.environ, "CONTROLLER_STATE_DIR": str(state_dir)}
            command = [python, "-m", "controller.main", "--url", url]
            first = subprocess.Popen(command, cwd=root, env=env, stdout=controller_log, stderr=subprocess.STDOUT)
            deadline = time.time() + 30
            killed_at = None
            while time.time() < deadline:
                state = httpx.get(url + "/sim/state", timeout=5).json()
                if int(state["sim_minute"]) >= 675:
                    killed_at = state
                    first.send_signal(signal.SIGKILL)
                    first.wait(timeout=5)
                    break
                if state["finished"]:
                    raise RuntimeError("controller finished before the kill point")
                time.sleep(0.02)
            if killed_at is None:
                raise RuntimeError("controller did not reach the kill point")

            second = subprocess.Popen(command, cwd=root, env=env, stdout=controller_log, stderr=subprocess.STDOUT)
            if second.wait(timeout=120) != 0:
                raise RuntimeError("restarted controller failed")
            final = httpx.get(url + "/sim/state", timeout=5).json()
            database = next(state_dir.glob("*.sqlite3"))
            db = sqlite3.connect(database)
            calls, distinct_calls = db.execute(
                "SELECT COUNT(*),COUNT(DISTINCT event_id) FROM calls"
            ).fetchone()
            proposals, distinct_proposals = db.execute(
                "SELECT COUNT(*),COUNT(DISTINCT proposal_id) FROM actions"
            ).fetchone()
            report = {
                "killed_at": killed_at,
                "final_state": final,
                "calls": calls,
                "distinct_call_event_ids": distinct_calls,
                "proposals": proposals,
                "distinct_proposal_ids": distinct_proposals,
                "checks": {
                    "scenario_finished": bool(final["finished"]),
                    "no_duplicate_call_events": calls == distinct_calls,
                    "no_duplicate_proposals": proposals == distinct_proposals,
                },
            }
            if not all(report["checks"].values()):
                raise RuntimeError(json.dumps(report, indent=2))
            rendered = json.dumps(report, indent=2)
            print(rendered)
            if args.output:
                Path(args.output).write_text(rendered + "\n")
        finally:
            for process in (first, second, server):
                if process is not None and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
            controller_log.close()
            server_log.close()


if __name__ == "__main__":
    main()

