#!/usr/bin/env python3
"""Offline synthetic collection + export measurements, not a production verdict."""

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import resource
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 4, 15, 12, tzinfo=timezone.utc)
PRICES = {"gpt-5": {"input_cost_per_token": 0.000001,
                     "output_cost_per_token": 0.000002, "source": "synthetic-fixture"}}


def positive(value):
    value = int(value)
    if value < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return value


def source_identity():
    # Include dirty candidates; a Git HEAD alone does not identify executed code.
    paths = sorted(set(ROOT.glob("*.py")) | set((ROOT / "parsers").rglob("*.py")) |
                   set((ROOT / "agent_monitor").rglob("*.py")) |
                   set((ROOT / "scripts").glob("benchmark*.py")))
    paths += [ROOT / "requirements.txt", ROOT / "agent_monitor/accounting/pricing.json"]
    files = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in paths if p.is_file()}
    return {"files": files, "sha256": hashlib.sha256(
        json.dumps(files, sort_keys=True).encode()).hexdigest()}


def event(index):
    return {"timestamp": NOW.isoformat(), "type": "event_msg", "payload": {
        "type": "token_count", "info": {"total_token_usage": {
            "input_tokens": index * 100, "output_tokens": index * 2,
            "cached_input_tokens": 0}}}}


def measured(operation):
    wall, cpu = time.perf_counter(), cpu_time()
    value = operation()
    return value, {"wall_seconds": time.perf_counter() - wall,
                   "cpu_seconds": cpu_time() - cpu}


def cpu_time():
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    return time.process_time() + children.ru_utime + children.ru_stime


def run_case(path, rows, repeat, pipeline="export"):
    # Called only after isolated HOME is installed, before any project import.
    from agent_monitor.ingestion import ProjectIdentityStore, UsageLoader
    from parsers import codex
    from agent_monitor.snapshots import exporter
    from agent_monitor.snapshots import statistics_snapshot
    from agent_monitor.accounting import pricing_fetcher, rollup, usage_projection
    from agent_monitor.ingestion import usage_archive

    sessions = path / "sessions"
    sessions.mkdir()
    metadata = path / "metadata.sqlite3"
    counts = {}
    with sqlite3.connect(metadata) as conn:
        conn.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, model TEXT)")
        for index in range(min(rows, 10)):
            name = "session-%d" % index
            count = rows // min(rows, 10) + (index < rows % min(rows, 10))
            counts[name] = count
            conn.execute("INSERT INTO threads VALUES (?, 'gpt-5')", (name,))
            records = [{"type": "session_meta", "payload": {
                "id": name, "cwd": str(path / "project"), "model": "gpt-5"}}]
            records.extend(event(i) for i in range(1, count + 1))
            (sessions / (name + ".jsonl")).write_text(
                "".join(json.dumps(row) + "\n" for row in records))

    popen = subprocess.Popen
    def local_git_only(command, *args, **kwargs):
        if command != ["git", "-C", str(path / "project"), "config", "--get", "remote.origin.url"]:
            raise AssertionError("unexpected child process: %r" % command)
        return popen(command, *args, **kwargs)

    loader = UsageLoader(ProjectIdentityStore(path / "identity.db"))
    with ExitStack() as stack:
        for patch in (
            mock.patch.object(loader, "usage_paths", side_effect=lambda **kw: sorted(sessions.glob("*.jsonl"))),
            mock.patch.object(codex, "SESSIONS_DIR", str(sessions)),
            mock.patch.object(codex, "STATE_DB", str(metadata)),
            mock.patch("agent_monitor.accounting.pricing_fetcher.get_pricing", return_value=PRICES),
            # Synthetic manifest identity; report actual source hashes separately.
            mock.patch.object(exporter, "exporter_version", return_value="a" * 40),
            mock.patch.object(exporter, "_rate_limits", side_effect=AssertionError("quota access")),
            mock.patch.object(socket.socket, "connect", side_effect=AssertionError("network access")),
            mock.patch.object(socket.socket, "connect_ex", side_effect=AssertionError("network access")),
            mock.patch.object(subprocess, "Popen", side_effect=local_git_only),
        ):
            stack.enter_context(patch)
        parsed = stack.enter_context(mock.patch.object(
            loader, "parse_usage_file", wraps=loader.parse_usage_file))
        work = {"decoded": 0, "priced": 0}
        def counted(function, key):
            def call(*args, **kwargs):
                work[key] += 1
                return function(*args, **kwargs)
            return call
        stack.enter_context(mock.patch.object(usage_archive, "_decode", counted(usage_archive._decode, "decoded")))
        stack.enter_context(mock.patch.object(usage_projection, "_decode", counted(usage_projection._decode, "decoded")))
        stack.enter_context(mock.patch.object(pricing_fetcher, "with_calculated_cost", counted(pricing_fetcher.with_calculated_cost, "priced")))
        samples = []
        expected = rows
        for state in ["initial"] + [s for _ in range(repeat) for s in ("unchanged", "append")]:
            if state == "append":
                name = "session-0"
                counts[name] += 1
                with (sessions / (name + ".jsonl")).open("a") as f:
                    f.write(json.dumps(event(counts[name])) + "\n")
                expected += 1
            parsed.reset_mock()
            work.update(decoded=0, priced=0)
            output = path / "export"
            start_wall, start_cpu = time.perf_counter(), cpu_time()
            loaded, collect = measured(loader.load_rollup_entries if pipeline == "rollup" else loader.load_all_entries)
            if pipeline == "rollup":
                _, export = measured(lambda: rollup.run(
                    db_path=path / "rollup.db", entries_loader=lambda: loaded, now=lambda: NOW))
            else:
                _, export = measured(lambda: exporter.export_bundle(
                    db_path=path / "rollup.db", output_path=output,
                    entries_loader=lambda: loaded, generated_at=NOW.isoformat(),
                    rollup_now=lambda: NOW, source_host_identity="host-v1:" + "5" * 64,
                    rate_limits={}, quota_refresh=False))
            total = {"wall_seconds": time.perf_counter() - start_wall,
                     "cpu_seconds": cpu_time() - start_cpu}
            result_db = path / "rollup.db" if pipeline == "rollup" else output / "snapshot.db"
            values = None if pipeline == "rollup" else statistics_snapshot.read_snapshot(result_db)["usage_entries"]
            with sqlite3.connect(result_db) as conn:
                tokens, outputs = conn.execute("SELECT SUM(input_tokens), SUM(output_tokens) FROM daily_usage_rollup").fetchone()
                entries, cost, known = conn.execute("SELECT SUM(entry_count), SUM(cost_usd), SUM(cost_known_count) FROM daily_usage_rollup").fetchone()
            if ((values is not None and (len(values) != expected or sum(e.input_tokens for e in values) != expected * 100
                    or sum(e.output_tokens for e in values) != expected * 2
                    or any(e.cache_read_tokens or e.cache_creation_tokens or e.cache_creation_1h_tokens for e in values)
                    or any(e.cost_usd is None or not math.isclose(e.cost_usd, 0.000104, rel_tol=1e-9)
                           or e.cost_provenance["basis"] != "nominal" for e in values)))
                    or tokens != expected * 100 or outputs != expected * 2
                    or entries != expected or known != expected
                    or not math.isclose(cost, expected * 0.000104, rel_tol=1e-9)
                    or not loaded.scan_complete or loaded.source_errors):
                raise AssertionError("synthetic collection/export accounting mismatch")
            samples.append({"state": state, "entries": expected,
                            "input_tokens": tokens, "output_tokens": outputs,
                            "parsed_files": parsed.call_count,
                            "archive_decoded_entries": work["decoded"],
                            "priced_entries": work["priced"],
                            "collection": collect, pipeline: export, "total": total,
                            "archive_bytes": (path / "usage_archive.sqlite3").stat().st_size,
                            "snapshot_bytes": result_db.stat().st_size})
            if output.exists():
                shutil.rmtree(output)
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {"initial_entries": rows, "source_files": len(counts), "samples": samples,
            "process_peak_rss_bytes": peak * (1 if sys.platform == "darwin" else 1024)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", nargs="+", type=positive, default=[100, 1000])
    parser.add_argument("--repeat", type=positive, default=2)
    parser.add_argument("--pipeline", choices=("export", "rollup"), default="export",
                        help="measure complete export or scheduled collection/rollup")
    parser.add_argument("--timeout", type=positive, default=120, help="seconds per case (default: 120)")
    parser.add_argument("--json", action="store_true", help="machine-readable raw measurements")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--case-root", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        if args.case_root is None:
            parser.error("internal worker requires a case root")
        path = args.case_root
        (path / "home").mkdir()
        os.environ["HOME"] = str(path / "home")
        sys.path.insert(0, str(ROOT))
        print(json.dumps(run_case(path, args.rows[0], args.repeat, args.pipeline)))
        return
    identity = source_identity()
    report = {"scope": "synthetic local collection + " + args.pipeline + ", one process per case",
              "limitations": "excludes imports/setup, price/quota providers, SSH, scheduling, HTTP and production workloads; peak RSS is worker-only, includes setup/readback and is not a phase delta; OS cache uncontrolled",
              "timing": "total spans collection start through completed rollup or published export; phases do not overlap; CPU includes worker and reaped children (local git identity lookup only)",
              "fixture": "codex cumulative token events v1; each event adds 100 input and 2 output tokens, zero cache tokens; synthetic nominal cost 0.000104 USD/event; append adds one event to one source",
              "environment": {"python": platform.python_version(), "platform": platform.platform(), "sqlite": sqlite3.sqlite_version},
              "source": identity, "cases": [], "status": "completed"}
    for rows in args.rows:
        # Parent owns cleanup even when the bounded worker is killed on timeout.
        with tempfile.TemporaryDirectory(prefix="agent-monitor-background-") as temp:
            try:
                child = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker",
                                        "--case-root", temp, "--rows", str(rows),
                                        "--repeat", str(args.repeat), "--pipeline", args.pipeline],
                                       capture_output=True, text=True, timeout=args.timeout)
                if child.returncode:
                    raise RuntimeError(child.stderr or "worker returned %d" % child.returncode)
                report["cases"].append(json.loads(child.stdout))
            except (subprocess.TimeoutExpired, RuntimeError, ValueError) as exc:
                report.update(status="failed", failure={"initial_entries": rows, "reason": str(exc)})
                break
    if identity != source_identity():
        report.update(status="failed", failure={"reason": "Source changed during measurement; rerun on a stable checkout."})
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print("Synthetic collection/export measurements %s; no production or SLA verdict." % report["status"])
        print("Entries  State       CPU seconds  Wall seconds  Parsed files")
        for case in report["cases"]:
            for sample in case["samples"]:
                print("%7d  %-10s  %11.6f  %12.6f  %d" % (
                    sample["entries"], sample["state"], sample["total"]["cpu_seconds"],
                    sample["total"]["wall_seconds"], sample["parsed_files"]))
        print(report["limitations"])
        print("Use --json for phase samples, source hashes and environment; retain output outside temporary directories.")
        if report["status"] == "failed":
            print("Failure: " + report["failure"]["reason"], file=sys.stderr)
    if report["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
