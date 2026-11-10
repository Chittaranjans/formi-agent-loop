#!/usr/bin/env python
"""Run baseline + controller + grade for a public scenario (local eval helper)."""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="scenarios/public_01_dev.yaml")
    ap.add_argument("--name", default=None)
    ap.add_argument("--port", type=int, default=8700)
    ap.add_argument("--skip-baseline", action="store_true")
    args = ap.parse_args()
    name = args.name or Path(args.scenario).stem
    base_dir = ROOT / "runs" / f"base_{name}"
    mine_dir = ROOT / "runs" / f"mine_{name}"
    data_dir = ROOT / "controller_data" / f"mine_{name}"
    reports = ROOT / "reports"
    reports.mkdir(exist_ok=True)

    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")

    if not args.skip_baseline:
        if base_dir.exists():
            shutil.rmtree(base_dir)
        print("=== baseline ===", flush=True)
        subprocess.check_call(
            [sys.executable, "-m", "sim", "baseline", "--scenario", args.scenario, "--run-dir", str(base_dir)],
            cwd=ROOT,
            env=env,
        )

    if mine_dir.exists():
        shutil.rmtree(mine_dir)
    if data_dir.exists():
        shutil.rmtree(data_dir)
    data_dir.mkdir(parents=True)

    print("=== serve ===", flush=True)
    serve = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "sim",
            "serve",
            "--scenario",
            args.scenario,
            "--run-dir",
            str(mine_dir),
            "--port",
            str(args.port),
            "--force",
        ],
        cwd=ROOT,
        env=env,
    )
    try:
        time.sleep(3)
        url = f"http://127.0.0.1:{args.port}"
        print("=== controller ===", flush=True)
        t0 = time.time()
        subprocess.check_call(
            [sys.executable, "-m", "controller", "run", "--url", url, "--data-dir", str(data_dir)],
            cwd=ROOT,
            env=env,
        )
        wall = time.time() - t0
        print(f"controller wall-clock: {wall:.1f}s", flush=True)
        (reports / f"{name}_wallclock.txt").write_text(f"{wall:.1f}\n")
    finally:
        serve.terminate()
        try:
            serve.wait(timeout=10)
        except Exception:
            serve.kill()

    print("=== grade ===", flush=True)
    out = reports / f"{name}.md"
    # Import grader directly so we can write UTF-8 on Windows (sim CLI uses locale encoding).
    sys.path.insert(0, str(ROOT))
    from sim.grader import grade, to_markdown

    g = grade(str(mine_dir), str(base_dir))
    md = to_markdown(g)
    out.write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
