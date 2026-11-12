from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .api import SimulatorClient, SimulatorError
from .statistics import (
    Rate,
    caller_health,
    choose_winner,
    feature_winner_candidates,
    option_rates,
    two_proportion_z,
    workflow_metrics,
)
from .store import Store, dumps, loads


BASE_TIME = datetime.fromisoformat("2027-01-04T00:00:00+05:30")
OPEN_ACTIONS = {"prepared", "sending", "pending", "approved", "applying"}


def to_minute(ts: str) -> int:
    return int((datetime.fromisoformat(ts) - BASE_TIME).total_seconds() // 60)


def hour_at(minute: int) -> int:
    return (minute % 1440) // 60


def day_at(minute: int) -> int:
    return minute // 1440


def distribution(config: dict[str, Any], cohort: str, dim: str) -> dict[str, float]:
    return (
        (config["allocations"].get(cohort) or {}).get(dim)
        or config["allocations"]["default"][dim]
    )


def changed_dimensions(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    changed: list[str] = []
    if old.get("cohorts", []) != new.get("cohorts", []):
        changed.append("cohorts")
    cohorts = {"default"}
    cohorts.update(c["id"] for c in old.get("cohorts", []))
    cohorts.update(c["id"] for c in new.get("cohorts", []))
    for dim in ("welcome_message", "voice", "caller_id"):
        if any(distribution(old, c, dim) != distribution(new, c, dim) for c in cohorts):
            changed.append(dim)
    if old.get("prompt_version") != new.get("prompt_version"):
        changed.append("prompt_version")
    if old.get("schedule") != new.get("schedule"):
        changed.append("schedule")
    return changed


def allocation_shift(old: dict[str, Any], new: dict[str, Any]) -> float:
    cohorts = {"default"}
    cohorts.update(c["id"] for c in old.get("cohorts", []))
    cohorts.update(c["id"] for c in new.get("cohorts", []))
    worst = 0.0
    for cohort in cohorts:
        for dim in ("welcome_message", "voice", "caller_id"):
            a, b = distribution(old, cohort, dim), distribution(new, cohort, dim)
            tv = 0.5 * sum(abs(a.get(k, 0) - b.get(k, 0)) for k in set(a) | set(b))
            worst = max(worst, tv)
    return worst


def config_diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for dim in changed_dimensions(old, new):
        if dim == "prompt_version":
            result[dim] = {"from": old[dim], "to": new[dim]}
        elif dim == "cohorts":
            result[dim] = {"from": old.get(dim, []), "to": new.get(dim, [])}
        elif dim == "schedule":
            result[dim] = {"from": old.get(dim, {}), "to": new.get(dim, {})}
        else:
            result[dim] = {
                "from": old["allocations"],
                "to": new["allocations"],
            }
    return result


def clean_id(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


class Controller:
    def __init__(self, sim_url: str, state_root: str | Path):
        self.api = SimulatorClient(sim_url)
        self.initial_state = self.api.state()
        self.end_minute = int(self.initial_state["end_minute"])
        scenario = clean_id(self.initial_state["scenario"])
        self.store = Store(Path(state_root) / f"{scenario}.sqlite3")
        self.scenario = scenario
        self.state = self.initial_state
        self.specs: dict[str, dict[str, Any]] = {}
        self._initialise_world_identity()

    def close(self) -> None:
        self.api.close()
        self.store.close()

    def log(self, message: str, **fields: Any) -> None:
        suffix = " " + dumps(fields) if fields else ""
        print(f"[{self.state.get('ts', '?')}] {message}{suffix}", flush=True)

    def _initialise_world_identity(self) -> None:
        remote_minute = int(self.initial_state["sim_minute"])
        stored_scenario = self.store.get_meta("scenario")
        stored_minute = int(self.store.get_meta("last_sim_minute", "-999999999") or -999999999)
        if stored_scenario and (stored_scenario != self.scenario or remote_minute < stored_minute):
            self.store.reset_runtime()
        self.store.set_meta("scenario", self.scenario)
        self.store.set_meta("last_sim_minute", remote_minute)

    def initialise(self) -> None:
        self.api.health()
        for spec in self.api.workflows():
            self.specs[spec["workflow_id"]] = spec
        # History is part of the controller's observable contract and is ingested
        # before any decision is made at D0.
        self.ingest_available("history")
        self.ingest_available("events")
        self.sync_workflows()
        self.reconcile_actions()
        self.store.audit(
            int(self.state["sim_minute"]), self.state["ts"], "controller_started",
            "Controller started or resumed and reconciled durable state.",
            {"scenario": self.scenario, "last_event_id": self.store.get_meta("last_event_id", "0")},
        )

    def ingest_available(self, kind: str) -> None:
        chunks = self.api.chunks(kind, -1)
        for meta in chunks:
            if self.store.chunk_done(kind, int(meta["chunk"])):
                continue
            counts = self.store.ingest_chunk(
                meta, self.api.chunk_events(int(meta["chunk"])), to_minute, int(self.state["sim_minute"])
            )
            self.log("ingested chunk", kind=kind, chunk=meta["chunk"], counts=counts)

    def sync_workflows(self) -> None:
        minute, ts = int(self.state["sim_minute"]), self.state["ts"]
        for wid, spec in self.specs.items():
            response = self.api.config(wid)
            old_config, old_row = self.store.upsert_workflow(spec, response, minute)
            if old_config is None or int(old_row["current_version"]) == int(response["version"]):
                continue
            actor = response.get("actor") or "unknown"
            dims = changed_dimensions(old_config, response["config"])
            self.store.audit(
                minute, ts, "config_reconciled",
                f"Observed workflow config v{response['version']} from {actor}.",
                {
                    "from_version": int(old_row["current_version"]), "to_version": response["version"],
                    "actor": actor, "changed_dims": dims,
                }, wid,
            )
            if actor == "supervisor":
                self.store.audit(
                    minute, ts, "supervisor_edit",
                    "Supervisor edit became the new configuration baseline; pending stale changes will not overwrite it.",
                    {"changed_dims": dims, "version": response["version"]}, wid,
                )

    def _history_contains(self, wid: str, *, proposal_id: str | None = None, approval_id: str | None = None):
        for item in self.api.config_history(wid):
            if proposal_id and item.get("proposal_id") == proposal_id:
                return item
            if approval_id and item.get("approval_id") == approval_id:
                return item
        return None

    def reconcile_actions(self) -> None:
        minute, ts = int(self.state["sim_minute"]), self.state["ts"]
        approvals = self.api.approvals()
        by_proposal = {a["proposal_id"]: a for a in approvals}
        for action in self.store.open_actions():
            aid = action["action_id"]
            wid = action["workflow_id"]
            if action["mode"] == "auto":
                applied = self._history_contains(wid, proposal_id=action["proposal_id"])
                if applied:
                    self._mark_applied(action, int(applied["version"]))
                elif action["status"] in ("prepared", "sending"):
                    self._send_auto(action)
                continue

            approval = by_proposal.get(action["proposal_id"])
            if approval and action["approval_id"] != approval["approval_id"]:
                self.store.update_action(aid, minute, status=approval["status"], approval_id=approval["approval_id"])
                action = self.store.action(aid)
            if not approval and action["status"] in ("prepared", "sending"):
                self._send_approval(action)
                continue
            if not approval:
                continue
            status = approval["status"]
            if status == "approved":
                self.store.update_action(aid, minute, status="approved")
                self._apply_approved(self.store.action(aid), approval)
            elif status == "applied":
                applied = self._history_contains(wid, approval_id=approval["approval_id"])
                self._mark_applied(action, int(applied["version"]) if applied else -1)
            elif status in ("rejected", "expired", "lapsed"):
                self.store.update_action(aid, minute, status=status, error_json=approval.get("reasons") or [])
                self.store.audit(
                    minute, ts, "action_closed", f"Proposal {status}; it will not be replayed.",
                    {"approval": approval}, wid, aid,
                )

    def _mark_applied(self, action, version: int) -> None:
        minute, ts = int(self.state["sim_minute"]), self.state["ts"]
        self.store.update_action(action["action_id"], minute, status="applied", applied_version=version)
        self.store.audit(
            minute, ts, "action_applied", f"Applied {action['kind']} as config v{version}.",
            {"proposal_id": action["proposal_id"], "mode": action["mode"]},
            action["workflow_id"], action["action_id"],
        )
        if action["kind"] == "prompt_recovery":
            incident = self.store.open_incident(action["workflow_id"], "hallucination")
            if incident:
                self.store.update_incident(incident["incident_id"], status="resolved", resolved_minute=minute)
        # Keep the local config mirror current immediately.  This prevents a
        # second decision in the same tick from proposing against the version
        # that existed before this action was applied.
        response = self.api.config(action["workflow_id"])
        self.store.upsert_workflow(self.specs[action["workflow_id"]], response, minute)

    def _send_approval(self, action) -> None:
        minute, ts = int(self.state["sim_minute"]), self.state["ts"]
        request = loads(action["request_json"])
        self.store.update_action(action["action_id"], minute, status="sending")
        try:
            response = self.api.request_approval(request)
        except SimulatorError as exc:
            if exc.status == 409:
                match = next((a for a in self.api.approvals() if a["proposal_id"] == action["proposal_id"]), None)
                if match:
                    self.store.update_action(
                        action["action_id"], minute, status=match["status"], approval_id=match["approval_id"]
                    )
                    return
            self.store.update_action(action["action_id"], minute, status="failed", error_json=exc.payload)
            self.store.audit(
                minute, ts, "action_failed", "Approval request failed and was closed safely.",
                {"status": exc.status, "error": exc.payload}, action["workflow_id"], action["action_id"],
            )
            return
        self.store.update_action(
            action["action_id"], minute, status=response["status"], approval_id=response["approval_id"]
        )
        self.store.audit(
            minute, ts, "approval_requested", request["change_summary"],
            {"approval_id": response["approval_id"], "request": request},
            action["workflow_id"], action["action_id"],
        )

    def _send_auto(self, action) -> None:
        minute, ts = int(self.state["sim_minute"]), self.state["ts"]
        request = loads(action["request_json"])
        self.store.update_action(action["action_id"], minute, status="sending")
        try:
            response = self.api.auto_apply(action["workflow_id"], request)
        except SimulatorError as exc:
            applied = self._history_contains(action["workflow_id"], proposal_id=action["proposal_id"])
            if applied:
                self._mark_applied(action, int(applied["version"]))
                return
            self.store.update_action(action["action_id"], minute, status="failed", error_json=exc.payload)
            self.store.audit(
                minute, ts, "auto_apply_failed", "Policy-constrained auto-apply was rejected.",
                {"status": exc.status, "error": exc.payload}, action["workflow_id"], action["action_id"],
            )
            return
        self._mark_applied(action, int(response["version"]))

    def _apply_approved(self, action, approval: dict[str, Any]) -> None:
        minute, ts = int(self.state["sim_minute"]), self.state["ts"]
        self.store.update_action(action["action_id"], minute, status="applying")
        try:
            response = self.api.apply_approval(action["workflow_id"], approval["approval_id"])
        except SimulatorError as exc:
            applied = self._history_contains(action["workflow_id"], approval_id=approval["approval_id"])
            if applied:
                self._mark_applied(action, int(applied["version"]))
                return
            status = "stale" if exc.status == 409 and "stale" in dumps(exc.payload) else "failed"
            self.store.update_action(action["action_id"], minute, status=status, error_json=exc.payload)
            self.store.audit(
                minute, ts, "apply_conflict", "Approved proposal was not applied over a changed base config.",
                {"status": exc.status, "error": exc.payload}, action["workflow_id"], action["action_id"],
            )
            return
        self._mark_applied(action, int(response["version"]))

    def _proposal_id(self, wid: str, kind: str, version: int, minute: int) -> str:
        raw = f"{self.scenario}|{wid}|{kind}|{version}|{minute}"
        return f"ctl-{hashlib.sha256(raw.encode()).hexdigest()[:24]}"

    def _can_request_approval(self, spec: dict[str, Any]) -> bool:
        hour = hour_at(int(self.state["sim_minute"]))
        _, end = spec["governance"]["approver_hours"]
        return 11 <= hour < end - 1

    def submit_change(
        self,
        wid: str,
        kind: str,
        new_config: dict[str, Any],
        summary: str,
        rationale: str,
        evidence: dict[str, Any],
        dry_run: dict[str, Any],
        impact_score: float,
        affected_leads: int,
        prefer_auto: bool = False,
    ) -> str | None:
        minute = int(self.state["sim_minute"])
        if self.store.workflow_has_open_action(wid):
            return None
        row = self.store.workflow(wid)
        old = loads(row["config_json"])
        dims = changed_dimensions(old, new_config)
        if not dims:
            return None
        spec = loads(row["spec_json"])
        policy = spec["governance"]["auto_apply"]
        auto = (
            prefer_auto and policy.get("enabled")
            and set(dims).issubset(set(policy.get("allowed_dims") or []))
            and allocation_shift(old, new_config) <= float(policy.get("max_shift", 0)) + 1e-9
        )
        if not auto and not self._can_request_approval(spec):
            return None
        pid = self._proposal_id(wid, kind, int(row["current_version"]), minute)
        action_id = pid
        request: dict[str, Any]
        if auto:
            request = {
                "proposal_id": pid,
                "base_version": int(row["current_version"]),
                "proposed_config": new_config,
                "rationale": rationale,
                "evidence": evidence,
            }
            mode = "auto"
        else:
            request = {
                "proposal_id": pid,
                "workflow_id": wid,
                "base_version": int(row["current_version"]),
                "proposed_config": new_config,
                "change_summary": summary,
                "diff": config_diff(old, new_config),
                "affected_leads": max(0, int(affected_leads)),
                "dry_run": dry_run,
                "impact_score": max(0.0, min(float(impact_score), 1.0)),
                "rationale": rationale,
            }
            if evidence:
                request["evidence"] = evidence
            mode = "approval"
        created = self.store.create_action({
            "action_id": action_id, "proposal_id": pid, "workflow_id": wid, "kind": kind,
            "mode": mode, "status": "prepared", "created_minute": minute,
            "base_version": int(row["current_version"]), "target_config": new_config, "request": request,
        })
        if not created:
            return None
        self.store.audit(
            minute, self.state["ts"], "action_prepared", summary,
            {"kind": kind, "mode": mode, "dims": dims, "rationale": rationale, "evidence": evidence},
            wid, action_id,
        )
        action = self.store.action(action_id)
        self._send_auto(action) if mode == "auto" else self._send_approval(action)
        return action_id

    @staticmethod
    def _exploration_weights(options: list[str]) -> dict[str, float]:
        if len(options) == 1:
            return {options[0]: 1.0}
        baseline = 0.5 if len(options) <= 3 else 0.4
        rest = (1 - baseline) / (len(options) - 1)
        return {option: (baseline if i == 0 else rest) for i, option in enumerate(options)}

    def _caller_exploration(self, wid: str, spec: dict[str, Any], cfg: dict[str, Any]) -> dict[str, float]:
        options = spec["options"]["caller_id"]
        observed = {r.option: r for r in caller_health(self.store, wid, -10**9)}
        current = distribution(cfg, "default", "caller_id")
        active = [o for o in options if o in current]
        unseen = [o for o in options if o not in observed and o not in active]
        # Explore at most two new DIDs.  A fourth unknown DID is not exposed merely
        # to make the allocation symmetric: caller reputation is a safety concern.
        explore = unseen[: max(0, 3 - len(active))]
        selected = active + explore
        if not selected:
            selected = options[:1]
        if len(selected) == 1:
            return {selected[0]: 1.0}
        exploration_mass = min(0.2 * len(explore), 0.4)
        if not explore:
            return {o: 1 / len(selected) for o in selected}
        out = {o: exploration_mass / len(explore) for o in explore}
        for o in active:
            out[o] = (1 - exploration_mass) / len(active)
        return out

    def bootstrap_experiments(self) -> None:
        minute = int(self.state["sim_minute"])
        if day_at(minute) > 2:
            return
        for row in self.store.workflows():
            wid = row["workflow_id"]
            latest = self.store.latest_action(wid, "bootstrap_experiment")
            if latest and latest["status"] in OPEN_ACTIONS | {"applied"}:
                continue
            if latest and minute - int(latest["created_minute"]) < 1440:
                continue
            spec = loads(row["spec_json"])
            if not self._can_request_approval(spec):
                continue
            cfg = copy.deepcopy(loads(row["config_json"]))
            # Agent-facing variants affect engagement, not pickup.  A
            # pickup-primary workflow therefore explores caller IDs only;
            # changing welcome or voice could only endanger its guardrail.
            if spec["objective"]["primary"] == "engagement_rate":
                cfg["allocations"]["default"]["welcome_message"] = self._exploration_weights(
                    spec["options"]["welcome_message"]
                )
                cfg["allocations"]["default"]["voice"] = self._exploration_weights(spec["options"]["voice"])
            cfg["allocations"]["default"]["caller_id"] = self._caller_exploration(wid, spec, cfg)
            metrics = workflow_metrics(self.store, wid)
            affected = int(spec["daily_batch"]["size"] * len(spec["cadence"]))
            self.submit_change(
                wid, "bootstrap_experiment", cfg,
                "Start bounded welcome, voice and caller-ID exploration using historical baselines.",
                "Randomised allocation is required to learn causal variant effects. The baseline retains the "
                "largest share, pickup and engagement remain separate, and no prompt is changed.",
                {"history_metrics": metrics},
                {"method": "history replay summary", "baseline": metrics, "expected_harm_bound": "guardrails monitored"},
                0.45, affected,
            )

    def _experiment_start(self, wid: str) -> int | None:
        action = self.store.latest_action(wid, "bootstrap_experiment")
        if not action or action["status"] != "applied":
            return None
        return int(action["updated_minute"])

    def promote_experiment_winners(self) -> None:
        minute = int(self.state["sim_minute"])
        for row in self.store.workflows():
            wid = row["workflow_id"]
            start = self._experiment_start(wid)
            if start is None or minute - start < 2 * 1440:
                continue
            last_eval = int(self.store.get_meta(f"promotion_eval:{wid}", "-999999") or -999999)
            if minute - last_eval < 720:
                continue
            self.store.set_meta(f"promotion_eval:{wid}", minute)
            existing = self.store.latest_action(wid, "promote_experiment")
            if existing and existing["status"] in OPEN_ACTIONS | {"applied"}:
                continue
            if self.store.open_incident(wid, "hallucination"):
                continue
            spec, cfg = loads(row["spec_json"]), copy.deepcopy(loads(row["config_json"]))
            if spec["objective"]["primary"] != "engagement_rate":
                continue
            evidence: dict[str, Any] = {"since_minute": start, "dimensions": {}}
            global_winners: dict[str, str] = {}
            changed = False
            for dim in ("welcome_message", "voice"):
                rates = option_rates(self.store, wid, dim, start)
                winner = choose_winner(rates, min_trials=100, min_uplift=0.015)
                evidence["dimensions"][dim] = {
                    "rates": [r.__dict__ | {"rate": r.value, "wilson_95": r.wilson()} for r in rates],
                    "decision": winner,
                }
                if winner:
                    global_winners[dim] = winner["winner"]
                    options = spec["options"][dim]
                    cfg["allocations"]["default"][dim] = {
                        o: (0.85 if o == winner["winner"] else 0.15 / (len(options) - 1))
                        for o in options
                    } if len(options) > 1 else {options[0]: 1.0}
                    changed = True

            cohort_candidates: list[dict[str, Any]] = []
            for dim, global_winner in global_winners.items():
                cohort_candidates.extend(
                    feature_winner_candidates(self.store, wid, dim, start, global_winner, min_trials=60)[:2]
                )
            # At most two transparent, one-feature cohorts.  They are shared by
            # config, experiment analysis and audit output.
            for candidate in cohort_candidates[:2]:
                cid = clean_id(f"{candidate['feature']}_{candidate['value']}")
                if cid not in {c["id"] for c in cfg.get("cohorts", [])}:
                    cfg.setdefault("cohorts", []).append(
                        {"id": cid, "where": {candidate["feature"]: [candidate["value"]]}}
                    )
                dim, winner = candidate["dimension"], candidate["winner"]
                options = spec["options"][dim]
                cfg["allocations"].setdefault(cid, {})[dim] = {
                    o: (0.85 if o == winner else 0.15 / (len(options) - 1))
                    for o in options
                } if len(options) > 1 else {options[0]: 1.0}
                changed = True
            evidence["cohort_candidates"] = cohort_candidates[:2]
            if not changed:
                self.store.audit(
                    minute, self.state["ts"], "experiment_continue",
                    "No variant cleared the sample-size, practical-uplift and multiplicity-adjusted threshold.",
                    evidence, wid,
                )
                continue
            affected = int(spec["daily_batch"]["size"] * len(spec["cadence"]))
            best_uplift = max(
                (d["decision"]["absolute_uplift"] for d in evidence["dimensions"].values() if d["decision"]),
                default=0.0,
            )
            self.submit_change(
                wid, "promote_experiment", cfg,
                "Promote statistically supported variants while retaining 15% monitoring traffic.",
                "Winners passed minimum sample size, practical uplift and multiplicity-adjusted significance. "
                "Residual traffic detects decay and protects against a permanent false winner.",
                evidence,
                {"method": "randomised observed traffic", "evidence": evidence},
                min(0.95, 0.4 + best_uplift * 5), affected,
            )

    def monitor_caller_ids(self) -> None:
        minute = int(self.state["sim_minute"])
        if minute < 1440:
            return
        for row in self.store.workflows():
            wid = row["workflow_id"]
            open_action = self.store.latest_action(wid, "caller_health")
            if open_action and open_action["status"] in OPEN_ACTIONS:
                continue
            rates = [r for r in caller_health(self.store, wid, minute - 240) if r.trials >= 150]
            if len(rates) < 2:
                continue
            best = max(rates, key=lambda r: r.value)
            bad = min(rates, key=lambda r: r.value)
            active = best.value - bad.value >= 0.04 and two_proportion_z(best, bad) >= 2.33
            evidence = {
                "window_minutes": 240,
                "rates": [r.__dict__ | {"rate": r.value, "wilson_95": r.wilson()} for r in rates],
                "best": best.option,
                "suspect": bad.option,
                "z": two_proportion_z(best, bad),
            }
            consecutive = self.store.set_signal(wid, f"caller_degraded:{bad.option}", active, minute, evidence)
            if not active or consecutive < 2:
                continue
            spec, cfg = loads(row["spec_json"]), copy.deepcopy(loads(row["config_json"]))
            weights = dict(distribution(cfg, "default", "caller_id"))
            if weights.get(bad.option, 0) <= 0:
                continue
            severe = bad.value <= best.value * 0.65
            # Preserve a three-DID exploration floor during ordinary load
            # balancing.  Only a severe reputation signal may remove a DID.
            if sum(1 for value in weights.values() if value > 1e-9) <= 2 and not severe:
                continue
            policy = spec["governance"]["auto_apply"]
            max_step = float(policy.get("max_shift", 0)) if policy.get("enabled") else 0.25
            removable = weights[bad.option] if severe else max(0.0, weights[bad.option] - 0.10)
            step = min(removable, max(max_step, 0.05))
            if step <= 1e-9:
                continue
            weights[bad.option] -= step
            # Spread removed traffic over the least-loaded healthy DIDs.  Moving
            # everything to the current empirical winner would overload it and
            # turn today's winner into tomorrow's degraded DID.
            recipients = sorted(
                (o for o, w in weights.items() if o != bad.option and w > 0),
                key=lambda o: (weights[o], o),
            )
            remaining = step
            while recipients and remaining > 1e-9:
                lowest = min(weights[o] for o in recipients)
                level = min(0.5, lowest + remaining / len([o for o in recipients if weights[o] == lowest]))
                changed_any = False
                for option in recipients:
                    if abs(weights[option] - lowest) < 1e-12 and remaining > 1e-9:
                        add = min(level - weights[option], remaining)
                        weights[option] += add
                        remaining -= add
                        changed_any = changed_any or add > 0
                if not changed_any:
                    break
            if remaining > 1e-9:
                # This can only happen with a single healthy DID; preserving the
                # total distribution is safer than silently dropping traffic.
                weights[best.option] = weights.get(best.option, 0) + remaining
            cfg["allocations"]["default"]["caller_id"] = {k: v for k, v in weights.items() if v > 1e-9}
            affected = int(spec["daily_batch"]["size"] * len(spec["cadence"]))
            self.submit_change(
                wid, "caller_health", cfg,
                f"Reduce traffic to degraded caller ID {bad.option} by {step:.0%}.",
                "Two consecutive population windows show a statistically significant pickup-rate deficit. "
                "Traffic moves in one policy-bounded step and will be re-evaluated.",
                evidence,
                {"method": "four-hour caller-ID comparison", "evidence": evidence},
                min(0.95, 0.55 + (best.value - bad.value)), affected, prefer_auto=True,
            )

    def monitor_hallucinations(self) -> None:
        minute = int(self.state["sim_minute"])
        for row in self.store.workflows():
            wid = row["workflow_id"]
            safe_prompt = row["safe_prompt"]
            cfg = loads(row["config_json"])
            prompt = cfg["prompt_version"]
            if prompt == safe_prompt:
                continue
            version = int(row["current_version"])
            result = self.store.rows(
                "SELECT SUM(connected) connected,SUM(hallucination_flag) flags FROM calls "
                "WHERE workflow_id=? AND config_version=? AND sim_minute>=?",
                (wid, version, minute - 240),
            )[0]
            connected, flags = int(result["connected"] or 0), int(result["flags"] or 0)
            rate = flags / connected if connected else 0.0
            evidence = {
                "config_version": version, "prompt_version": prompt, "safe_prompt": safe_prompt,
                "window_minutes": 240, "connected": connected, "flags": flags, "flag_rate": rate,
                "threshold": 0.03,
            }
            active = connected >= 120 and rate >= 0.03
            consecutive = self.store.set_signal(wid, "hallucination_rate", active, minute, evidence)
            incident = self.store.open_incident(wid, "hallucination")
            if incident:
                prior = self.store.action(incident["action_id"]) if incident["action_id"] else None
                retryable = (
                    active
                    and prior is not None
                    and prior["status"] in {"expired", "lapsed", "stale", "failed"}
                    and minute - int(prior["updated_minute"]) >= 1440
                    and not self.store.workflow_has_open_action(wid)
                    and self._can_request_approval(loads(row["spec_json"]))
                )
                if retryable:
                    self.store.audit(
                        minute, self.state["ts"], "incident_reproposal",
                        "The unsafe signal persisted after a terminal proposal; preparing fresh evidence.",
                        {"previous_action_id": prior["action_id"], "previous_status": prior["status"], **evidence},
                        wid,
                    )
                    self._respond_to_hallucination(wid, incident["incident_id"], evidence)
                continue
            if not active or consecutive < 1:
                continue
            incident_id = f"inc-{hashlib.sha256(f'{wid}|hallucination|{version}'.encode()).hexdigest()[:20]}"
            if not self.store.create_incident({
                "incident_id": incident_id, "workflow_id": wid, "kind": "hallucination",
                "detected_minute": minute, "source_version": version, "source_prompt": prompt,
                "safe_prompt": safe_prompt, "evidence": evidence,
            }):
                continue
            self.store.audit(
                minute, self.state["ts"], "incident_detected",
                f"Population hallucination signal exceeded threshold under prompt {prompt}.", evidence, wid,
            )
            self._respond_to_hallucination(wid, incident_id, evidence)

    def _respond_to_hallucination(self, wid: str, incident_id: str, evidence: dict[str, Any]) -> None:
        minute = int(self.state["sim_minute"])
        row = self.store.workflow(wid)
        spec, cfg = loads(row["spec_json"]), copy.deepcopy(loads(row["config_json"]))
        safe_prompt = row["safe_prompt"]
        suggestions: list[dict[str, Any]] = []
        try:
            suggestions = self.api.suggest_fixes(
                wid, f"Hallucination flag rate {evidence['flag_rate']:.1%} across {evidence['connected']} connected calls"
            )["suggestions"]
        except SimulatorError as exc:
            self.store.audit(
                minute, self.state["ts"], "llm_unavailable",
                "LLM suggestion failed; deterministic rollback candidate remains available.",
                {"error": exc.payload}, wid,
            )
        # The LLM proposes; it never selects the winner.  The previous safe prompt
        # is the deterministic first response to a supervisor-induced regression.
        candidate = safe_prompt
        validation = self.api.validate_prompt(wid, candidate, 600)
        self.store.save_validation(validation, minute)
        threshold = float(spec["governance"].get("min_validation_pass_rate", 0.97))
        if float(validation["pass_rate"]) < threshold:
            # Validate suggestions in stable lexical order and pick the safest one.
            validated = []
            for item in sorted(suggestions, key=lambda x: x["prompt_version"]):
                run = self.api.validate_prompt(wid, item["prompt_version"], 400)
                self.store.save_validation(run, minute)
                validated.append(run)
            viable = [r for r in validated if float(r["pass_rate"]) >= threshold]
            if not viable:
                self.store.update_incident(incident_id, status="blocked", evidence_json={
                    **evidence, "suggestions": suggestions, "validation": validation, "fallbacks": validated,
                })
                return
            validation = max(viable, key=lambda r: (float(r["pass_rate"]), r["prompt_version"]))
            candidate = validation["prompt_version"]
        cfg["prompt_version"] = candidate
        affected = int(spec["daily_batch"]["size"] * len(spec["cadence"]))
        action_id = self.submit_change(
            wid, "prompt_recovery", cfg,
            f"Restore validated prompt {candidate} after a population-level hallucination incident.",
            "Automated QA crossed a minimum-sample population threshold. The LLM supplied "
            "candidates, but deterministic policy selected a validated safe prompt; engagement remains a post-apply guardrail.",
            {"validation_run_id": validation["validation_run_id"]},
            {
                "incident": evidence, "llm_suggestions": suggestions, "validation": validation,
                "note": "validation measures safety only; engagement is monitored after apply",
            },
            0.95, affected,
        )
        if action_id:
            self.store.update_incident(incident_id, status="mitigating", action_id=action_id)

    def monitor_decay(self) -> None:
        minute = int(self.state["sim_minute"])
        for row in self.store.workflows():
            wid = row["workflow_id"]
            promotion = self.store.latest_action(wid, "promote_experiment")
            if not promotion or promotion["status"] != "applied" or minute - int(promotion["updated_minute"]) < 2880:
                continue
            existing = self.store.latest_action(wid, "decay_rollback")
            if existing and existing["status"] in OPEN_ACTIONS | {"applied"}:
                continue
            spec, cfg = loads(row["spec_json"]), copy.deepcopy(loads(row["config_json"]))
            experiment_start = self._experiment_start(wid)
            if experiment_start is None:
                continue
            changed = False
            evidence: dict[str, Any] = {}
            for dim in ("welcome_message", "voice"):
                current = distribution(cfg, "default", dim)
                winner = max(current, key=current.get)
                if current[winner] < 0.8:
                    continue
                early_rates = option_rates(
                    self.store, wid, dim, experiment_start, until_minute=experiment_start + 2 * 1440,
                    prompt_version=cfg["prompt_version"],
                )
                recent_rates = option_rates(
                    self.store, wid, dim, minute - 1440, prompt_version=cfg["prompt_version"]
                )
                early = next((r for r in early_rates if r.option == winner), None)
                recent = next((r for r in recent_rates if r.option == winner), None)
                alternatives = [r for r in recent_rates if r.option != winner and r.trials >= 50]
                if not early or not recent or early.trials < 100 or recent.trials < 100 or not alternatives:
                    continue
                alt = max(alternatives, key=lambda r: r.value)
                trend_z = two_proportion_z(early, recent)
                alternative_z = two_proportion_z(alt, recent)
                decayed = (
                    early.value - recent.value >= 0.03
                    and trend_z >= 2.0
                    and alt.value - recent.value >= 0.015
                    and alternative_z >= 1.96
                )
                evidence[dim] = {
                    "winner": winner, "early": early.__dict__ | {"rate": early.value},
                    "recent": recent.__dict__ | {"rate": recent.value},
                    "best_alternative": alt.__dict__ | {"rate": alt.value},
                    "trend_z": trend_z, "alternative_z": alternative_z, "decayed": decayed,
                }
                if decayed and alt.value > recent.value:
                    options = spec["options"][dim]
                    cfg["allocations"]["default"][dim] = {
                        o: (0.85 if o == alt.option else 0.15 / (len(options) - 1)) for o in options
                    }
                    changed = True
            if changed:
                affected = int(spec["daily_batch"]["size"] * len(spec["cadence"]))
                self.submit_change(
                    wid, "decay_rollback", cfg,
                    "Roll back a promoted variant whose rolling effect decayed.",
                    "The promoted arm fell materially and significantly against its early window; monitoring traffic "
                    "identified a better active alternative.", evidence,
                    {"method": "early-vs-rolling-window decay test", "evidence": evidence},
                    0.85, affected,
                )

    def decide(self) -> None:
        if self.state.get("finished"):
            return
        self.bootstrap_experiments()
        self.monitor_hallucinations()
        self.monitor_caller_ids()
        self.promote_experiment_winners()
        self.monitor_decay()

    def run(self) -> None:
        self.initialise()
        self.state = self.api.state()
        while not self.state.get("finished"):
            self.sync_workflows()
            self.reconcile_actions()
            self.decide()
            # Pending governance and active incidents get 15-minute reaction time;
            # otherwise hourly ticks reduce HTTP overhead while preserving useful windows.
            pending = bool(self.store.open_actions())
            incident = any(self.store.open_incident(w["workflow_id"], "hallucination") for w in self.store.workflows())
            step = 15 if pending or incident else 60
            before = int(self.state["sim_minute"])
            remaining = self.end_minute - before
            step = min(step, remaining)
            if step <= 0:
                break
            self.state = self.api.advance(step, before)
            self.ingest_available("events")
            self.store.set_meta("last_sim_minute", int(self.state["sim_minute"]))
        self.sync_workflows()
        self.reconcile_actions()
        self.state = self.api.state()
        self.store.audit(
            int(self.state["sim_minute"]), self.state["ts"], "controller_finished",
            "Scenario finished; durable audit and decision state are complete.",
            {"credits_remaining": self.state.get("credits_remaining")},
        )
        self.log("controller finished", credits_remaining=self.state.get("credits_remaining"))

