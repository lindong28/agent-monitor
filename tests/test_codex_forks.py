import json
import tempfile
import unittest
from pathlib import Path

from parsers import codex


def usage(inputs, cached, outputs):
    return {"input_tokens": inputs, "cached_input_tokens": cached,
            "output_tokens": outputs}


def event(total, last=None):
    return {"type": "event_msg", "timestamp": "2026-09-20T05:54:37Z",
            "payload": {"type": "token_count", "info": {
                "total_token_usage": total, "last_token_usage": last}}}


class CodexForkTests(unittest.TestCase):
    def parse(self, rows, **kwargs):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "child.jsonl"
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
            return codex.parse_file(path, **kwargs)

    def meta(self, fork=True):
        payload = {"id": "child", "cwd": "/repo/child", "model": "child-model"}
        if fork:
            payload["forked_from_id"] = "parent"
        return {"type": "session_meta", "payload": payload}

    def test_fork_only_counts_new_usage_then_cumulative_differences(self):
        rows = [self.meta(), event(usage(1100, 900, 110), usage(100, 0, 10)),
                event(usage(1100, 900, 110), usage(100, 0, 10)),
                event(usage(1400, 1100, 140), usage(150, 100, 15))]
        entries = self.parse(rows)
        self.assertEqual([(e.input_tokens, e.cache_read_tokens, e.output_tokens)
                          for e in entries], [(100, 0, 10), (100, 200, 30)])

    def test_copied_parent_metadata_cannot_replace_child_identity_or_model(self):
        rows = [self.meta(False), {"type": "session_meta", "payload": {
            "id": "parent", "cwd": "/repo/parent", "model": "parent-model"}},
            event(usage(100, 20, 10), usage(100, 20, 10))]
        entry, = self.parse(rows, models={"child": "actual-child-model", "parent": "wrong"})
        self.assertEqual((entry.session_id, entry.project, entry.model),
                         ("child", "/repo/child", "actual-child-model"))

    def test_non_fork_keeps_legacy_first_cumulative_count(self):
        entry, = self.parse([self.meta(False), event(usage(1100, 900, 110), usage(100, 0, 10))])
        self.assertEqual(entry.input_tokens + entry.cache_read_tokens + entry.output_tokens, 1210)

    def test_zero_first_fork_usage_preserves_key_for_archive_correction(self):
        entry, = self.parse([self.meta(), event(usage(1000, 900, 100), usage(0, 0, 0))])
        self.assertEqual((entry.input_tokens, entry.cache_read_tokens, entry.output_tokens,
                          entry.message_count), (0, 0, 0, 0))
        self.assertEqual(entry.request_id, "token-count:2")

    def test_zero_total_placeholder_does_not_create_a_new_usage_event(self):
        last = dict(usage(0, 0, 0), total_tokens=9142)
        entries = self.parse([self.meta(), event(usage(0, 0, 0), last),
                              event(usage(100, 20, 10), usage(100, 20, 10))])
        entry, = entries
        self.assertEqual((entry.input_tokens, entry.cache_read_tokens, entry.output_tokens),
                         (80, 20, 10))
        self.assertEqual(entry.request_id, "token-count:3")

    def test_missing_fork_last_usage_is_visible_as_source_error(self):
        errors = []
        entries = self.parse([self.meta(), event(usage(1000, 900, 100))], source_errors=errors)
        self.assertEqual(entries, [])
        self.assertEqual(len(errors), 1)

    def test_invalid_initial_last_sets_baseline_for_following_delta(self):
        for last in (None, {}, usage(-1, 0, 0), usage(1100, 900, 110),
                     usage(10, 20, 1), usage("100", 0, 10)):
            with self.subTest(last=last):
                errors = []
                entries = self.parse([self.meta(), event(usage(1000, 900, 100), last),
                                      event(usage(1200, 1000, 120), usage(200, 100, 20))],
                                     source_errors=errors)
                self.assertEqual(len(errors), 1)
                entry, = entries
                self.assertEqual((entry.input_tokens, entry.cache_read_tokens, entry.output_tokens),
                                 (100, 100, 20))


if __name__ == "__main__":
    unittest.main()
