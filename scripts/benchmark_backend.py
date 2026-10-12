#!/usr/bin/env python3
"""Measure real admission/view/aggregate code against isolated synthetic snapshots.

JSON on stdout; exit 0 means measurement completed, not an SLA verdict. No HTTP,
provider, SSH, or production state is accessed. Cases run serially because they
share the disk and process validation cache; concurrency would pollute timings.
Set LLM_GATEWAY_ROOT to the independent Gateway source checkout before running;
the isolated HOME intentionally has no default checkout.
"""

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import platform
import sqlite3
import sys
import tempfile
import time
from unittest import mock


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def nonnegative(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be nonnegative")
    return number


def clear_validation_cache(generation):
    # This is application-cold, never a claim about the OS page cache.
    with generation._READ_VALIDATIONS_LOCK:
        generation._READ_VALIDATIONS.clear()


def measure(generation, operation):
    digest = generation._file_digest
    digest_bytes = 0

    def counted_digest(path):
        nonlocal digest_bytes
        digest_bytes += Path(path).stat().st_size
        return digest(path)

    with mock.patch.object(generation, "_file_digest", side_effect=counted_digest) as hashes, \
            mock.patch.object(generation, "_validate_generation", wraps=generation._validate_generation) as validations, \
            mock.patch.object(generation, "generation_admission_snapshot",
                              wraps=generation.generation_admission_snapshot) as admissions:
        started = time.perf_counter()
        value = operation()
        elapsed = time.perf_counter() - started
    return {"seconds": elapsed, "admission_calls": admissions.call_count,
            "validation_calls": validations.call_count, "digest_calls": hashes.call_count,
            "digest_bytes": digest_bytes, "input_tokens": value}


def make_snapshot(path, rows, padding_mib, tokens, rollup):
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executescript(rollup.SCHEMA)
        conn.executemany("INSERT INTO daily_rollup "
                         "(date,agent_id,project,model,input_tokens,entry_count,message_count) "
                         "VALUES ('2026-08-04','codex',?,?,?,1,1)",
                         (("project-%d" % i, "model-%d" % i, tokens) for i in range(rows)))
        conn.execute("INSERT INTO daily_usage_rollup "
                     "(date,agent_id,model,input_tokens,entry_count,message_count) "
                     "SELECT date,agent_id,model,input_tokens,entry_count,message_count FROM daily_rollup")
        conn.executemany("INSERT INTO rollup_meta VALUES (?,?)", [
            ("bucket_timezone", "Asia/Shanghai"), ("usage_rollup_schema", "2"),
            (rollup.USAGE_ROLLUP_AUTHORITY_FLOOR_KEY, "2026-08-04")])
        # Payload volume is independent of aggregate cardinality. This is not a
        # synthetic claim about real request/session history or its retention.
        conn.execute("CREATE TABLE benchmark_padding (payload BLOB)")
        for _ in range(padding_mib):
            conn.execute("INSERT INTO benchmark_padding VALUES (zeroblob(1048576))")


def run_case(path, rows, padding_mib, repeat):
    import generation
    import query_view
    import rollup
    from machine_config import Machine, machine_config_fingerprint

    path.mkdir()
    root, config = path / "generations", path / "machines.json"
    machine = Machine("synthetic", "synthetic", True)
    config.write_text(json.dumps({"machines": [machine.as_config_dict()], "retired_names": []}))

    def publish(index):
        snapshot = path / ("source-%d.db" % index)
        make_snapshot(snapshot, rows, padding_mib, index, rollup)
        meta = generation.build_generation_meta(snapshot,
            machine_config_fingerprint=machine_config_fingerprint(machine),
            source_host_identity="host-v1:" + "5" * 64, aliases=[], rate_limits={},
            exporter_commit="a" * 40, generated_at="2026-08-04T12:0%d:00Z" % index)
        current = generation.publish_generation(machine.name, snapshot, meta, root=root)
        current.close()
        return snapshot.stat().st_size

    def query(context):
        with context as admission:
            if len(admission.admitted) != 1:
                raise RuntimeError("synthetic generation was not admitted")
            with rollup.use_admitted_generations(admission.admitted):
                return rollup.query_grouped([], "input")["rows"][0]["value"]

    def admitted_query():
        return query(generation.generation_admission_snapshot(config_path=config, root=root))

    snapshot_bytes = publish(1)
    phases = {name: [] for name in ("cold_admission_query", "hot_admission_query",
              "cold_prepare", "prepared_query", "new_generation_prepare", "new_generation_query")}
    view = query_view.QueryView(config_path=config, root=root)
    try:
        for _ in range(repeat):
            clear_validation_cache(generation)
            phases["cold_admission_query"].append(measure(generation, admitted_query))
            phases["hot_admission_query"].append(measure(generation, admitted_query))
            clear_validation_cache(generation)
            phases["cold_prepare"].append(measure(generation, view.refresh))
            phases["prepared_query"].append(measure(generation, lambda: query(view.snapshot())))
        new_snapshot_bytes = publish(2)
        # Publish also validates. Clear its process cache to model a new view
        # preparing a generation published by the independent sync worker.
        clear_validation_cache(generation)
        phases["new_generation_prepare"].append(measure(generation, view.refresh))
        for _ in range(repeat):
            phases["new_generation_query"].append(measure(generation, lambda: query(view.snapshot())))
    finally:
        view.close()
    return {"aggregate_rows": rows, "padding_mib": padding_mib,
            "snapshot_bytes": snapshot_bytes, "new_generation_snapshot_bytes": new_snapshot_bytes,
            "phases": phases}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", nargs="+", type=positive, default=[100, 1000])
    parser.add_argument("--padding-mib", nargs="+", type=nonnegative, default=[0, 4])
    parser.add_argument("--repeat", type=positive, default=3)
    args = parser.parse_args()
    # Set HOME before importing project modules, and pass explicit state paths.
    # The CLI is its own process, so no caller environment needs restoring.
    with tempfile.TemporaryDirectory(prefix="agent-monitor-benchmark-") as temporary:
        path = Path(temporary)
        home = path / "home"
        home.mkdir()
        os.environ["HOME"] = str(home)
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        cases = []
        for rows in args.rows:
            for padding in args.padding_mib:
                cases.append(run_case(path / ("case-%d" % len(cases)), rows, padding, args.repeat))
        report = {"scope": "synthetic admission + QueryView + grouped aggregate, one machine, schema v2",
                  "cold_definition": "application validation cache cleared; OS page cache uncontrolled",
                  "limitations": "no HTTP/browser/network/provider, full-history retention, or production latency verdict",
                  "environment": {"python": platform.python_version(), "platform": platform.platform(),
                                  "sqlite": sqlite3.sqlite_version},
                  "cases": cases}
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
