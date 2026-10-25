"""Counter expectations come from persisted IDs and actual writer row sets."""
import json
import logging
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import diagnostics
import rollup


class OperationalMetricsTests(unittest.TestCase):
    def test_dropped_events_exclude_completed_emits_and_snapshot_failures(self):
        for fault in (None, 0, 1, "snapshot"):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as directory:
                monitor = diagnostics.Monitor(slow_seconds=0)
                monitor.sources_error = None
                monitor.path = Path(directory)
                target = monitor.path / "events.jsonl"

                class Handler(logging.FileHandler):
                    def emit(self, record):
                        if fault in (0, 1) and len(target.read_text().splitlines()) == fault:
                            raise OSError("controlled emit failure")
                        super().emit(record)

                monitor.event_handler = Handler(target)
                for name in ("first", "second"):
                    with monitor.operation(name):
                        pass
                write = Path.write_text

                def write_snapshot(path, *args, **kwargs):
                    if fault == "snapshot" and path.name.startswith("current."):
                        raise OSError("controlled snapshot failure")
                    return write(path, *args, **kwargs)

                try:
                    with patch.object(Path, "write_text", write_snapshot):
                        monitor.tick()
                    persisted = [json.loads(line)["id"] for line in target.read_text().splitlines()]
                    self.assertEqual(monitor.snapshot()["coverage"]["events_dropped"],
                                     len({1, 2} - set(persisted)))
                finally:
                    monitor.close()

    def test_orphan_disposition_uses_writer_authority_and_project_scope(self):
        bucket = {name: 1 for name in rollup.BUCKET_FIELDS}
        for dimensions in (3, 4):
            for authority, minimum, projects, expected in (
                    (None, None, None, "preserve"),
                    ("2026-10-01", None, None, "remove"),
                    ("2026-10-01", "2026-10-15", None, "preserve"),
                    ("2026-10-01", None, set(), "preserve")):
                key = ("2026-10-14", "codex", "old-model") if dimensions == 3 else (
                    "2026-10-14", "codex", "project", "old-model")
                with self.subTest(dimensions=dimensions, authority=authority, minimum=minimum, projects=projects):
                    result = rollup._compare_bucket_sets(
                        [{"key": key, "values": bucket}], {}, {key[0]},
                        minimum_date=minimum, history_authoritative_from=authority,
                        history_authoritative_projects=projects)
                    self.assertEqual(result["orphan"][0]["action"], expected)
