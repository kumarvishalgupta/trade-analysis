#!/usr/bin/env python3
"""
Pipeline Orchestrator — Email → PDFs → CSV → enriched JSON → built dashboard.

Steps:
    1. Fetch Dhan PDFs from Gmail (skip with --skip-fetch)
    2. Parse PDFs → TradesFromPDFs.csv
    3. Run fetch_and_analyze.py → dashboard/public/enriched_trades.json
    4. Build the React dashboard → dashboard/dist/

Usage:
    python pipeline.py
    python pipeline.py --skip-fetch              # parse + analyze + build only
    python pipeline.py --skip-build               # no npm build
    python pipeline.py --since 2026-03-10        # filter Gmail by date
    python pipeline.py --base /trade-analysis/   # override Vite base path

Env (from .env):
    GMAIL_ADDRESS, GMAIL_APP_PASSWORD, PDF_PASSWORD, BROKER, VITE_BASE
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent
TRADES_DIR = ROOT / "Trades log"
CSV_PATH = ROOT / "TradesFromPDFs.csv"
ANALYZE_SCRIPT = ROOT / "fetch_and_analyze.py"
ENRICHED_JSON = ROOT / "dashboard" / "public" / "enriched_trades.json"
DASHBOARD_DIR = ROOT / "dashboard"


def _load_dotenv_lazy() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def _section(title: str) -> None:
    print()
    print("─" * 72)
    print(f"  {title}")
    print("─" * 72)


def _run(cmd: list[str], *, cwd: Optional[Path] = None, env: Optional[dict] = None) -> int:
    print(f"$ {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=str(cwd or ROOT), env=env)
    return proc.returncode


def step_fetch(since: Optional[str]) -> int:
    _section("Step 1/4 — Fetch new Dhan PDFs from Gmail")
    cmd = [sys.executable, str(ROOT / "gmail_fetcher.py"), "--out", str(TRADES_DIR)]
    if since:
        cmd += ["--since", since]
    rc = _run(cmd)
    if rc != 0:
        print(f"WARN: gmail_fetcher.py exited with {rc}; continuing with existing PDFs.")
    return rc


def step_parse() -> int:
    _section("Step 2/4 — Parse PDFs → TradesFromPDFs.csv")
    rc = _run([
        sys.executable,
        str(ROOT / "pdf_parser.py"),
        "--in", str(TRADES_DIR),
        "--out", str(CSV_PATH),
    ])
    return rc


def step_analyze() -> int:
    _section("Step 3/4 — Run fetch_and_analyze.py")
    if not CSV_PATH.exists():
        print(f"ERROR: {CSV_PATH} missing; cannot analyze.", file=sys.stderr)
        return 1
    rc = _run([sys.executable, str(ANALYZE_SCRIPT)])
    if rc != 0:
        print("ERROR: fetch_and_analyze.py failed.", file=sys.stderr)
    elif not ENRICHED_JSON.exists():
        print(f"WARN: expected output {ENRICHED_JSON} not found.", file=sys.stderr)
    return rc


def step_build(base: Optional[str]) -> int:
    _section("Step 4/4 — Build the React dashboard")
    if not (DASHBOARD_DIR / "package.json").exists():
        print(f"ERROR: {DASHBOARD_DIR}/package.json missing.", file=sys.stderr)
        return 1
    npm = shutil.which("npm")
    if not npm:
        print("ERROR: npm is not on PATH; install Node.js 20+.", file=sys.stderr)
        return 1

    env = os.environ.copy()
    if base:
        env["VITE_BASE"] = base

    if not (DASHBOARD_DIR / "node_modules").exists():
        rc = _run([npm, "ci"], cwd=DASHBOARD_DIR, env=env)
        if rc != 0:
            return rc
    return _run([npm, "run", "build"], cwd=DASHBOARD_DIR, env=env)


def _summary(t0: float, rc_per_step: dict[str, int]) -> None:
    _section("Summary")
    elapsed = time.time() - t0
    for label, rc in rc_per_step.items():
        flag = "OK" if rc == 0 else f"FAIL({rc})"
        print(f"  {label:24} {flag}")
    print(f"  Total time              {elapsed:.1f}s")
    if all(rc == 0 for rc in rc_per_step.values()):
        if ENRICHED_JSON.exists():
            print(f"\n  Dashboard data:  {ENRICHED_JSON.relative_to(ROOT)}")
        if (DASHBOARD_DIR / "dist").exists():
            print(f"  Built site:      {(DASHBOARD_DIR / 'dist').relative_to(ROOT)}")
        print("\n  Ready to deploy. (GitHub Actions will push dist/ to gh-pages.)")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-fetch", action="store_true", help="Skip Gmail fetch step")
    parser.add_argument("--skip-parse", action="store_true", help="Skip PDF parse step")
    parser.add_argument("--skip-analyze", action="store_true", help="Skip fetch_and_analyze step")
    parser.add_argument("--skip-build", action="store_true", help="Skip npm build step")
    parser.add_argument("--since", default=None, help="Only fetch Gmail emails on/after YYYY-MM-DD")
    parser.add_argument("--base", default=None,
                        help="Override Vite base path (default: $VITE_BASE or '/trade-analysis/')")
    args = parser.parse_args(argv)

    _load_dotenv_lazy()
    t0 = time.time()
    print(f"Pipeline starting at {datetime.now():%Y-%m-%d %H:%M:%S}")

    results: dict[str, int] = {}

    if not args.skip_fetch:
        results["1. Gmail fetch"] = step_fetch(args.since)
    else:
        print("(skipping Gmail fetch)")

    if not args.skip_parse:
        results["2. PDF parse"] = step_parse()
        if results["2. PDF parse"] != 0:
            _summary(t0, results)
            return results["2. PDF parse"]
    else:
        print("(skipping PDF parse)")

    if not args.skip_analyze:
        results["3. Analyze"] = step_analyze()
        if results["3. Analyze"] != 0:
            _summary(t0, results)
            return results["3. Analyze"]
    else:
        print("(skipping analyze)")

    if not args.skip_build:
        results["4. npm build"] = step_build(args.base)
    else:
        print("(skipping npm build)")

    _summary(t0, results)
    return 0 if all(rc == 0 for rc in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
