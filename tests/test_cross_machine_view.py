import contextlib
import hashlib
import json
import sqlite3
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from contextlib import closing

import generation
import exporter
import rollup
import server
import sync
from machine_config import Machine, MachineConfig, machine_config_fingerprint
from parsers import UsageEntry


_ACCOUNT_MEMORY_TEMP = None
_ACCOUNT_MEMORY_PATCHER = None


def setUpModule():
    global _ACCOUNT_MEMORY_TEMP, _ACCOUNT_MEMORY_PATCHER
    _ACCOUNT_MEMORY_TEMP = tempfile.TemporaryDirectory()
    memory_path = Path(_ACCOUNT_MEMORY_TEMP.name) / "account_memory.json"
    _ACCOUNT_MEMORY_PATCHER = mock.patch("server._ACCOUNT_MEMORY_PATH", memory_path)
    _ACCOUNT_MEMORY_PATCHER.start()


def tearDownModule():
    _ACCOUNT_MEMORY_PATCHER.stop()
    _ACCOUNT_MEMORY_TEMP.cleanup()


class CrossMachineOverviewTests(unittest.TestCase):
    def setUp(self):
        server._ACCOUNT_MEMORY_PATH.unlink(missing_ok=True)

    def test_iv11_overview_usage_fields_are_all_computed_from_rollup(self):
        def pivot(x_dim, group_dim, metric, **_kwargs):
            if x_dim == "agent" and metric == "cost":
                return {
                    "columns": ["value"],
                    "rows": [
                        {"x": "claude-code", "values": {"value": 2.0}},
                        {"x": "codex", "values": {"value": 3.0}},
                    ],
                }
            if x_dim == "agent" and metric == "total":
                return {
                    "columns": ["value"],
                    "rows": [
                        {"x": "claude-code", "values": {"value": 20}},
                        {"x": "codex", "values": {"value": 30}},
                    ],
                }
            if x_dim == "project":
                return {
                    "columns": ["value"],
                    "rows": [{"x": "repo", "values": {"value": 5.0}}],
                }
            if x_dim == "model":
                return {
                    "columns": ["value"],
                    "rows": [{"x": "gpt-5", "values": {"value": 50}}],
                }
            return {
                "columns": ["claude-code", "codex"],
                "rows": [
                    {
                        "x": "2026-08-04",
                        "values": {"claude-code": 2.0, "codex": 3.0},
                    }
                ],
            }

        sync_status = {
            "coverage": {"admitted": 3, "declared": 3},
            "machines": [],
            "syncing": False,
            "terminal": True,
        }
        with (
            mock.patch(
                "server.load_all_entries",
                side_effect=AssertionError("Overview must not read live entries"),
            ),
            mock.patch("server._maybe_sync_remotes", return_value=False),
            mock.patch("server._sync_status", return_value=sync_status),
            mock.patch(
                "server._rate_limits",
                return_value={
                    "claude": {"accounts": [], "unavailable_reason": None},
                    "codex": {"accounts": [], "unavailable_reason": None},
                },
            ),
            mock.patch(
                "server.generation.generation_admission_snapshot",
                return_value=contextlib.nullcontext(
                    SimpleNamespace(
                        admitted=(),
                        records=(),
                        config=SimpleNamespace(machines=()),
                    )
                ),
            ),
            mock.patch("server.rollup.query_pivot", side_effect=pivot),
            mock.patch("server.rollup.earliest_rollup_date", return_value="2026-04-21"),
        ):
            payload = server.overview({"range": ["30d"]})

        for key in ("today", "week", "range"):
            self.assertEqual(payload[key]["cost_usd"], 5.0)
            self.assertEqual(payload[key]["tokens"], 50)
            self.assertEqual(
                payload[key]["by_agent"],
                {"claude-code": 2.0, "codex": 3.0},
            )
        self.assertEqual(payload["top_projects_week"], [{"project": "repo", "cost_usd": 5.0}])
        self.assertEqual(
            payload["model_mix_month"],
            [{"model": "gpt-5", "tokens": 50, "pct": 1.0}],
        )
        self.assertEqual(payload["sync"]["coverage"], sync_status["coverage"])
        self.assertFalse(payload["sync"]["refresh_pending"])

    def test_iv8_quota_groups_by_account_not_by_freshest_machine(self):
        """The shape this pins is the difference between the two real cases.

        Three machines on one Claude account report one counter three times, so
        the freshest of them is the answer and the other two are stale copies.
        Two Codex accounts are independent pools, so picking the freshest across
        them shows one and hides the other — that was the reported bug, where a
        second machine's 89% stood in for this machine's 1%.
        """
        admitted = (
            self.current(
                "macbook",
                {
                    "claude": self.limit("2026-08-04T10:00:00Z", 10, "claude-A"),
                    "codex": self.limit("2026-08-04T12:00:00Z", 30, "codex-X"),
                },
            ),
            self.current(
                "macmini",
                {
                    "claude": self.limit("2026-08-04T11:00:00Z", 20, "claude-A"),
                    "codex": self.limit("2026-08-04T09:00:00Z", 40, "codex-Y"),
                },
            ),
            self.current(
                "dgx0023",
                {
                    "claude": self.limit("2026-08-04T10:30:00Z", 99, "claude-A"),
                    "codex": self.limit("2026-08-04T13:00:00Z", 50, "codex-Y"),
                },
            ),
        )
        admission = SimpleNamespace(admitted=admitted, records=())
        with mock.patch(
            "server.generation.generation_admission_snapshot",
            return_value=contextlib.nullcontext(admission),
        ), mock.patch(
            "server.claude_status.load_rate_limits",
            side_effect=AssertionError("must not read viewer-local quota"),
        ), mock.patch(
            "server.codex.load_rate_limits",
            side_effect=AssertionError("must not read viewer-local quota"),
        ):
            limits = server._rate_limits()

        claude = limits["claude"]["accounts"]
        self.assertEqual(len(claude), 1)
        self.assertEqual(claude[0]["account_id"], "claude-A")
        self.assertEqual(claude[0]["five_hour_used_pct"], 20)
        self.assertEqual(claude[0]["machines"], ["dgx0023", "macbook", "macmini"])
        self.assertNotEqual(claude[0]["five_hour_used_pct"], 10 + 20 + 99)

        codex = {entry["account_id"]: entry for entry in limits["codex"]["accounts"]}
        self.assertEqual(set(codex), {"codex-X", "codex-Y"})
        self.assertEqual(codex["codex-X"]["five_hour_used_pct"], 30)
        self.assertEqual(codex["codex-X"]["machines"], ["macbook"])
        self.assertEqual(codex["codex-Y"]["five_hour_used_pct"], 50)
        self.assertEqual(codex["codex-Y"]["machines"], ["dgx0023", "macmini"])
        for entry in limits["codex"]["accounts"]:
            self.assertNotEqual(entry["five_hour_used_pct"], 30 + 40 + 50)

    def test_iv8_unstamped_readings_do_not_merge_across_machines(self):
        """Two machines whose exporters predate account stamping are two
        unknowns, not one shared account — merging them would rebuild the very
        collapse this grouping exists to undo."""
        blocks = []
        for updated_at, pct in (("2026-08-04T09:00:00Z", 40), ("2026-08-04T13:00:00Z", 50)):
            block = self.limit(updated_at, pct, None)
            del block["account_id"]  # what an exporter predating the stamp emits
            blocks.append(block)
        admitted = (
            self.current("macmini", {"codex": blocks[0]}),
            self.current("dgx0023", {"codex": blocks[1]}),
        )
        admission = SimpleNamespace(admitted=admitted, records=())

        limits = server._rate_limits(admission=admission)

        entries = limits["codex"]["accounts"]
        self.assertEqual(len(entries), 2)
        self.assertEqual(
            sorted(entry["machines"][0] for entry in entries), ["dgx0023", "macmini"]
        )
        self.assertTrue(all(entry["account_state"] == "unstamped" for entry in entries))

    def test_iv8_signed_out_is_told_apart_from_an_exporter_that_cannot_stamp(self):
        """Both leave no account, but only one is fixed by updating that machine.
        Telling a current machine to update itself sends the reader after a
        problem they do not have."""
        signed_out = self.limit("2026-08-04T09:00:00Z", 40, None)
        unstamped = self.limit("2026-08-04T10:00:00Z", 50, None)
        del unstamped["account_id"]
        admission = SimpleNamespace(
            admitted=(
                self.current("macmini", {"codex": signed_out}),
                self.current("dgx0023", {"codex": unstamped}),
            ),
            records=(),
        )

        limits = server._rate_limits(admission=admission)

        states = {
            entry["machines"][0]: entry["account_state"]
            for entry in limits["codex"]["accounts"]
        }
        self.assertEqual(states, {"macmini": "signed_out", "dgx0023": "unstamped"})

    def test_iv8_the_row_names_which_machine_the_reader_is_sitting_at(self):
        """Three e-mail addresses do not tell the reader which account the
        session in front of them is spending; a marked machine name does."""
        admitted = (
            self.current("macbook", {"codex": self.limit("2026-08-04T10:00:00Z", 10, "A")}),
            self.current("macmini", {"codex": self.limit("2026-08-04T09:00:00Z", 40, "B")}),
        )
        admission = SimpleNamespace(
            admitted=admitted,
            records=(),
            config=MachineConfig(
                machines=(Machine("macmini", "macmini", False), Machine("macbook", "macbook", True)),
                retired_names=frozenset(),
            ),
        )

        limits = server._rate_limits(admission=admission)

        marks = {e["account_id"]: e["this_machine"] for e in limits["codex"]["accounts"]}
        self.assertEqual(marks, {"A": "macbook", "B": None})

    def test_iv8_no_self_machine_declared_marks_nothing(self):
        """Absent config must leave the mark empty rather than guessing at the
        first machine — a wrong 'this machine' is worse than none."""
        admission = SimpleNamespace(
            admitted=(self.current("macmini", {"codex": self.limit("2026-08-04T09:00:00Z", 40)}),),
            records=(),
        )

        limits = server._rate_limits(admission=admission)

        self.assertIsNone(limits["codex"]["accounts"][0]["this_machine"])

    def test_iv8_one_malformed_block_costs_only_its_own_row(self):
        """`rate_limits` is validated only as "an object" — nothing checks what
        is inside. A machine publishing a bad field must not take down the
        Overview payload it travels in, which carries cost and charts too."""
        broken = self.limit("2026-08-04T09:00:00Z", 40)
        broken["account_id"] = {"unhashable": True}
        good = self.limit("2026-08-04T10:00:00Z", 50, "codex-Y")
        admission = SimpleNamespace(
            admitted=(
                self.current("macmini", {"codex": broken}),
                self.current("dgx0023", {"codex": good}),
            ),
            records=(),
        )

        limits = server._rate_limits(admission=admission)

        entries = {entry["machines"][0]: entry for entry in limits["codex"]["accounts"]}
        self.assertEqual(entries["dgx0023"]["account_id"], "codex-Y")
        self.assertIsNone(entries["macmini"]["account_id"])
        self.assertEqual(entries["macmini"]["five_hour_used_pct"], 40)

    def test_iv8_a_naive_timestamp_on_one_machine_does_not_break_the_others(self):
        """Codex rollouts carry tz-naive timestamps and Claude's are aware;
        `tests/test_codex_rate_limits.py` pins that both occur. Comparing one of
        each raises, and both comparisons here span machines."""
        naive = self.limit("2026-08-04T09:00:00", 40, "codex-X")
        aware = self.limit("2026-08-04T10:00:00Z", 50, "codex-Y")
        admission = SimpleNamespace(
            admitted=(
                self.current("macmini", {"codex": naive}),
                self.current("dgx0023", {"codex": aware}),
            ),
            records=(),
        )

        limits = server._rate_limits(admission=admission)

        self.assertEqual(
            [entry["account_id"] for entry in limits["codex"]["accounts"]],
            ["codex-Y", "codex-X"],
        )

    def test_iv8_legacy_generation_quota_unavailability_names_latest_sync_failure(self):
        admission = SimpleNamespace(
            admitted=(self.current("macbook", {}),),
            records=(),
        )
        sync_status = {
            "syncing": False,
            "machines": [
                {
                    "name": "macbook",
                    "reason": "exporter runtime authority differs from HEAD",
                    "last_attempt_outcome": "failure",
                },
                {
                    "name": "macmini",
                    "reason": "remote export returned non-zero exit status 2",
                    "last_attempt_outcome": "failure",
                },
            ],
        }

        limits = server._rate_limits(admission=admission, sync_status=sync_status)

        expected = (
            "最近一次同步在拿到 claude 配额数据前就失败了："
            "macbook: exporter runtime authority differs from HEAD；"
            "macmini: remote export returned non-zero exit status 2。"
        )
        self.assertEqual(limits["claude"]["unavailable_reason"], expected)
        self.assertEqual(limits["claude"]["accounts"], [])

    def test_iv8_unknown_contact_is_not_misreported_as_latest_sync_failure(self):
        admission = SimpleNamespace(admitted=(self.current("macbook", {}),), records=())
        sync_status = {
            "syncing": False,
            "machines": [{
                "name": "macbook",
                "reason": "Contact status unknown since server restart",
                "last_attempt_outcome": "unknown_since_restart",
            }],
        }

        limits = server._rate_limits(admission=admission, sync_status=sync_status)

        reason = limits["claude"]["unavailable_reason"]
        self.assertIn("需要一次成功的刷新", reason)
        self.assertNotIn("最近一次同步在拿到", reason)

    @staticmethod
    def current(name, rate_limits):
        return SimpleNamespace(host=name, meta={"rate_limits": rate_limits})

    @staticmethod
    def limit(updated_at, pct, account_id="acct"):
        return {
            "five_hour_pct": pct,
            "five_hour_resets_at": 1,
            "seven_day_pct": pct + 1,
            "seven_day_resets_at": 2,
            "updated_at": updated_at,
            "account_id": account_id,
            "account_label": f"{account_id}@example.com" if account_id else None,
            "account_plan": None,
        }


class BackgroundSyncTests(unittest.TestCase):
    def tearDown(self):
        release = getattr(self, "release", None)
        if release is not None:
            release.set()
        deadline = time.monotonic() + 2
        while getattr(server, "_SYNC_STATE", {}).get("running") and time.monotonic() < deadline:
            time.sleep(0.01)
        if hasattr(server, "_reset_sync_state_for_tests"):
            server._reset_sync_state_for_tests()

    def test_iv10_force_starts_one_nonblocking_round_and_status_reaches_terminal(self):
        self.release = threading.Event()
        calls = []

        def slow_sync_all(**_kwargs):
            calls.append("started")
            self.release.wait(2)
            return {
                "macbook": sync.SyncResult(generation=SimpleNamespace(meta={})),
                "macmini": sync.SyncResult(error="timeout"),
                "macstudio": sync.SyncResult(generation=SimpleNamespace(meta={})),
                "dgx0023": sync.SyncResult(error="offline"),
            }

        if hasattr(server, "_reset_sync_state_for_tests"):
            server._reset_sync_state_for_tests()
        admission = SimpleNamespace(records=tuple(
            SimpleNamespace(machine=SimpleNamespace(name=name))
            for name in ("macbook", "macmini", "macstudio", "dgx0023")
        ))
        with mock.patch("server.sync.sync_all", side_effect=slow_sync_all), mock.patch(
            "server.generation.generation_admission_snapshot", side_effect=lambda: contextlib.nullcontext(admission)
        ), mock.patch("server._remember_accounts_after_sync_publish"):
            started = time.monotonic()
            server._maybe_sync_remotes({"force": ["1"]})
            server._maybe_sync_remotes({"force": ["1"]})
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 0.1)
            deadline = time.monotonic() + 1
            while not calls and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(calls, ["started"])
            self.assertTrue(server._sync_runtime_snapshot()["syncing"])
            self.release.set()
            deadline = time.monotonic() + 2
            while server._sync_runtime_snapshot()["syncing"] and time.monotonic() < deadline:
                time.sleep(0.01)

        final = server._sync_runtime_snapshot()
        self.assertFalse(final["syncing"])
        self.assertTrue(final["terminal"])
        self.assertEqual(final["errors"], {"macmini": "timeout", "dgx0023": "offline"})

    def test_g6_outcome_less_sync_result_is_malformed_not_contact_success(self):
        machine, admission = self.single_admitted_machine()
        server._reset_sync_state_for_tests()
        with mock.patch(
            "server.generation.generation_admission_snapshot",
            return_value=contextlib.nullcontext(admission),
        ), mock.patch(
            "server.sync.sync_all",
            return_value={machine.name: sync.SyncResult()},
        ):
            self.assertTrue(server._maybe_sync_remotes({"force": ["1"]}))
            self.wait_for_terminal()

        row = server._sync_status(admission=admission)["machines"][0]
        self.assertEqual(row["availability"], "unknown")
        self.assertEqual(row["last_attempt_outcome"], "malformed_result")
        self.assertIn("没有明确结论", row["reason"])

    def test_g6_none_result_cannot_bypass_terminal_commit(self):
        machine, admission = self.single_admitted_machine()
        server._reset_sync_state_for_tests()
        with mock.patch(
            "server.generation.generation_admission_snapshot",
            return_value=contextlib.nullcontext(admission),
        ), mock.patch(
            "server.sync.sync_all",
            return_value={machine.name: None},
        ):
            self.assertTrue(server._maybe_sync_remotes({"force": ["1"]}))
            self.wait_for_terminal()

        runtime = server._sync_runtime_snapshot()
        row = server._sync_status(admission=admission)["machines"][0]
        self.assertTrue(runtime["terminal"])
        self.assertFalse(runtime["syncing"])
        self.assertEqual(row["last_attempt_outcome"], "malformed_result")
        self.assertIn("格式错误", row["reason"])

    def test_g6_new_round_preserves_prior_unreachable_observation_until_contact(self):
        self.release = threading.Event()
        machine = Machine("macbook", "macbook", True)
        current = SimpleNamespace(
            host="macbook",
            meta={"published_at": "2026-08-04T11:00:00Z"},
        )
        admission = SimpleNamespace(
            admitted=(current,),
            records=(generation.AdmissionRecord(machine, True, False, current=current),),
            config=SimpleNamespace(machines=(machine,)),
        )
        attempts = [
            {"macbook": sync.SyncResult(error="ssh timeout")},
            None,
        ]

        def sync_all(**_kwargs):
            result = attempts.pop(0)
            if result is None:
                self.release.wait(2)
                return {"macbook": sync.SyncResult(generation=SimpleNamespace(meta={}))}
            return result

        server._reset_sync_state_for_tests()
        with mock.patch(
            "server.generation.generation_admission_snapshot",
            return_value=contextlib.nullcontext(admission),
        ), mock.patch("server.sync.sync_all", side_effect=sync_all):
            self.assertTrue(server._maybe_sync_remotes({"force": ["1"]}))
            self.wait_for_terminal()
            failed = server._sync_status(admission=admission)
            self.assertEqual(failed["machines"][0]["availability"], "unreachable")
            self.assertEqual(failed["machines"][0]["reason"], "ssh timeout")
            self.assertEqual(
                failed["machines"][0]["last_successful_contact_ts"],
                "2026-08-04T11:00:00Z",
            )

            self.assertTrue(server._maybe_sync_remotes({"force": ["1"]}))
            during = server._sync_status(admission=admission)
            self.release.set()
            self.wait_for_terminal()

        self.assertTrue(during["syncing"])
        self.assertEqual(during["machines"][0]["availability"], "unreachable")
        self.assertEqual(during["machines"][0]["reason"], "ssh timeout")

    def test_g6_thread_start_failure_rolls_back_to_terminal_attempt_failure(self):
        machine = Machine("macbook", "macbook", True)
        current = SimpleNamespace(
            host="macbook",
            meta={"published_at": "2026-08-04T11:00:00Z"},
        )
        admission = SimpleNamespace(
            admitted=(current,),
            records=(generation.AdmissionRecord(machine, True, False, current=current),),
            config=SimpleNamespace(machines=(machine,)),
        )
        server._reset_sync_state_for_tests()
        with self.assertLogs("agent-monitor", level="ERROR") as logs:
            with mock.patch(
                "server.generation.generation_admission_snapshot",
                return_value=contextlib.nullcontext(admission),
            ), mock.patch("server.threading.Thread.start", side_effect=RuntimeError("no thread")):
                self.assertFalse(server._maybe_sync_remotes({"force": ["1"]}))

        runtime = server._sync_runtime_snapshot()
        status = server._sync_status(admission=admission)
        self.assertTrue(runtime["terminal"])
        self.assertFalse(runtime["syncing"])
        self.assertEqual(status["machines"][0]["availability"], "unknown")
        self.assertEqual(status["machines"][0]["last_attempt_outcome"], "failure")
        self.assertIn("no thread", status["machines"][0]["reason"])
        self.assertIn("no thread", "\n".join(logs.output))

    def test_g6_generation_cleanup_failure_still_commits_terminal_observation(self):
        machine = Machine("macbook", "macbook", True)
        admitted_current = SimpleNamespace(
            host="macbook",
            meta={"published_at": "2026-08-04T11:00:00Z"},
        )
        admission = SimpleNamespace(
            admitted=(admitted_current,),
            records=(
                generation.AdmissionRecord(
                    machine, True, False, current=admitted_current
                ),
            ),
            config=SimpleNamespace(machines=(machine,)),
        )
        current = mock.Mock()
        current.close.side_effect = OSError("close failed")
        server._reset_sync_state_for_tests()
        with self.assertLogs("agent-monitor", level="ERROR") as logs:
            with mock.patch(
                "server.generation.generation_admission_snapshot",
                return_value=contextlib.nullcontext(admission),
            ), mock.patch(
                "server.sync.sync_all",
                return_value={"macbook": sync.SyncResult(generation=current)},
            ):
                self.assertTrue(server._maybe_sync_remotes({"force": ["1"]}))
                self.wait_for_terminal()

        runtime = server._sync_runtime_snapshot()
        status = server._sync_status(admission=admission)
        self.assertTrue(runtime["terminal"])
        self.assertEqual(status["machines"][0]["availability"], "reachable")
        self.assertEqual(status["machines"][0]["last_attempt_outcome"], "cleanup_failed")
        self.assertIn("close failed", status["machines"][0]["reason"])
        self.assertIn("close failed", "\n".join(logs.output))

    def test_g6_machine_removed_during_sync_retains_terminal_observation(self):
        machine_a = Machine("macbook", "macbook", True)
        machine_b = Machine("macmini", "macmini", False)
        starting = SimpleNamespace(
            admitted=(),
            records=(
                generation.AdmissionRecord(machine_a, False, True),
                generation.AdmissionRecord(machine_b, False, True),
            ),
            config=SimpleNamespace(machines=(machine_a, machine_b)),
        )
        final = SimpleNamespace(
            admitted=(),
            records=(generation.AdmissionRecord(machine_a, False, True),),
            config=SimpleNamespace(machines=(machine_a,)),
        )
        server._reset_sync_state_for_tests()
        with mock.patch(
            "server.generation.generation_admission_snapshot",
            return_value=contextlib.nullcontext(starting),
        ), mock.patch(
            "server.sync.sync_all",
            return_value={
                "macbook": sync.SyncResult(error="self export failed"),
                "macmini": sync.SyncResult(error="ssh timeout"),
            },
        ):
            self.assertTrue(server._maybe_sync_remotes({"force": ["1"]}))
            self.wait_for_terminal()

        status = server._sync_status(admission=final)
        self.assertEqual([row["name"] for row in status["machines"]], ["macbook"])
        removed = server._sync_runtime_snapshot()["observations"]["macmini"]
        self.assertEqual(removed["contact_status"], "unreachable")
        self.assertEqual(removed["reason"], "ssh timeout")
        self.assertEqual(status["coverage"], {"admitted": 0, "declared": 1})

        with mock.patch(
            "server.generation.generation_admission_snapshot",
            return_value=contextlib.nullcontext(final),
        ), mock.patch(
            "server.sync.sync_all",
            return_value={"macbook": sync.SyncResult(error="self still dirty")},
        ):
            self.assertTrue(server._maybe_sync_remotes({"force": ["1"]}))
            self.wait_for_terminal()

        after_later_round = server._sync_status(admission=final)
        self.assertEqual(
            [row["name"] for row in after_later_round["machines"]],
            ["macbook"],
        )

    def test_g4_restart_is_explicitly_unknown_until_this_process_observes_contact(self):
        machine = Machine("macbook", "macbook", True)
        current = SimpleNamespace(
            host="macbook",
            meta={"published_at": "2026-08-04T11:00:00Z"},
        )
        admission = SimpleNamespace(
            admitted=(current,),
            records=(generation.AdmissionRecord(machine, True, False, current=current),),
            config=SimpleNamespace(machines=(machine,)),
        )
        server._reset_sync_state_for_tests()

        row = server._sync_status(admission=admission)["machines"][0]

        self.assertEqual(row["availability"], "unknown")
        self.assertEqual(row["last_attempt_outcome"], "unknown_since_restart")
        self.assertIsNone(row["last_attempt_ts"])
        self.assertEqual(row["last_successful_contact_ts"], "2026-08-04T11:00:00Z")
        self.assertIn("服务重启后", row["reason"])

    def test_g4_machine_added_after_start_is_not_mislabelled_as_restart_unknown(self):
        machine_a, initial = self.single_admitted_machine()
        machine_b = Machine("macmini", "macmini", False)
        expanded = SimpleNamespace(
            admitted=initial.admitted,
            records=initial.records + (generation.AdmissionRecord(machine_b, False, True),),
            config=SimpleNamespace(machines=(machine_a, machine_b)),
        )
        server._reset_sync_state_for_tests()
        server._sync_status(admission=initial)

        added = next(
            row for row in server._sync_status(admission=expanded)["machines"]
            if row["name"] == "macmini"
        )

        self.assertEqual(added["last_attempt_outcome"], "not_attempted_since_added")
        self.assertIn("启动后加入", added["reason"])
        self.assertNotIn("服务重启后", added["reason"])

    def wait_for_terminal(self):
        deadline = time.monotonic() + 2
        while server._sync_runtime_snapshot()["syncing"] and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(server._sync_runtime_snapshot()["terminal"])

    @staticmethod
    def single_admitted_machine():
        machine = Machine("macbook", "macbook", True)
        current = SimpleNamespace(
            host=machine.name,
            meta={"published_at": "2026-08-04T11:00:00Z"},
        )
        admission = SimpleNamespace(
            admitted=(current,),
            records=(generation.AdmissionRecord(machine, True, False, current=current),),
            config=SimpleNamespace(machines=(machine,)),
        )
        return machine, admission

    def test_sync_status_exposes_each_admitted_generation_totals_schema(self):
        _machine, admission = self.single_admitted_machine()
        admission.records[0].current.meta.update(
            {
                "schema_version": 2,
                "metric_totals_basis": "usage_with_legacy_project_fallback",
                "legacy_fallback_bucket_count": 7,
                "legacy_fallback_bucket_dimensions": ["date", "agent", "model"],
            }
        )

        row = server._sync_status(admission=admission)["machines"][0]

        self.assertEqual(row["totals_schema_version"], 2)
        self.assertEqual(
            row["generation_totals_basis"],
            "usage_with_legacy_project_fallback",
        )
        self.assertEqual(row["generation_legacy_fallback_bucket_count"], 7)
        self.assertEqual(
            row["generation_legacy_fallback_bucket_dimensions"],
            ["date", "agent", "model"],
        )

    def test_iv10_sync_due_and_g4_stale_use_independent_age_boundaries(self):
        machine = Machine("macbook", "macbook", True)

        def admission(age):
            published_at = (
                datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc) - timedelta(seconds=age)
            ).isoformat()
            current = SimpleNamespace(host=machine.name, meta={"published_at": published_at})
            record = generation.AdmissionRecord(machine, True, False, current=current)
            return SimpleNamespace(
                config=SimpleNamespace(machines=(machine,)),
                admitted=(current,),
                records=(record,),
            )

        now = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)
        with mock.patch("server._aware_now", return_value=now), mock.patch(
            "server.generation.generation_admission_snapshot",
            return_value=contextlib.nullcontext(admission(599.999)),
        ), mock.patch("server.threading.Thread") as thread:
            self.assertFalse(server._maybe_sync_remotes({}))
            thread.assert_not_called()

        server._reset_sync_state_for_tests()
        with mock.patch("server._aware_now", return_value=now), mock.patch(
            "server.generation.generation_admission_snapshot",
            return_value=contextlib.nullcontext(admission(600)),
        ), mock.patch("server.threading.Thread") as thread:
            self.assertTrue(server._maybe_sync_remotes({}))
            thread.assert_called_once()
            self.assertTrue(thread.call_args.kwargs["daemon"])
        server._reset_sync_state_for_tests()

        for age, expected_stale in (
            (600, False),
            (6 * 60 * 60 - 0.001, False),
            (6 * 60 * 60, True),
        ):
            with self.subTest(age=age):
                status = server._sync_status(now=now, admission=admission(age))
                self.assertEqual(status["machines"][0]["stale"], expected_stale)

        self.assertEqual(server._SYNC_DUE_AFTER_SECONDS, 600)
        self.assertEqual(server._STALE_AFTER_SECONDS, 6 * 60 * 60)

    def test_g6_failed_sync_remains_terminal_during_normal_overview_polling(self):
        machine = Machine("macbook", "macbook", True)
        published_at = datetime(2026, 8, 4, 10, 0, tzinfo=timezone.utc).isoformat()
        current = SimpleNamespace(
            host=machine.name,
            meta={"published_at": published_at},
        )
        admission = SimpleNamespace(
            admitted=(current,),
            records=(generation.AdmissionRecord(machine, True, False, current=current),),
            config=SimpleNamespace(machines=(machine,)),
        )

        server._reset_sync_state_for_tests()
        with mock.patch(
            "server.sync.sync_all",
            return_value={
                "macbook": sync.SyncResult(
                    error="exporter runtime authority differs from HEAD"
                )
            },
        ), mock.patch(
            "server.generation.generation_admission_snapshot",
            return_value=contextlib.nullcontext(admission),
        ), mock.patch(
            "server._utc_timestamp",
            side_effect=("2026-08-04T11:59:00Z", "2026-08-04T11:59:30Z"),
        ):
            self.assertTrue(server._maybe_sync_remotes({"force": ["1"]}))
            deadline = time.monotonic() + 2
            while server._sync_runtime_snapshot()["syncing"] and time.monotonic() < deadline:
                time.sleep(0.01)

        failed = server._sync_runtime_snapshot()
        self.assertTrue(failed["terminal"])
        self.assertEqual(
            failed["errors"],
            {"macbook": "exporter runtime authority differs from HEAD"},
        )

        with mock.patch("server._aware_now", return_value=datetime(2026, 8, 4, 12, 9, 29, 999000, tzinfo=timezone.utc)), mock.patch(
            "server.generation.generation_admission_snapshot",
            return_value=contextlib.nullcontext(admission),
        ), mock.patch("server.threading.Thread") as thread:
            self.assertFalse(server._maybe_sync_remotes({}))
            thread.assert_not_called()

        final = server._sync_runtime_snapshot()
        self.assertTrue(final["terminal"])
        self.assertFalse(final["syncing"])
        self.assertEqual(
            final["errors"],
            {"macbook": "exporter runtime authority differs from HEAD"},
        )

        with mock.patch("server._aware_now", return_value=datetime(2026, 8, 4, 12, 9, 30, tzinfo=timezone.utc)), mock.patch(
            "server.generation.generation_admission_snapshot",
            return_value=contextlib.nullcontext(admission),
        ), mock.patch("server.threading.Thread") as thread:
            self.assertTrue(server._maybe_sync_remotes({}))
            thread.assert_called_once()
        server._reset_sync_state_for_tests()

    def test_g6_http_failure_polling_reaches_terminal_and_surfaces_quota_cause(self):
        self.release = threading.Event()
        calls = []
        machine = Machine("macbook", "macbook", True)
        published_at = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        current = SimpleNamespace(
            host=machine.name,
            db_path=Path("/nonexistent/synthetic-generation.db"),
            meta={
                "published_at": published_at,
                "generated_at": published_at,
                "data_start_date": "2026-04-21",
                "generation_id": "a" * 64,
                "rate_limits": {},
            },
        )
        admission = SimpleNamespace(
            admitted=(current,),
            records=(generation.AdmissionRecord(machine, True, False, current=current),),
            config=SimpleNamespace(machines=(machine,)),
        )

        def failing_sync_all(**_kwargs):
            calls.append("started")
            self.release.wait(2)
            return {
                "macbook": sync.SyncResult(
                    error="exporter runtime authority differs from HEAD"
                )
            }

        server._reset_sync_state_for_tests()
        httpd = server.ThreadingHTTPServer(("0.0.0.0", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def get_json(path):
            with opener.open(
                "http://127.0.0.1:%d%s" % (httpd.server_port, path), timeout=2
            ) as response:
                return json.load(response)

        try:
            with mock.patch("server.sync.sync_all", side_effect=failing_sync_all), mock.patch(
                "server.generation.generation_admission_snapshot",
                return_value=contextlib.nullcontext(admission),
            ), mock.patch(
                "server.rollup.query_pivot",
                return_value={"columns": [], "rows": []},
            ), mock.patch("server.rollup.earliest_rollup_date", return_value=None):
                initial = get_json("/api/overview?force=1")
                self.assertTrue(initial["sync"]["syncing"])
                self.release.set()

                deadline = time.monotonic() + 2
                terminal = None
                while time.monotonic() < deadline:
                    terminal = get_json("/api/sync-status")
                    if terminal["terminal"]:
                        break
                    time.sleep(0.01)

                final = get_json("/api/overview")
        finally:
            self.release.set()
            httpd.shutdown()
            httpd.server_close()
            thread.join(2)

        self.assertEqual(calls, ["started"])
        self.assertIsNotNone(terminal)
        self.assertTrue(terminal["terminal"])
        self.assertFalse(terminal["syncing"])
        self.assertEqual(
            terminal["machines"][0]["reason"],
            "exporter runtime authority differs from HEAD",
        )
        self.assertTrue(final["sync"]["terminal"])
        self.assertFalse(final["sync"]["syncing"])
        self.assertEqual(calls, ["started"])
        self.assertIn(
            "最近一次同步在拿到 claude 配额数据前就失败了",
            final["rate_limits"]["claude"]["unavailable_reason"],
        )
        self.assertIn(
            "macbook: exporter runtime authority differs from HEAD",
            final["rate_limits"]["claude"]["unavailable_reason"],
        )

    def test_g6_overview_returns_old_generation_while_slow_sync_is_running(self):
        self.release = threading.Event()

        def slow_sync_all(**_kwargs):
            self.release.wait(2)
            return {}

        admission = SimpleNamespace(
            admitted=(),
            records=(),
            config=SimpleNamespace(machines=()),
        )
        with mock.patch("server.sync.sync_all", side_effect=slow_sync_all), mock.patch(
            "server.generation.generation_admission_snapshot",
            return_value=contextlib.nullcontext(admission),
        ), mock.patch(
            "server.rollup.query_pivot",
            return_value={"columns": [], "rows": []},
        ), mock.patch("server.rollup.earliest_rollup_date", return_value=None):
            started = time.monotonic()
            payload = server.overview({"force": ["1"]})
            elapsed = time.monotonic() - started
            self.release.set()
            self.wait_for_terminal()

        self.assertLess(elapsed, 0.1)
        self.assertTrue(payload["sync"]["syncing"])
        self.assertFalse(payload["sync"]["terminal"])
        self.assertTrue(payload["sync"]["refresh_pending"])


class AdmissionStatusTests(unittest.TestCase):
    def test_g4_admission_reports_each_fail_closed_reason_without_admitting_it(self):
        cases = (
            ("machine_config_fingerprint", self.install_wrong_fingerprint),
            ("bucket_timezone", self.install_wrong_timezone),
            ("generation_id", self.install_wrong_generation_id),
            ("digest", self.install_wrong_digest),
        )
        for expected_reason, install in cases:
            with self.subTest(reason=expected_reason), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "generations"
                config_path = Path(tmp) / "machines.json"
                machine = Machine("macbook", "macbook", True)
                self.write_config(config_path, machine)
                install(root, machine)

                with generation.generation_admission_snapshot(
                    config_path=config_path,
                    root=root,
                ) as admission:
                    self.assertEqual(len(admission.admitted), 0)
                    self.assertEqual(len(admission.records), 1)
                    record = admission.records[0]
                    self.assertFalse(record.admitted)
                    self.assertFalse(record.never)
                    self.assertEqual(record.exclusion_reason, expected_reason)

    def test_iv11_overview_pins_one_admitted_generation_set_for_every_panel(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, config_path, machines = self.three_machine_fixture(tmp)
            for index, machine in enumerate(machines, start=1):
                self.publish(root, machine, multiplier=index).close()
            admission_snapshot = generation.generation_admission_snapshot
            with mock.patch(
                "server.generation.generation_admission_snapshot",
                side_effect=lambda: admission_snapshot(config_path=config_path, root=root),
            ) as load_admission, mock.patch(
                "rollup.admitted_generations",
                side_effect=AssertionError("overview queries must reuse the pinned admission set"),
            ):
                payload = server.overview({"range": ["30d"], "sync": ["0"]})

            load_admission.assert_called_once()
            self.assertEqual(payload["sync"]["coverage"], {"admitted": 3, "declared": 3})
            self.assertEqual(payload["sync"]["all_machines"], ["dgx0023", "macbook", "macmini"])
            self.assertEqual(payload["today"]["cost_usd"], 9.0)
            self.assertEqual(payload["today"]["tokens"], 114)

    def test_g2_machine_filter_exposes_three_names_and_selects_one_host_slice(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, config_path, machines = self.three_machine_fixture(tmp)
            for index, machine in enumerate(machines, start=1):
                self.publish(root, machine, multiplier=index).close()

            with self.rollup_admission(root, config_path):
                options = rollup.filter_options()
                selected = rollup.query_pivot(
                    "machine",
                    "none",
                    "total",
                    machines={"dgx0023"},
                )

            self.assertEqual(options["machine"], ["dgx0023", "macbook", "macmini"])
            self.assertEqual(
                selected,
                {
                    "columns": ["value"],
                    "rows": [{"x": "dgx0023", "values": {"value": 57}}],
                    "totals_provenance": {
                        "basis": "legacy_project_derived",
                        "legacy_fallback_bucket_count": 1,
                        "bucket_dimensions": ["date", "machine", "agent", "model"],
                    },
                },
            )

    def test_g4_unreachable_generation_keeps_all_seven_metrics_and_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, config_path, machines = self.three_machine_fixture(tmp)
            for index, machine in enumerate(machines, start=1):
                current = self.publish(
                    root,
                    machine,
                    published_at=datetime(2026, 8, 4, 6, 0, tzinfo=timezone.utc),
                    multiplier=index,
                    quota_updated_at="2026-08-04T0%d:00:00Z" % index,
                    quota_pct=index * 10,
                )
                current.close()

            with self.rollup_admission(root, config_path):
                before = self.metric_totals()
                per_machine_before = self.machine_metric_totals("macmini")

            status = self.status(
                root,
                config_path,
                errors={"macmini": "ssh timeout"},
                running=False,
                now=datetime(2026, 8, 4, 12, 20, tzinfo=timezone.utc),
            )
            with self.rollup_admission(root, config_path):
                after = self.metric_totals()
                per_machine_after = self.machine_metric_totals("macmini")

            self.assertEqual(before, self.expected_metrics(6))
            self.assertEqual(after, before)
            self.assertEqual(per_machine_before, self.expected_metrics(2))
            self.assertEqual(per_machine_after, per_machine_before)
            self.assertEqual(status["coverage"], {"admitted": 3, "declared": 3})
            macmini = self.machine_status(status, "macmini")
            self.assertTrue(macmini["admitted"])
            self.assertEqual(macmini["availability"], "unreachable")
            self.assertTrue(macmini["stale"])
            self.assertEqual(macmini["reason"], "ssh timeout")

    def test_g4_syncing_and_recent_failure_are_orthogonal_to_stale(self):
        machine = Machine("macbook", "macbook", True)
        old = self.fake_admission(machine, "2026-08-04T05:59:59Z")
        recent = self.fake_admission(machine, "2026-08-04T07:00:00Z")
        now = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)

        syncing = self.status_from_admission(old, running=True, errors={}, now=now)
        syncing_machine = syncing["machines"][0]
        self.assertTrue(syncing_machine["syncing"])
        self.assertTrue(syncing_machine["stale"])
        self.assertEqual(syncing_machine["availability"], "unknown")

        failed_recent = self.status_from_admission(
            recent,
            running=False,
            errors={"macbook": "connection refused"},
            now=now,
        )
        failed_machine = failed_recent["machines"][0]
        self.assertEqual(failed_machine["availability"], "unreachable")
        self.assertFalse(failed_machine["stale"])
        self.assertEqual(failed_machine["reason"], "connection refused")

    def test_g4_never_excludes_machine_and_changes_exact_coverage_and_totals(self):
        with tempfile.TemporaryDirectory() as complete_tmp, tempfile.TemporaryDirectory() as partial_tmp:
            complete_root, complete_config, machines = self.three_machine_fixture(complete_tmp)
            partial_root, partial_config, partial_machines = self.three_machine_fixture(partial_tmp)
            for index, machine in enumerate(machines, start=1):
                self.publish(complete_root, machine, multiplier=index).close()
            for index, machine in enumerate(partial_machines[:2], start=1):
                self.publish(partial_root, machine, multiplier=index).close()

            with self.rollup_admission(complete_root, complete_config):
                complete_totals = self.metric_totals()
            with self.rollup_admission(partial_root, partial_config):
                partial_totals = self.metric_totals()
            complete_status = self.status(complete_root, complete_config)
            partial_status = self.status(partial_root, partial_config)

            self.assertEqual(complete_totals, self.expected_metrics(6))
            self.assertEqual(partial_totals, self.expected_metrics(3))
            self.assertEqual(complete_status["coverage"], {"admitted": 3, "declared": 3})
            self.assertEqual(partial_status["coverage"], {"admitted": 2, "declared": 3})
            never = self.machine_status(partial_status, "dgx0023")
            self.assertFalse(never["admitted"])
            self.assertEqual(never["availability"], "never")
            self.assertFalse(never["stale"])

    def test_g4_each_admission_failure_is_excluded_from_every_consumer(self):
        cases = (
            ("machine_config_fingerprint", self.corrupt_fingerprint),
            ("bucket_timezone", self.corrupt_timezone),
            ("generation_id", self.corrupt_generation_id),
            ("digest", self.corrupt_digest),
        )
        for expected_reason, corrupt in cases:
            with self.subTest(reason=expected_reason), tempfile.TemporaryDirectory() as tmp:
                root, config_path, machines = self.three_machine_fixture(tmp)
                for index, machine in enumerate(machines, start=1):
                    fingerprint = (
                        "f" * 64
                        if expected_reason == "machine_config_fingerprint" and machine.name == "macmini"
                        else None
                    )
                    current = self.publish(
                        root,
                        machine,
                        fingerprint=fingerprint,
                        multiplier=index,
                        quota_updated_at="2026-08-04T0%d:00:00Z" % index,
                        quota_pct=index * 10,
                    )
                    if machine.name == "macmini" and expected_reason != "machine_config_fingerprint":
                        corrupt(current)
                    current.close()

                with self.rollup_admission(root, config_path):
                    totals = self.metric_totals()
                    options = rollup.filter_options()
                admission_snapshot = generation.generation_admission_snapshot
                with mock.patch(
                    "server.generation.generation_admission_snapshot",
                    side_effect=lambda: admission_snapshot(
                        config_path=config_path,
                        root=root,
                    ),
                ):
                    limits = server._rate_limits()
                    status = server._sync_status(
                        now=datetime(2026, 8, 4, 12, 20, tzinfo=timezone.utc)
                    )

                self.assertEqual(totals, self.expected_metrics(4))
                self.assertEqual(options["machine"], ["dgx0023", "macbook"])
                self.assertEqual(status["coverage"], {"admitted": 2, "declared": 3})
                excluded = self.machine_status(status, "macmini")
                self.assertFalse(excluded["admitted"])
                self.assertIsNone(excluded["availability"])
                self.assertEqual(excluded["exclusion_reason"], expected_reason)
                quota = limits["claude"]["accounts"]
                self.assertEqual(len(quota), 1)
                self.assertEqual(quota[0]["five_hour_used_pct"], 30)
                self.assertEqual(quota[0]["machines"], ["dgx0023", "macbook"])
                # The excluded machine contributes neither its value nor its name.
                self.assertNotEqual(quota[0]["five_hour_used_pct"], 20)
                self.assertNotIn("macmini", quota[0]["machines"])

    def install_wrong_fingerprint(self, root, machine):
        self.publish(root, machine, fingerprint="f" * 64)

    def install_wrong_timezone(self, root, machine):
        current = self.publish(root, machine)
        meta_path = current.generation_dir / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["bucket_timezone"] = "UTC"
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
        current.close()

    def install_wrong_generation_id(self, root, machine):
        current = self.publish(root, machine)
        meta_path = current.generation_dir / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["generation_id"] = "e" * 64
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
        current.close()

    def install_wrong_digest(self, root, machine):
        current = self.publish(root, machine)
        self.corrupt_digest(current)
        current.close()

    @staticmethod
    def corrupt_fingerprint(current):
        meta_path = current.generation_dir / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["machine_config_fingerprint"] = "f" * 64
        meta_path.write_text(json.dumps(meta), encoding="utf-8")

    @staticmethod
    def corrupt_timezone(current):
        meta_path = current.generation_dir / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["bucket_timezone"] = "UTC"
        meta_path.write_text(json.dumps(meta), encoding="utf-8")

    @staticmethod
    def corrupt_generation_id(current):
        meta_path = current.generation_dir / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["generation_id"] = "e" * 64
        meta_path.write_text(json.dumps(meta), encoding="utf-8")

    @staticmethod
    def corrupt_digest(current):
        with current.db_path.open("ab") as handle:
            handle.write(b"changed")

    def publish(
        self,
        root,
        machine,
        fingerprint=None,
        multiplier=1,
        quota_updated_at="2026-08-04T10:00:00Z",
        quota_pct=10,
        published_at=None,
    ):
        root.mkdir(parents=True, exist_ok=True)
        source = root.parent / ("source-%s.db" % time.monotonic_ns())
        self.make_db(source, multiplier=multiplier)
        meta = generation.build_generation_meta(
            source,
            machine_config_fingerprint=fingerprint or machine_config_fingerprint(machine),
            source_host_identity="host-v1:" + hashlib.sha256(machine.name.encode()).hexdigest(),
            aliases=[],
            rate_limits={
                "claude": {
                    "five_hour_pct": quota_pct,
                    "five_hour_resets_at": 1,
                    "seven_day_pct": quota_pct + 1,
                    "seven_day_resets_at": 2,
                    "updated_at": quota_updated_at,
                    # One Claude account across the fleet, as in the real setup:
                    # the machines differ, the counter they report does not.
                    "account_id": "claude-acct",
                    "account_label": "fleet@example.com",
                    "account_plan": None,
                }
            },
            exporter_commit="a" * 40,
            generated_at="2026-08-04T12:00:00Z",
        )
        return generation.publish_generation(
            machine.name,
            source,
            meta,
            root=root,
            now=published_at or datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
        )

    @staticmethod
    def make_db(path, multiplier=1):
        with closing(sqlite3.connect(path)) as conn:
            with conn:
                conn.executescript(rollup.SCHEMA)
                conn.execute("DROP TABLE daily_usage_rollup")
                conn.execute(
                    "INSERT INTO rollup_meta (key, value) VALUES ('bucket_timezone', 'Asia/Shanghai')"
                )
                conn.execute(
                    """
                    INSERT INTO daily_rollup (
                      date, agent_id, project, model, input_tokens, output_tokens,
                      cache_creation_tokens, cache_read_tokens, cost_usd,
                      cost_known_count, entry_count, message_count
                    ) VALUES (?, 'codex', ?, 'gpt-5', ?, ?, ?, ?, ?, 1, 1, ?)
                    """,
                    (
                        datetime.now(rollup.BUCKET_TIMEZONE).date().isoformat(),
                        "repo-%s" % multiplier,
                        10 * multiplier,
                        2 * multiplier,
                        3 * multiplier,
                        4 * multiplier,
                        1.5 * multiplier,
                        multiplier,
                    ),
                )

    @staticmethod
    def write_config(path, *machines):
        path.write_text(
            json.dumps(
                {
                    "machines": [machine.as_config_dict() for machine in machines],
                    "retired_names": [],
                }
            ),
            encoding="utf-8",
        )

    def three_machine_fixture(self, tmp):
        root = Path(tmp) / "generations"
        config_path = Path(tmp) / "machines.json"
        machines = (
            Machine("macbook", "macbook", True),
            Machine("macmini", "macmini", False),
            Machine("dgx0023", "dgx0023", False),
        )
        self.write_config(config_path, *machines)
        return root, config_path, machines

    @staticmethod
    @contextlib.contextmanager
    def rollup_admission(root, config_path):
        def load():
            currents = generation.admitted_generations(config_path=config_path, root=root)
            return tuple(
                rollup.AdmittedGeneration(current.host, current.db_path, current)
                for current in currents
            )

        with mock.patch("rollup.admitted_generations", side_effect=load):
            yield

    @staticmethod
    def metric_totals():
        return {
            metric: rollup.query_pivot("agent", "none", metric)["rows"][0]["values"]["value"]
            for metric in sorted(rollup.METRICS)
        }

    @staticmethod
    def machine_metric_totals(machine):
        return {
            metric: rollup.query_pivot("agent", "none", metric, machines={machine})["rows"][0]["values"]["value"]
            for metric in sorted(rollup.METRICS)
        }

    @staticmethod
    def expected_metrics(multiplier_sum):
        return {
            "cache_creation": 3 * multiplier_sum,
            "cache_read": 4 * multiplier_sum,
            "cost": 1.5 * multiplier_sum,
            "input": 10 * multiplier_sum,
            "messages": multiplier_sum,
            "output": 2 * multiplier_sum,
            "total": 19 * multiplier_sum,
        }

    @staticmethod
    def machine_status(status, name):
        return next(machine for machine in status["machines"] if machine["name"] == name)

    @staticmethod
    def fake_admission(machine, published_at):
        current = SimpleNamespace(
            host=machine.name,
            meta={
                "published_at": published_at,
                "generated_at": published_at,
                "data_start_date": "2026-08-04",
                "generation_id": "a" * 64,
            },
        )
        return SimpleNamespace(
            config=SimpleNamespace(machines=(machine,)),
            admitted=(current,),
            records=(generation.AdmissionRecord(machine, True, False, current=current),),
        )

    def status(self, root, config_path, errors=None, running=False, now=None):
        admission_snapshot = generation.generation_admission_snapshot
        with mock.patch(
            "server.generation.generation_admission_snapshot",
            side_effect=lambda: admission_snapshot(
                config_path=config_path,
                root=root,
            ),
        ), mock.patch(
            "server._sync_runtime_snapshot",
            return_value={
                "syncing": running,
                "terminal": not running,
                "started_at": None,
                "completed_at": None,
                "round_machines": tuple(
                    record.machine.name for record in admission.records
                ) if running else (),
                "observations": {},
                "errors": errors or {},
            },
        ):
            return server._sync_status(
                now=now or datetime(2026, 8, 4, 12, 20, tzinfo=timezone.utc)
            )

    @staticmethod
    def status_from_admission(admission, *, running, errors, now):
        with mock.patch(
            "server.generation.generation_admission_snapshot",
            return_value=contextlib.nullcontext(admission),
        ), mock.patch(
            "server._sync_runtime_snapshot",
            return_value={
                "syncing": running,
                "terminal": not running,
                "started_at": None,
                "completed_at": None,
                "round_machines": tuple(
                    record.machine.name for record in admission.records
                ) if running else (),
                "pending_machines": tuple(
                    record.machine.name for record in admission.records
                ) if running else (),
                "observations": {},
                "errors": errors,
            },
        ):
            return server._sync_status(now=now)


class FrontendStatusRenderTests(unittest.TestCase):
    def test_sessions_latest_request_wins_and_invalid_rows_render_error(self):
        script = r'''
const fs = require("fs");

function makeNode(overrides = {}) {
  const node = {
    value: "", textContent: "", disabled: false, className: "", dataset: {},
    attributes: {}, children: [], listeners: {}, options: [{ textContent: "All" }],
    style: { setProperty() {} },
    addEventListener(name, callback) { this.listeners[name] = callback; },
    appendChild(child) { this.children.push(child); return child; },
    setAttribute(name, value) { this.attributes[name] = String(value); },
    getAttribute(name) { return this.attributes[name] || null; },
    querySelector(selector) {
      return selector === "thead" ? { getBoundingClientRect() { return { height: 32 }; } } : null;
    },
  };
  let html = "";
  Object.defineProperty(node, "innerHTML", {
    get() { return html; },
    set(value) { html = String(value); this.children = []; },
  });
  return Object.assign(node, overrides);
}

const nodes = {
  "#range": makeNode({ value: "30d" }),
  "#sort": makeNode({ value: "time" }),
  "#filter-agent": makeNode(),
  "#filter-project": makeNode(),
  "#filter-model": makeNode(),
  "#page-prev": makeNode(),
  "#page-next": makeNode(),
  "#page-status": makeNode(),
  "#session-count": makeNode(),
  "#refresh": makeNode({ textContent: "Refresh" }),
  "#sessions-body": makeNode(),
  ".table-wrap.sticky-head": makeNode(),
};

global.window = {
  location: { origin: "http://example.test", pathname: "/sessions", search: "?range=30d" },
  history: { replaceState() {} },
};
global.document = {
  readyState: "loading",
  addEventListener() {},
  querySelector(selector) { return nodes[selector] || null; },
  querySelectorAll() { return []; },
  createElement() { return makeNode(); },
  createTextNode(text) { return { textContent: text }; },
};

const pending = [];
global.fetch = (url) => {
  const path = new URL(String(url), window.location.origin).pathname;
  if (path === "/api/timezone") {
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ timezone: "UTC" }) });
  }
  if (path === "/api/sessions") {
    return new Promise((resolve, reject) => pending.push({ resolve, reject }));
  }
  if (path === "/api/refresh" || path === "/api/sync-status") {
    return Promise.resolve(response({ instance_id: "hub", refresh_requested: 1, refresh_completed: 1 }));
  }
  throw new Error(path);
};

function response(payload) {
  return { ok: true, statusText: "", json: () => Promise.resolve(payload) };
}
function session(id, project, legacy = false) {
  const row = {
    session_id: id, agent_id: "codex", project, model: "gpt-5",
    started_at: "2026-08-19T01:00:00Z", tokens: 10,
    cost_usd: null, estimated: false,
  };
  row[legacy ? "messages" : "usage_events"] = 2;
  return row;
}
function flush() {
  return new Promise((resolve) => setImmediate(resolve));
}
function state() {
  const row = nodes["#sessions-body"].children[0];
  return row && row.dataset ? row.dataset.sessionState || null : null;
}

eval(fs.readFileSync("web/app.js", "utf8"));
(async () => {
  const firstLoad = window.AgentMonitor.initSessions();
  await flush();
  nodes["#range"].value = "7d";
  nodes["#range"].listeners.change();
  await flush();

  // Static assets are read from disk while the Python process is frozen. A new
  // app.js must accept the old server shape until restart completes.
  pending[1].resolve(response([session("new", "/new-project", true)]));
  await flush();
  await flush();
  const afterNew = {
    state: state(), count: nodes["#session-count"].textContent,
    filterDisabled: nodes["#filter-agent"].disabled,
    rowHtml: nodes["#sessions-body"].children.find((row) => row.className === "session-row")?.innerHTML || "",
  };

  pending[0].resolve(response([session("old", "/old-project")]));
  await firstLoad;
  await flush();
  const afterLateSuccess = {
    state: state(), count: nodes["#session-count"].textContent,
    filterDisabled: nodes["#filter-agent"].disabled,
    rowHtml: nodes["#sessions-body"].children.find((row) => row.className === "session-row")?.innerHTML || "",
  };

  nodes["#refresh"].listeners.click();
  await flush();
  nodes["#range"].value = "30d";
  nodes["#range"].listeners.change();
  await flush();
  pending[3].resolve(response([session("newer", "/newer-project")]));
  await flush();
  await flush();
  const afterNewer = {
    state: state(), count: nodes["#session-count"].textContent,
    filterDisabled: nodes["#filter-agent"].disabled,
    rowHtml: nodes["#sessions-body"].children.find((row) => row.className === "session-row")?.innerHTML || "",
  };
  pending[2].reject(new Error("late failure"));
  await flush();
  const afterLateFailure = {
    state: state(), count: nodes["#session-count"].textContent,
    filterDisabled: nodes["#filter-agent"].disabled,
    rowHtml: nodes["#sessions-body"].children.find((row) => row.className === "session-row")?.innerHTML || "",
  };

  nodes["#sort"].listeners.change();
  await flush();
  const loadingBeforeFilter = state();
  nodes["#filter-agent"].value = "claude-code";
  nodes["#filter-agent"].listeners.change();
  const loadingAfterFilter = state();
  pending[4].resolve(response([null]));
  await flush();
  await flush();
  const afterInvalid = {
    state: state(), status: nodes["#page-status"].textContent,
    filterDisabled: nodes["#filter-agent"].disabled,
  };

  process.stdout.write(JSON.stringify({
    afterNew, afterLateSuccess, afterNewer, afterLateFailure, loadingBeforeFilter, loadingAfterFilter, afterInvalid,
  }));
})().catch((error) => { console.error(error); process.exit(1); });
'''
        result = subprocess.run(
            ["node", "-e", script],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["afterNew"]["state"], None)
        self.assertEqual(payload["afterNew"]["count"], "1 个会话")
        self.assertFalse(payload["afterNew"]["filterDisabled"])
        self.assertIn("/new-project", payload["afterNew"]["rowHtml"])
        self.assertEqual(payload["afterLateSuccess"], payload["afterNew"])
        self.assertIn("/newer-project", payload["afterNewer"]["rowHtml"])
        self.assertEqual(payload["afterLateFailure"], payload["afterNewer"])
        self.assertEqual(payload["loadingBeforeFilter"], "loading")
        self.assertEqual(payload["loadingAfterFilter"], "loading")
        self.assertEqual(payload["afterInvalid"]["state"], "error")
        self.assertIn("无效行", payload["afterInvalid"]["status"])
        self.assertTrue(payload["afterInvalid"]["filterDisabled"])

    def test_g4_idle_page_adopts_unknown_after_same_version_server_restart(self):
        script = r'''
const fs = require("fs");
const nodes = {
  "#sync-coverage": { textContent: "" },
  "#sync-summary": { textContent: "" },
  "#sync-machines": { innerHTML: "" },
};
let reloads = 0;
global.window = {
  location: { origin: "http://example.test", pathname: "/", search: "", reload() { reloads += 1; } },
  history: { replaceState() {} },
};
global.document = {
  readyState: "loading",
  addEventListener() {},
  querySelector(selector) { return nodes[selector] || null; },
  querySelectorAll() { return []; },
};
const health = [
  { ok: true, web_signature: "same-version", instance_id: "process-B", stale: false },
];
const unknown = {
  instance_id: "process-B",
  coverage: { admitted: 1, declared: 1 }, all_machines: ["macbook"],
  syncing: false, terminal: true,
  machines: [{ name: "macbook", declared: true, this_machine: true, admitted: true,
    availability: "unknown", stale: false, syncing: false,
    reason: "Contact status unknown since server restart", exclusion_reason: null,
    last_sync_ts: "2026-08-04T11:00:00Z", generated_at: "2026-08-04T11:00:00Z",
    data_start_date: "2026-04-21", last_attempt_outcome: "unknown_since_restart",
    last_attempt_ts: null, last_successful_contact_ts: "2026-08-04T11:00:00Z" }],
};
global.fetch = (url) => {
  const path = new URL(String(url), window.location.origin).pathname;
  if (path === "/api/health") return Promise.resolve({ ok: true, json: () => Promise.resolve(health.shift()) });
  if (path === "/api/timezone") return Promise.resolve({ ok: true, json: () => Promise.resolve({ timezone: "UTC" }) });
  if (path === "/api/sync-status") return Promise.resolve({ ok: true, json: () => Promise.resolve(unknown) });
  throw new Error(path);
};
eval(fs.readFileSync("web/app.js", "utf8"));
const reachable = JSON.parse(JSON.stringify(unknown));
reachable.instance_id = "process-A";
reachable.machines[0].availability = "reachable";
reachable.machines[0].last_attempt_outcome = "success";
reachable.machines[0].reason = null;
window.AgentMonitor.renderSyncStatus(reachable);
(async () => {
  const before = nodes["#sync-machines"].innerHTML;
  await window.AgentMonitor.pollFreshness();
  const after = nodes["#sync-machines"].innerHTML;
  process.stdout.write(JSON.stringify({ before, after, reloads }));
})().catch((error) => { console.error(error); process.exit(1); });
'''
        result = subprocess.run(
            ["node", "-e", script],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertIn(">可用<", payload["before"])
        self.assertIn(">未检查<", payload["after"])
        self.assertIn("本服务启动后还没检查过这台机器", payload["after"])
        self.assertEqual(payload["reloads"], 0)

    def test_g6_stale_poll_cannot_overwrite_newer_sync_panel(self):
        script = r'''
const fs = require("fs");
const nodes = {
  "#sync-coverage": { textContent: "" },
  "#sync-summary": { textContent: "" },
  "#sync-machines": { innerHTML: "" },
};
global.window = {
  location: { origin: "http://example.test", pathname: "/explore", search: "" },
  history: { replaceState() {} },
};
global.document = {
  readyState: "loading",
  addEventListener() {},
  querySelector(selector) { return nodes[selector] || null; },
  querySelectorAll() { return []; },
};
global.setTimeout = (callback) => { callback(); return 1; };
let resolveOldPoll;
global.fetch = (url) => {
  const path = new URL(String(url), window.location.origin).pathname;
  if (path === "/api/timezone") return Promise.resolve({ ok: true, json: () => Promise.resolve({ timezone: "UTC" }) });
  if (path === "/api/sync-status") return new Promise((resolve) => { resolveOldPoll = resolve; });
  throw new Error(path);
};
eval(fs.readFileSync("web/app.js", "utf8"));
function status(name, availability, syncing, reason) {
  return {
    coverage: { admitted: 1, declared: 1 }, all_machines: [name], syncing, terminal: !syncing,
    machines: [{ name, declared: true, this_machine: false, admitted: true,
      availability, stale: false, syncing, reason: reason || null, exclusion_reason: null,
      last_sync_ts: "2026-08-04T11:00:00Z", generated_at: "2026-08-04T11:00:00Z",
      data_start_date: "2026-04-21", last_attempt_outcome: reason ? "failure" : "success",
      last_attempt_ts: "2026-08-04T11:01:00Z", last_successful_contact_ts: "2026-08-04T11:00:00Z" }],
  };
}
(async () => {
  let current = true;
  const oldWait = window.AgentMonitor.waitForSyncTerminal(
    status("old-load", "unknown", true, null),
    { isCurrent: () => current },
  );
  await new Promise((resolve) => setImmediate(resolve));
  current = false;
  window.AgentMonitor.renderSyncStatus(status("new-load", "reachable", false, null));
  resolveOldPoll({ ok: true, json: () => Promise.resolve(status("old-load", "unreachable", false, "late failure")) });
  await oldWait;
  process.stdout.write(JSON.stringify({ html: nodes["#sync-machines"].innerHTML, summary: nodes["#sync-summary"].textContent }));
})().catch((error) => { console.error(error); process.exit(1); });
'''
        result = subprocess.run(
            ["node", "-e", script],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertIn("new-load", payload["html"])
        self.assertNotIn("old-load", payload["html"])
        self.assertNotIn("late failure", payload["html"])

    def test_g6_poll_connection_failure_replaces_frozen_syncing_state_with_reason(self):
        script = r'''
const fs = require("fs");
const nodes = {
  "#sync-coverage": { textContent: "" },
  "#sync-summary": { textContent: "" },
  "#sync-machines": { innerHTML: "" },
};
global.window = {
  location: { origin: "http://example.test", pathname: "/", search: "" },
  history: { replaceState() {} },
};
global.document = {
  readyState: "loading",
  addEventListener() {},
  querySelector(selector) { return nodes[selector] || null; },
  querySelectorAll() { return []; },
};
global.setTimeout = (callback) => { callback(); return 1; };
global.fetch = () => Promise.reject(new Error("connection reset"));
eval(fs.readFileSync("web/app.js", "utf8"));
const initial = {
  coverage: { admitted: 1, declared: 1 },
  all_machines: ["macbook"],
  syncing: true,
  terminal: false,
  machines: [{
    name: "macbook", declared: true, this_machine: true, admitted: true,
    availability: "unknown", stale: false, syncing: true, reason: "Contact pending",
    exclusion_reason: null, last_sync_ts: "2026-08-04T11:00:00Z",
    generated_at: "2026-08-04T11:00:00Z", data_start_date: "2026-04-21",
    last_attempt_outcome: "unknown_since_restart", last_attempt_ts: null,
    last_successful_contact_ts: "2026-08-04T11:00:00Z",
  }],
};
window.AgentMonitor.waitForSyncTerminal(initial).then((result) => {
  process.stdout.write(JSON.stringify({ result, summary: nodes["#sync-summary"].textContent, html: nodes["#sync-machines"].innerHTML }));
}).catch((error) => { console.error(error); process.exit(1); });
'''
        result = subprocess.run(
            ["node", "-e", script],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["result"]["syncing"])
        self.assertFalse(payload["result"]["terminal"])
        self.assertIn("connection reset", payload["result"]["polling_error"])
        self.assertIn("同步状态不可用", payload["summary"])
        self.assertNotIn(">刷新中<", payload["html"])

    def test_g2_out_of_order_pivot_completion_cannot_render_under_new_machine_label(self):
        script = r'''
const fs = require("fs");
const controls = {};
for (const id of ["time-dim", "metric", "range", "agent-filter", "project-filter", "model-filter", "machine-filter"]) {
  controls[`#${id}`] = {
    id,
    value: id === "x-dim" ? "machine" : id === "group-dim" ? "none" : id === "metric" ? "cost" : id === "range" ? "30d" : "",
    innerHTML: "",
    selectedOptions: [{ textContent: "成本" }],
    disabled: false,
    listeners: {},
    addEventListener(name, callback) { this.listeners[name] = callback; },
    appendChild(option) {},
  };
}
const nodes = Object.assign(controls, {
  "#pivot-status": { textContent: "" },
  "#pivot-panel": {},
  "#ranking-panel": {},
  "#ranking-title": {},
  "#ranking-count": {},
  "#ranking-description": {},
  "#group-ranking": { set innerHTML(html) { rendered.push(html); } },
  "#pivot-chart": { closest() { return null; } },
});
let page = new URL("http://example.test/explore?x=machine&group=none&metric=cost&range=30d");
global.window = {
  get location() { return page; },
  history: { replaceState(_state, _title, target) { page = new URL(target, page.origin); } },
};
global.document = {
  readyState: "loading",
  addEventListener() {},
  createElement() { return { value: "", textContent: "" }; },
};
let resolveA;
let resolveB;
const rendered = [];
global.AgentMonitor = {
  qs(selector) { return nodes[selector] || null; },
  qsa(selector) { return selector === "[data-filter-control]" ? [controls["#agent-filter"], controls["#project-filter"], controls["#model-filter"], controls["#machine-filter"]] : []; },
  params() { return new URLSearchParams(page.search); },
  getRange() { return controls["#range"].value; },
  autoTimeDim() { return "day"; },
  setParam(key, value) { value ? page.searchParams.set(key, value) : page.searchParams.delete(key); },
  // Pivot rewrites the URL through the shared writer rather than calling
  // replaceState itself, so the navigation layer's idea of the current route
  // follows it. Mirrors what the real one does to the address: the filter state
  // this test drives travels through `page`.
  replaceCurrentUrl(target) { page = new URL(target, page.origin); },
  bindShell() {},
  renderSyncStatus() {},
  waitForSyncTerminal(status) { return Promise.resolve(status); },
  pageScope: () => () => true,
  watchPageData() {},
  integer(value) { return String(value); },
  moneyPrecise(value) { return String(value); },
  dataset(label, data) { return { label, data }; },
  chart(_key, _id, config) { rendered.push(config.data.labels[0]); },
  chartOptions(value) { return value; },
  api(path, query) {
    if (path === "/api/sync-status") return Promise.resolve({ syncing: false, completed_at: null, machines: [] });
    if (path === "/api/pivot-filters") return Promise.resolve({ machine: ["A", "B"] });
    if (path === "/api/pivot") {
      const machine = query.machine || "initial";
      if (machine === "A") return new Promise((resolve) => { resolveA = resolve; });
      if (machine === "B") return new Promise((resolve) => { resolveB = resolve; });
      return Promise.resolve({ dimensions: ["machine"], rows: [{ key: ["initial-data"], value: 0 }] });
    }
    throw new Error(path);
  },
};
eval(fs.readFileSync("web/pivot.js", "utf8"));
(async () => {
  await window.AgentMonitorPivot.init();
  controls["#machine-filter"].value = "A";
  const requestA = controls["#machine-filter"].listeners.change();
  await new Promise((resolve) => setImmediate(resolve));
  controls["#machine-filter"].value = "B";
  const requestB = controls["#machine-filter"].listeners.change();
  await new Promise((resolve) => setImmediate(resolve));
  resolveB({ dimensions: ["machine"], rows: [{ key: ["B-data"], value: 2 }] });
  await requestB;
  resolveA({ dimensions: ["machine"], rows: [{ key: ["A-data"], value: 1 }] });
  await requestA;
  process.stdout.write(JSON.stringify({ rendered, selected: controls["#machine-filter"].value, search: page.search }));
})().catch((error) => { console.error(error); process.exit(1); });
'''
        result = subprocess.run(
            ["node", "-e", script],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["selected"], "B")
        self.assertIn("machine=B", payload["search"])
        self.assertEqual(len(payload["rendered"]), 2)
        self.assertIn("initial-data", payload["rendered"][0])
        self.assertIn("B-data", payload["rendered"][1])
        self.assertNotIn("A-data", "".join(payload["rendered"]))

    def test_g4_renderer_preserves_orthogonal_statuses_and_exclusion_consequences(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(generation.GenerationValidationError) as rejected:
                generation.bind_source_identity(
                    "dgx0023",
                    "host-v1:" + "7" * 64,
                    root=Path(tmp) / "generations",
                )
        first_use_reason = str(rejected.exception)
        script = r'''
const fs = require("fs");
const firstUseReason = __FIRST_USE_REASON__;
const nodes = {
  "#sync-coverage": { textContent: "" },
  "#sync-summary": { textContent: "" },
  "#sync-machines": { innerHTML: "" },
};
global.window = {
  location: { origin: "http://example.test", pathname: "/", search: "" },
  history: { replaceState() {} },
};
global.document = {
  readyState: "loading",
  addEventListener() {},
  querySelector(selector) { return nodes[selector] || null; },
  querySelectorAll() { return []; },
};
eval(fs.readFileSync("web/app.js", "utf8"));
function base(name) {
  return {
    name,
    this_machine: name === "macbook",
    admitted: true,
    availability: "reachable",
    stale: false,
    syncing: false,
    reason: null,
    exclusion_reason: null,
    last_sync_ts: "2026-08-04T11:00:00Z",
    generated_at: "2026-08-04T10:59:00Z",
    data_start_date: "2026-07-01",
    totals_schema_version: 2,
    generation_totals_basis: "usage_with_legacy_project_fallback",
    generation_legacy_fallback_bucket_count: 7,
    generation_legacy_fallback_bucket_dimensions: ["date", "agent", "model"],
    last_attempt_outcome: "success",
    last_attempt_ts: "2026-08-04T11:00:30Z",
    last_successful_contact_ts: "2026-08-04T11:00:30Z",
  };
}
const staleFailure = base("macmini");
Object.assign(staleFailure, { availability: "unreachable", stale: true, syncing: true, reason: "ssh timeout" });
window.AgentMonitor.renderSyncStatus({ coverage: { admitted: 3, declared: 3 }, all_machines: ["macbook", "macmini", "dgx0023"], syncing: true, machines: [base("macbook"), staleFailure] });
const combined = { coverage: nodes["#sync-coverage"].textContent, summary: nodes["#sync-summary"].textContent, html: nodes["#sync-machines"].innerHTML };
const never = base("dgx0023");
Object.assign(never, { admitted: false, availability: "never", reason: firstUseReason, last_attempt_outcome: "failure", last_sync_ts: null, generated_at: null, data_start_date: null });
window.AgentMonitor.renderSyncStatus({ coverage: { admitted: 2, declared: 3 }, all_machines: ["macbook", "macmini"], syncing: false, machines: [never] });
const excluded = { coverage: nodes["#sync-coverage"].textContent, html: nodes["#sync-machines"].innerHTML };
const recentFailure = base("macbook");
Object.assign(recentFailure, { availability: "unreachable", reason: "connection refused" });
window.AgentMonitor.renderSyncStatus({ coverage: { admitted: 1, declared: 1 }, all_machines: ["macbook"], syncing: false, machines: [recentFailure] });
const recent = nodes["#sync-machines"].innerHTML;
const cleanupFailure = base("macbook");
Object.assign(cleanupFailure, { availability: "reachable", last_attempt_outcome: "cleanup_failed", reason: "close failed" });
window.AgentMonitor.renderSyncStatus({ coverage: { admitted: 1, declared: 1 }, all_machines: ["macbook"], syncing: false, machines: [cleanupFailure] });
const cleanup = nodes["#sync-machines"].innerHTML;
const unknown = base("macmini");
Object.assign(unknown, { availability: "unknown", last_attempt_outcome: "unknown_since_restart", last_attempt_ts: null });
window.AgentMonitor.renderSyncStatus({ coverage: { admitted: 1, declared: 1 }, all_machines: ["macmini"], syncing: false, machines: [unknown] });
const unchecked = nodes["#sync-machines"].innerHTML;
window.AgentMonitor.renderSyncStatus({ coverage: { admitted: 1, declared: 1 }, all_machines: ["macbook"], syncing: false, machines: [base("macbook")] });
const healthy = { coverage: nodes["#sync-coverage"].textContent, summary: nodes["#sync-summary"].textContent, html: nodes["#sync-machines"].innerHTML };
const legacy = base("macbook");
Object.assign(legacy, { totals_schema_version: 1, generation_totals_basis: "legacy_project_derived", generation_legacy_fallback_bucket_count: null, generation_legacy_fallback_bucket_dimensions: null });
window.AgentMonitor.renderSyncStatus({ coverage: { admitted: 1, declared: 1 }, all_machines: ["macbook"], syncing: false, machines: [legacy] });
const legacyHtml = nodes["#sync-machines"].innerHTML;
const pure = base("macbook");
Object.assign(pure, { generation_totals_basis: "project_independent_usage", generation_legacy_fallback_bucket_count: 0 });
window.AgentMonitor.renderSyncStatus({ coverage: { admitted: 1, declared: 1 }, all_machines: ["macbook"], syncing: false, machines: [pure] });
const pureHtml = nodes["#sync-machines"].innerHTML;
const unavailable = base("macbook");
Object.assign(unavailable, { generation_totals_basis: null, generation_legacy_fallback_bucket_count: null });
window.AgentMonitor.renderSyncStatus({ coverage: { admitted: 1, declared: 1 }, all_machines: ["macbook"], syncing: false, machines: [unavailable] });
const unavailableHtml = nodes["#sync-machines"].innerHTML;
process.stdout.write(JSON.stringify({ combined, excluded, recent, cleanup, unchecked, healthy, legacyHtml, pureHtml, unavailableHtml }));
'''.replace("__FIRST_USE_REASON__", json.dumps(first_use_reason))
        result = subprocess.run(
            ["node", "-e", script],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        rendered = json.loads(result.stdout)

        self.assertEqual(rendered["combined"]["coverage"], "已纳入 3/3 台")
        self.assertEqual(rendered["combined"]["summary"], "后台更新统计与配额 · 新数据陆续显示")
        for text in (
            "刷新中",
            "刷新失败",
            "数据过期",
            "用的是上次的数据",
            "ssh timeout",
            "本机",
            "最后尝试",
            "最后联系",
        ):
            self.assertIn(text, rendered["combined"]["html"])
        self.assertEqual(rendered["excluded"]["coverage"], "已纳入 2/3 台")
        self.assertIn("未纳入", rendered["excluded"]["html"])
        self.assertIn("尚无数据", rendered["excluded"]["html"])
        self.assertIn("agent-monitor machines accept dgx0023", rendered["excluded"]["html"])
        self.assertIn("SSH 目标", rendered["excluded"]["html"])
        self.assertNotIn("`", rendered["excluded"]["html"])
        for internal_term in (
            "TOFU",
            "machine slot",
            "pins",
            "never synced",
            "no generation is available",
            "最近一次刷新失败",
        ):
            self.assertNotIn(internal_term, rendered["excluded"]["html"])
        self.assertIn("connection refused", rendered["recent"])
        self.assertIn("用的是上次的数据", rendered["recent"])
        self.assertNotIn("数据过期", rendered["recent"])
        self.assertIn("数据已更新，但刷新后的清理失败：close failed", rendered["cleanup"])
        self.assertNotIn("数据过期", rendered["cleanup"])
        for text in (
            "未检查",
            "用的是上次的数据",
            "本服务启动后还没检查过这台机器",
            "最后联系",
        ):
            self.assertIn(text, rendered["unchecked"])
        self.assertNotIn("最后尝试", rendered["unchecked"])
        self.assertEqual(rendered["healthy"]["coverage"], "已纳入 1/1 台")
        self.assertEqual(rendered["healthy"]["summary"], "使用已采集数据 · 自动更新")
        for text in (
            "可用",
            "数据更新于",
            "历史自",
        ):
            self.assertIn(text, rendered["healthy"]["html"])
        for text in (
            ">included<",
            ">reachable<",
            "Included in All",
            "last sync",
            "最后尝试",
            "最后联系",
        ):
            self.assertNotIn(text, rendered["healthy"]["html"])
        # The internal totals-derivation basis is deliberately not rendered. Three
        # fixtures differing only in that basis must therefore be indistinguishable
        # on screen — which is a stronger claim than "the English spelling is gone",
        # and the one that fails if any wording of it comes back.
        self.assertEqual(rendered["legacyHtml"], rendered["pureHtml"])
        self.assertEqual(rendered["legacyHtml"], rendered["unavailableHtml"])

    def test_g4_terminal_attempt_failures_raise_attention_without_overstating_contact(self):
        script = r'''
const fs = require("fs");
const nodes = {
  "#sync-coverage": { textContent: "" },
  "#sync-summary": { textContent: "" },
  "#sync-machines": { innerHTML: "" },
  "#sync-panel": { open: false },
};
global.window = {
  location: { origin: "http://example.test", pathname: "/", search: "" },
  history: { replaceState() {} },
};
global.document = {
  readyState: "loading",
  addEventListener() {},
  querySelector(selector) { return nodes[selector] || null; },
  querySelectorAll() { return []; },
};
eval(fs.readFileSync("web/app.js", "utf8"));
function machine(overrides) {
  return Object.assign({
    name: "macbook",
    this_machine: true,
    admitted: true,
    availability: "reachable",
    stale: false,
    syncing: false,
    reason: null,
    exclusion_reason: null,
    generated_at: "2026-08-04T10:59:00Z",
    data_start_date: "2026-07-01",
    last_attempt_outcome: "success",
    last_attempt_ts: "2026-08-04T11:00:30Z",
    last_successful_contact_ts: "2026-08-04T10:55:00Z",
  }, overrides);
}
function render(overrides) {
  window.AgentMonitor.renderSyncStatus({
    coverage: { admitted: 1, declared: 1 },
    syncing: false,
    machines: [machine(overrides)],
  });
  return {
    summary: nodes["#sync-summary"].textContent,
    html: nodes["#sync-machines"].innerHTML,
    open: nodes["#sync-panel"].open,
  };
}
const reachableFailure = render({
  last_attempt_outcome: "failure",
  reason: "worker crashed",
});
const unknownFailure = render({
  availability: "unknown",
  last_attempt_outcome: "malformed_result",
  reason: "malformed sync result",
  last_successful_contact_ts: null,
});
const cleanupFailure = render({
  last_attempt_outcome: "cleanup_failed",
  reason: "close failed",
});
process.stdout.write(JSON.stringify({ reachableFailure, unknownFailure, cleanupFailure }));
'''
        result = subprocess.run(
            ["node", "-e", script],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        rendered = json.loads(result.stdout)

        self.assertEqual(rendered["reachableFailure"]["summary"], "1 台需要关注")
        self.assertTrue(rendered["reachableFailure"]["open"])
        self.assertIn("刷新失败", rendered["reachableFailure"]["html"])
        self.assertIn("最近一次刷新失败：worker crashed", rendered["reachableFailure"]["html"])
        self.assertNotIn("最新", rendered["reachableFailure"]["html"])
        self.assertEqual(rendered["unknownFailure"]["summary"], "1 台需要关注")
        self.assertIn("刷新失败", rendered["unknownFailure"]["html"])
        self.assertIn("最后尝试", rendered["unknownFailure"]["html"])
        self.assertIn("最近一次刷新失败：malformed sync result", rendered["unknownFailure"]["html"])
        self.assertNotIn("本服务启动后还没检查过这台机器", rendered["unknownFailure"]["html"])
        self.assertEqual(rendered["cleanupFailure"]["summary"], "1 台需要关注")
        self.assertIn("清理失败", rendered["cleanupFailure"]["html"])
        self.assertNotIn("最新", rendered["cleanupFailure"]["html"])


class WriteSetTests(unittest.TestCase):
    publish = AdmissionStatusTests.publish
    make_db = staticmethod(AdmissionStatusTests.make_db)
    write_config = staticmethod(AdmissionStatusTests.write_config)

    def test_iv18_page_load_refresh_and_export_only_write_derived_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = Path(tmp)
            state_root = sandbox / "state"
            sessions_root = sandbox / "sessions"
            project_root = sandbox / "project-worktree"
            sessions_root.mkdir()
            project_root.mkdir()
            (sessions_root / "session.jsonl").write_text('{"usage":"source"}\n', encoding="utf-8")
            (project_root / "tracked.txt").write_text("business data\n", encoding="utf-8")
            generations_root = state_root / "generations"
            config_path = sandbox / "machines.json"
            machine = Machine("macbook", "macbook", True)
            self.write_config(config_path, machine)
            self.publish(
                generations_root,
                machine,
                published_at=datetime.now(timezone.utc),
            ).close()
            db_path = state_root / "rollup.db"
            self.make_db(db_path)
            entry = UsageEntry(
                timestamp=datetime.now(timezone.utc),
                session_id="s1",
                message_id="m1",
                request_id="r1",
                model="gpt-5",
                input_tokens=10,
                output_tokens=2,
                cache_creation_tokens=0,
                cache_read_tokens=0,
                cost_usd=1.0,
                project=str(project_root),
                agent_id="codex",
            )
            clean_manifest = self.tree_manifest(sandbox)
            session_manifest = self.tree_manifest(sessions_root)
            project_manifest = self.tree_manifest(project_root)
            admission_snapshot = generation.generation_admission_snapshot
            source_identity = "host-v1:" + hashlib.sha256(machine.name.encode()).hexdigest()

            def sync_self(**_kwargs):
                current = sync.sync_machine(
                    machine,
                    db_path=db_path,
                    root=generations_root,
                    export_kwargs={
                        "entries_loader": lambda: [entry],
                        "rate_limits": {},
                    },
                )
                return {machine.name: sync.SyncResult(generation=current)}

            with mock.patch(
                "server.generation.generation_admission_snapshot",
                side_effect=lambda: admission_snapshot(
                    config_path=config_path,
                    root=generations_root,
                ),
            ), mock.patch("server.sync.sync_all", side_effect=sync_self), mock.patch(
                "exporter.exporter_version", return_value="a" * 40
            ), mock.patch(
                "generation.self_certified_host_identity", return_value=source_identity
            ):
                server._reset_sync_state_for_tests()
                server.overview({})
                after_load = self.tree_manifest(sandbox)
                server.overview({"force": ["1"]})
                deadline = time.monotonic() + 2
                while server._sync_runtime_snapshot()["syncing"] and time.monotonic() < deadline:
                    time.sleep(0.01)
                after_refresh = self.tree_manifest(sandbox)
                self.assertEqual(server._sync_runtime_snapshot()["errors"], {})

            self.assert_write_subset(clean_manifest, after_load, "state/")
            self.assert_write_subset(after_load, after_refresh, "state/")
            self.assertNotEqual(after_load, after_refresh)
            output_path = state_root / "export-bundle"
            with mock.patch("exporter.exporter_version", return_value="a" * 40):
                exporter.export_bundle(
                    db_path=db_path,
                    output_path=output_path,
                    entries_loader=lambda: [entry],
                    source_host_identity="host-v1:" + "1" * 64,
                    rate_limits={},
                )
            after_export = self.tree_manifest(sandbox)

            self.assert_write_subset(after_refresh, after_export, "state/")
            self.assertEqual(self.tree_manifest(sessions_root), session_manifest)
            self.assertEqual(self.tree_manifest(project_root), project_manifest)

    @staticmethod
    def tree_manifest(root):
        root = Path(root)
        result = {}
        for path in sorted(root.rglob("*")):
            if path.is_file():
                result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        return result

    def assert_write_subset(self, before, after, prefix):
        changed = {
            path
            for path in set(before) | set(after)
            if before.get(path) != after.get(path)
        }
        self.assertTrue(all(path.startswith(prefix) for path in changed), changed)


if __name__ == "__main__":
    unittest.main()
