"""CLI entrypoints for the controller and audit queries."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .loop import run_controller
from .store import Store


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="controller", description="Formi outcome-first agent loop")
    sub = p.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="Run the controller until the simulator finishes")
    run_p.add_argument("--url", default=os.environ.get("SIM_URL", "http://127.0.0.1:8700"))
    run_p.add_argument(
        "--data-dir",
        default=os.environ.get("CONTROLLER_DATA", str(Path("controller_data") / "default")),
    )

    why_p = sub.add_parser("why", help="Answer: why did workflow X change?")
    why_p.add_argument("workflow_id")
    why_p.add_argument("--data-dir", required=True)
    why_p.add_argument("--limit", type=int, default=30)

    view_p = sub.add_parser("view", help="Consolidated supervisor view for a workflow")
    view_p.add_argument("workflow_id")
    view_p.add_argument("--data-dir", required=True)

    args = p.parse_args(argv)

    if args.cmd == "run":
        run_controller(args.url, args.data_dir)
        return 0

    store = Store(Path(args.data_dir) / "controller.sqlite")
    if args.cmd == "why":
        rows = store.query_audit(workflow_id=args.workflow_id, limit=args.limit)
        interesting = [
            r
            for r in rows
            if r["kind"]
            in {
                "proposal_submitted",
                "apply",
                "apply_on_reconcile",
                "auto_apply_did",
                "auto_apply_schedule",
                "auto_apply_promote",
                "auto_apply_decay",
                "config_changed",
                "halluc_detected",
                "decay_detected",
                "guardrail_blocked",
                "approval_decided_event",
                "proposal_terminal",
            }
        ]
        print(json.dumps(interesting, indent=2))
        return 0

    if args.cmd == "view":
        cohorts = store.get_cohorts(args.workflow_id) or []
        exp = store.get_experiment(args.workflow_id)
        pending = store.all_pending().get(args.workflow_id)
        recent = store.query_audit(workflow_id=args.workflow_id, limit=15)
        from . import cohorts as C

        view = C.summarize_supervisor_view(
            args.workflow_id,
            cohorts,
            exp,
            pending,
            recent,
            {"note": "open controller.why for full audit"},
        )
        print(json.dumps(view, indent=2))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
