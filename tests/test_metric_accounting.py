import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from datetime import datetime, timezone

from parsers import claude, codex, UsageEntry
from usage_archive import UsageArchive


def usage(n):
    return {"input_tokens": n, "output_tokens": n // 10, "cached_input_tokens": n // 2}


def meta(identity, base=None):
    payload = {"id": identity, "cwd": "/project"}
    if base is not None:
        payload.update(history_mode="paginated", history_base=base)
    return {"type": "session_meta", "payload": payload}


def count(n):
    return {"timestamp": "2026-10-09T12:00:00Z", "type": "event_msg", "payload": {
        "type": "token_count", "info": {"total_token_usage": usage(n)}}}


class PaginationAccountingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write(self, name, rows):
        p = self.root / (name + ".jsonl")
        p.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
        return p

    def base(self, identity, path):
        return {"thread_id": identity, "end_byte_offset": path.stat().st_size}

    def parse(self, p, errors=None):
        return codex.parse_file(p, source_errors=errors)

    def test_split_and_join_preserve_actual_usage(self):
        parent = self.write("parent", [meta("p"), count(1000)])
        child = self.write("child", [meta("c", self.base("p", parent)), count(1200), count(1500)])
        joined = self.write("joined", [meta("joined"), count(1000), count(1200), count(1500)])
        total = lambda es: sum(e.input_tokens + e.cache_read_tokens + e.output_tokens for e in es)
        self.assertEqual(total(self.parse(parent) + self.parse(child)), total(self.parse(joined)))
        self.assertEqual(total(self.parse(child)), 550)

    def test_physical_thread_identity_differs_from_logical_session(self):
        first = "01a133e1-5e82-76e3-8053-7068b190bbff"
        second = "01a133f2-e9d3-7f83-8501-f92db642c319"
        parent = self.write("rollout-" + first, [meta(first), count(1000)])
        middle = self.write("rollout-" + first + "_" + second, [meta(first, self.base(first, parent)), count(1200)])
        child = self.write("child", [meta(first, self.base(second, middle)), count(1500)])
        self.assertEqual(self.parse(child)[0].input_tokens, 150)

    def test_replayed_boundary_keeps_zero_identity(self):
        parent = self.write("parent", [meta("p"), count(1000)])
        child = self.write("child", [meta("c", self.base("p", parent)), count(1000), count(1200)])
        entries = self.parse(child)
        self.assertEqual([e.input_tokens for e in entries], [0, 100])
        self.assertEqual([e.message_count for e in entries], [0, 1])
        self.assertEqual(entries[0].request_id, "token-count:2")

    def test_exact_byte_cutoff_and_empty_intermediate_page(self):
        parent = self.write("parent", [meta("p"), {"type": "note", "text": "中文"}, count(1000)])
        boundary = self.base("p", parent)
        with parent.open("a") as f:
            f.write(json.dumps(count(4000)) + "\n")
        middle = self.write("middle", [meta("m", boundary)])
        child = self.write("child", [meta("c", self.base("m", middle)), count(1200)])
        self.assertEqual(self.parse(child)[0].input_tokens, 100)

    def test_missing_parent_is_visible_but_later_difference_survives(self):
        child = self.write("child", [meta("c", {"thread_id": "missing", "end_byte_offset": 12}), count(1200), count(1500)])
        errors = []
        entries = self.parse(child, errors)
        self.assertTrue(errors)
        self.assertEqual([e.input_tokens for e in entries], [150])
        self.assertEqual(entries[0].request_id, "token-count:3")

    def test_parent_non_object_record_does_not_discard_child_deltas(self):
        parent = self.write("parent", [meta("p"), [], count(1000)])
        child = self.write("child", [meta("c", self.base("p", parent)), count(1200)])
        self.assertEqual(self.parse(child)[0].input_tokens, 100)

    def test_signature_does_not_decode_parent_usage_body(self):
        parent = self.write("parent", [meta("p"), count(1000)])
        child = self.write("child", [meta("c", self.base("p", parent)), count(1200)])
        decoded_counts = []
        original = json.loads
        def observe(value, *args, **kwargs):
            result = original(value, *args, **kwargs)
            if isinstance(result, dict) and result.get("type") == "event_msg":
                decoded_counts.append(result)
            return result
        history = codex.HistoryResolver([parent, child])
        with mock.patch.object(codex.json, "loads", side_effect=observe):
            self.assertIsNotNone(history.signature(child))
        self.assertEqual(decoded_counts, [])

    def test_fork_parent_with_counter_has_cacheable_signature(self):
        owner = meta("p")
        owner["payload"]["forked_from_id"] = "ancestor"
        parent = self.write("parent", [owner, count(1000)])
        child = self.write("child", [meta("c", self.base("p", parent)), count(1200)])
        history = codex.HistoryResolver([parent, child])
        self.assertIsNotNone(history.signature(child))
        self.assertEqual(self.parse(child)[0].input_tokens, 100)

    def test_cycle_is_visible(self):
        child = self.write("child", [meta("c", {"thread_id": "c", "end_byte_offset": 1}), count(1200)])
        errors = []
        self.assertEqual(self.parse(child, errors), [])
        self.assertTrue(errors)


class ClaudeObservationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def row(self, output):
        return {"type": "assistant", "timestamp": "2026-10-09T12:00:00Z", "sessionId": "s", "requestId": "r", "message": {
            "id": "m", "model": "claude-opus-5-5", "usage": {"input_tokens": 2, "output_tokens": output, "cache_creation_input_tokens": 40955}}}

    def write(self, name, outputs):
        p = self.root / name
        p.write_text("".join(json.dumps(self.row(n)) + "\n" for n in outputs))
        return p

    def test_streaming_growth_and_repeated_completed_copy(self):
        for outputs in ([5, 164], [164, 5], [164, 164]):
            with self.subTest(outputs=outputs):
                entries = claude.parse_file(self.write("one.jsonl", outputs))
                self.assertEqual(len(entries), 1)
                self.assertEqual(entries[0].output_tokens, 164)

    def test_archive_keeps_complete_observation_after_copy_disappears(self):
        early = self.write("a.jsonl", [5])
        complete = self.write("b.jsonl", [164])
        archive = UsageArchive(self.root / "archive.sqlite3")
        errors = []
        def collect(paths):
            return archive.collect(paths, lambda p: claude.parse_file(p, source_errors=errors), lambda p: str(p.stat().st_mtime_ns), errors, timezone.utc)[0]
        self.assertEqual(collect([early, complete])[0].output_tokens, 164)
        complete.unlink()
        self.assertEqual(collect([early])[0].output_tokens, 164)

    def test_incomparable_usage_is_reported_without_fabricated_maxima(self):
        p = self.write("conflict.jsonl", [164])
        row = self.row(5)
        row["message"]["usage"]["input_tokens"] = 200
        with p.open("a") as f:
            f.write(json.dumps(row) + "\n")
        errors = []
        entries = claude.parse_file(p, source_errors=errors)
        self.assertTrue(errors)
        self.assertEqual((entries[0].input_tokens, entries[0].output_tokens), (2, 164))
