"""Synthetic legacy rollup snapshot shared by generation and view tests."""
from contextlib import closing
import sqlite3

from agent_monitor.accounting import rollup
from agent_monitor.snapshots import generation


def make_rollup_snapshot(path, input_tokens):
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executescript(rollup.SCHEMA)
        conn.execute("DROP TABLE daily_usage_rollup")
        conn.execute(
            """
                INSERT INTO daily_rollup (
                  date, agent_id, project, model, input_tokens, output_tokens,
                  cache_creation_tokens, cache_read_tokens, cost_usd,
                  cost_known_count, entry_count, message_count
                ) VALUES ('2026-08-04', 'codex', 'repo', 'gpt-5', ?, 2, 3, 4, 1.5, 1, 1, 1)
                """,
            (input_tokens,),
        )
        conn.execute(
            "INSERT INTO rollup_meta (key, value) VALUES ('bucket_timezone', 'Asia/Shanghai')"
        )
    return path


def make_generation_meta(snapshot, generated_at, *, machine_config_fingerprint="f" * 64):
    return generation.build_generation_meta(
        snapshot,
        machine_config_fingerprint=machine_config_fingerprint,
        source_host_identity="host-v1:" + "5" * 64,
        aliases=[],
        rate_limits={},
        exporter_commit="a" * 40,
        generated_at=generated_at,
    )
