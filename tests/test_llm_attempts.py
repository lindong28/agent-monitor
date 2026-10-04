import http.client
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
import gateway_dependency

from llm_gateway.ledger import V3_SCHEMA_SQL  # noqa: E402

import llm_attempts  # noqa: E402
import server  # noqa: E402


NOW = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
SESSION = "claude:12345678-1234-4123-8123-123456789abc"
CORRUPT_LEDGER_FAMILIES = (
    "request_identity_state",
    "request_capability_vocabulary",
    "request_attempt_topology",
    "request_outcome_truth",
    "credential_route_identity",
    "billing_scope_vocabulary",
    "funding_type_vocabulary",
    "billing_scope_unassigned",
    "funding_type_unassigned",
    "route_capability_vocabulary",
    "usage_outcome_error",
    "dispatch_cost",
    "pricing_tuple",
    "timestamp_public_shape",
)


class LedgerFixture:
    def __init__(self, path):
        self.path = Path(path)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.executescript(V3_SCHEMA_SQL)

    def request(
        self,
        logical_request_id,
        timestamp,
        *,
        project="philo-prompt",
        model="claude-text",
        outcome="success",
        reject_reason=None,
        session_ref=SESSION,
        active_attempt_id=None,
        requested_capabilities=("text",),
    ):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            cursor = connection.execute(
                """INSERT INTO logical_requests
                   (request_timestamp, canonical_project_id, logical_request_id,
                    session_ref, logical_model, requested_capabilities_json,
                    admission_revision, requested_route_id, route_selection_source,
                    preselection_candidates_json, request_outcome,
                    request_reject_reason, active_attempt_id)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    timestamp,
                    project,
                    logical_request_id,
                    session_ref,
                    model,
                    json.dumps(list(requested_capabilities)),
                    "registry-rev-1",
                    None,
                    "policy",
                    "[]",
                    outcome,
                    reject_reason,
                    active_attempt_id,
                ),
            )
            return cursor.lastrowid

    def attempt(
        self,
        request_fk,
        attempt_id,
        timestamp,
        *,
        provider="tokeneum",
        profile="tokeneum-claude-api",
        route="tokeneum-claude-api",
        actual_model="claude-sonnet-4-5",
        outcome="success",
        cost_state="exact",
        cost_value=1.25,
        cost_basis="usd_per_request",
        cost_currency="USD",
        funding_type="company_paid",
        billing_scope="company",
        route_verified_capabilities=("text",),
        credential_source_kind="env_assignment_name",
        credential_source_ref="TOKEN_EUM_API_KEY_CLAUDE",
        attempt_no=1,
        parent_attempt_id=None,
        dispatch_boundary="crossed",
        latency_ms=125.5,
        error_class=None,
        http_status=None,
    ):
        credential = {
            "provider_id": provider,
            "credential_profile_id": profile,
            "auth_type": "bearer",
            "credential_source_kind": credential_source_kind,
            "credential_source_ref": credential_source_ref,
            "billing_scope": billing_scope,
            "funding_type": funding_type,
        }
        usage = (
            {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
            if outcome == "success"
            else None
        )
        provenance = None
        if cost_state == "exact":
            provenance = "provider_reported_charge"
        elif cost_state == "estimated":
            provenance = "litellm_estimate"
        if cost_state not in {"exact", "estimated"}:
            cost_value = cost_basis = cost_currency = None
        if outcome == "http_error" and http_status is None:
            http_status = 503
        if outcome not in {"success", "in_flight"} and error_class is None:
            error_class = outcome
        with closing(sqlite3.connect(self.path)) as connection, connection:
            cursor = connection.execute(
                """INSERT INTO attempts
                   (attempt_id, logical_request_fk, attempt_no, parent_attempt_id,
                    run_id, attempt_timestamp, routing_revision,
                    route_selection_source, route_id, actual_model, provider_id,
                    credential_profile_id, credential_source_json, transport,
                    route_verified_capabilities_json, generation_controls_json,
                    dispatch_boundary, outcome, latency_ms, usage_state,
                    usage_json, cost_state, cost_value, cost_basis, cost_currency,
                    cost_provenance, pricing_state, error_class, http_status)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    attempt_id,
                    request_fk,
                    attempt_no,
                    parent_attempt_id,
                    "run-1",
                    timestamp,
                    "registry-rev-1",
                    "fallback" if attempt_no > 1 else "policy",
                    route,
                    actual_model,
                    provider,
                    profile,
                    json.dumps(credential),
                    "litellm",
                    json.dumps(list(route_verified_capabilities)),
                    "{}",
                    dispatch_boundary,
                    outcome,
                    latency_ms,
                    "reported" if usage else "not_reported",
                    json.dumps(usage) if usage else None,
                    cost_state,
                    cost_value,
                    cost_basis,
                    cost_currency,
                    provenance,
                    "not_consulted",
                    error_class,
                    http_status,
                ),
            )
            return cursor.lastrowid


def valid_completed_ledger(path):
    fixture = LedgerFixture(path)
    request_fk = fixture.request(
        "valid-request", "2026-08-31T01:00:00+00:00", outcome="success"
    )
    fixture.attempt(
        request_fk,
        "valid-attempt",
        "2026-08-31T01:01:00+00:00",
        outcome="success",
    )
    return fixture


def corrupt_ledger(path, family):
    with closing(sqlite3.connect(path)) as connection, connection:
        if family == "request_identity_state":
            connection.execute(
                "UPDATE logical_requests SET route_selection_source='explicit_pin', requested_route_id=NULL"
            )
        elif family == "request_capability_vocabulary":
            connection.execute(
                "UPDATE logical_requests SET requested_capabilities_json='[\"future-capability\"]'"
            )
        elif family == "request_attempt_topology":
            connection.execute("UPDATE attempts SET attempt_no=2")
        elif family == "request_outcome_truth":
            connection.execute("UPDATE logical_requests SET request_outcome='failed'")
        elif family == "credential_route_identity":
            raw = connection.execute(
                "SELECT credential_source_json FROM attempts"
            ).fetchone()[0]
            credential = json.loads(raw)
            credential["credential_profile_id"] = "different-profile"
            connection.execute(
                "UPDATE attempts SET credential_source_json=?",
                (json.dumps(credential),),
            )
        elif family in {
            "billing_scope_vocabulary",
            "funding_type_vocabulary",
            "billing_scope_unassigned",
            "funding_type_unassigned",
        }:
            raw = connection.execute(
                "SELECT credential_source_json FROM attempts"
            ).fetchone()[0]
            credential = json.loads(raw)
            if family == "billing_scope_vocabulary":
                credential["billing_scope"] = "future-value"
            elif family == "funding_type_vocabulary":
                credential["funding_type"] = "future-value"
            elif family == "billing_scope_unassigned":
                credential["billing_scope"] = "unassigned"
                credential["funding_type"] = "subscription"
            else:
                credential["billing_scope"] = "company"
                credential["funding_type"] = "unassigned"
            connection.execute(
                "UPDATE attempts SET credential_source_json=?",
                (json.dumps(credential),),
            )
        elif family == "route_capability_vocabulary":
            connection.execute(
                "UPDATE attempts SET route_verified_capabilities_json='[\"future-capability\"]'"
            )
        elif family == "usage_outcome_error":
            connection.execute(
                "UPDATE attempts SET usage_json=?",
                ('{"prompt_tokens":10,"completion_tokens":5,"total_tokens":1}',),
            )
        elif family == "dispatch_cost":
            connection.execute(
                "UPDATE attempts SET cost_provenance='litellm_estimate'"
            )
        elif family == "pricing_tuple":
            connection.execute("UPDATE attempts SET pricing_state='fresh'")
        elif family == "timestamp_public_shape":
            connection.execute(
                "UPDATE attempts SET attempt_timestamp='2026-08-31T01:01:00'"
            )
        else:
            raise AssertionError(f"unknown corruption family: {family}")


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.db_path = root / "audit.sqlite3"
        self.registry_path = root / "registry.json"
        self.registry_path.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "credential_profiles": [
                        {
                            "id": "tokeneum-claude-api",
                            "display_name": "TokenEum Claude API",
                        }
                    ],
                    "secret": "sk-test-canary-must-not-leak",
                }
            ),
            encoding="utf-8",
        )

    def calls(self, query=None, *, now=NOW):
        return llm_attempts.llm_calls(
            query or {},
            db_path=self.db_path,
            registry_path=self.registry_path,
            now=now,
        )

    def filters(self, query=None):
        return llm_attempts.llm_call_filters(
            query or {},
            db_path=self.db_path,
            registry_path=self.registry_path,
            now=NOW,
        )

    def fixture(self):
        return LedgerFixture(self.db_path)

    def test_missing_ledger_is_a_versioned_empty_state(self):
        payload = self.calls()

        self.assertEqual(payload["projection_version"], 4)
        self.assertEqual(payload["scope"], {"kind": "this_machine_only"})
        self.assertEqual(
            payload["ledger"], {"state": "missing", "schema_version": None}
        )
        self.assertIsNone(payload["high_watermark"]["request_ingest_sequence"])
        self.assertNotIn("distinct_requests", payload["request_summary"])
        self.assertNotIn("attempts", payload["attempt_summary"])
        self.assertEqual(payload["requests"]["matching_count"], 0)
        self.assertEqual(payload["attempts"]["matching_count"], 0)
        self.assertEqual(payload["requests"]["items"], [])
        self.assertEqual(payload["attempts"]["items"], [])
        self.assertFalse(self.db_path.exists(), "read-only endpoint created the ledger")

        options = self.filters()
        self.assertEqual(options["ledger"]["state"], "missing")
        self.assertEqual(options["request_dimensions"], {})
        self.assertEqual(options["attempt_dimensions"], {})
        self.assertFalse(self.db_path.exists())

    def test_request_selection_uses_semantic_timestamp_token(self):
        missing = self.calls({"range": ["7d"]})
        self.assertEqual(
            missing["request_selection"]["request_time_basis"],
            "request_timestamp",
        )

        valid_completed_ledger(self.db_path)
        available = self.calls({"range": ["7d"]})
        self.assertEqual(
            available["request_selection"]["request_time_basis"],
            "request_timestamp",
        )

    def test_dangling_ledger_symlink_is_an_error_not_an_empty_ledger(self):
        self.db_path.symlink_to(self.db_path.with_name("missing-target.sqlite3"))

        for endpoint in (self.calls, self.filters):
            with self.subTest(endpoint=endpoint.__name__):
                with self.assertRaises(llm_attempts.ProjectionError) as raised:
                    endpoint({"range": ["7d"]})
                self.assertEqual(raised.exception.status, 500)
                self.assertEqual(raised.exception.code, "unreadable_ledger")

    def test_existing_non_v3_ledger_fails_loud_without_migration(self):
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute("CREATE TABLE unrelated(value TEXT)")
        before = self.db_path.read_bytes()

        with self.assertRaises(llm_attempts.ProjectionError) as raised:
            self.calls()

        self.assertEqual(raised.exception.status, 500)
        self.assertEqual(raised.exception.code, "unsupported_ledger_schema")
        self.assertEqual(self.db_path.read_bytes(), before)

    def test_public_projection_uses_frozen_field_allowlists(self):
        valid_completed_ledger(self.db_path)

        payload = self.calls({"range": ["7d"]})
        request = payload["requests"]["items"][0]
        attempt = payload["attempts"]["items"][0]

        self.assertEqual(
            set(request),
            {
                "request_timestamp",
                "canonical_project_id",
                "logical_request_id",
                "session_ref",
                "logical_model",
                "requested_route_id",
                "route_selection_source",
                "request_outcome",
                "caller_username",
                # gateway audit projection v4 (additive; frozen copy of PUBLIC_REQUEST_FIELDS)
                "requested_mode",
                "resolved_route_id",
                "request_reject_reason",
                "admission_revision",
                "preselection_candidates",
                "matching_attempt_count",
                "total_attempt_count",
            },
        )
        self.assertEqual(
            set(attempt),
            {
                "attempt_no",
                "parent_attempt_id",
                "attempt_timestamp",
                "route_selection_source",
                "route_id",
                "actual_model",
                "provider_id",
                "credential_profile_id",
                "credential_source_kind",
                "credential_source_ref",
                "funding_source",
                "outcome",
                "latency_ms",
                "usage_state",
                "usage",
                "cost_state",
                "cost_value",
                "cost_basis",
                "cost_currency",
                "pricing_state",
                "pricing_basis",
                # gateway audit projection v4 (additive; frozen copy of PUBLIC_ATTEMPT_FIELDS)
                "attempt_id",
                "error_class",
                "http_status",
                "dispatch_boundary",
                "transport",
                "routing_revision",
                "run_id",
                "cost_provenance",
                "pricing_value",
                "pricing_currency",
                "pricing_source",
                "pricing_checked_at",
                "pricing_effective_at",
                "pricing_valid_until",
                "generation_controls",
                "route_verified_capabilities",
                "credential_profile_display_name",
                "credential_profile_display_name_source",
                "parent_request",
            },
        )
        self.assertEqual(
            set(attempt["parent_request"]),
            {"canonical_project_id", "logical_request_id"},
        )
        self.assertNotIn("credential_source", attempt)

    def test_each_semantic_invariant_family_fails_closed_at_public_python_api(self):
        for family in CORRUPT_LEDGER_FAMILIES:
            with self.subTest(family=family):
                if self.db_path.exists():
                    self.db_path.unlink()
                valid_completed_ledger(self.db_path)
                corrupt_ledger(self.db_path, family)

                with self.assertRaises(llm_attempts.ProjectionError) as raised:
                    self.calls({"range": ["7d"]})

                self.assertEqual(raised.exception.status, 500)
                self.assertEqual(raised.exception.code, "invalid_ledger_data")
                with self.assertRaises(llm_attempts.ProjectionError) as filter_error:
                    self.filters({"range": ["7d"]})
                self.assertEqual(filter_error.exception.status, 500)
                self.assertEqual(filter_error.exception.code, "invalid_ledger_data")

    def test_success_without_usage_remains_visible_with_unknown_cost(self):
        fixture = self.fixture()
        request_fk = fixture.request("native-no-usage", "2026-08-31T01:00:00+00:00")
        fixture.attempt(
            request_fk, "native-no-usage-attempt", "2026-08-31T01:01:00+00:00",
            cost_state="unknown",
        )
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute("UPDATE attempts SET usage_state='not_reported', usage_json=NULL")
        payload = self.calls({"range": ["7d"]})
        attempt = payload["attempts"]["items"][0]
        self.assertEqual(attempt["outcome"], "success")
        self.assertEqual(attempt["usage_state"], "not_reported")
        self.assertIsNone(attempt["usage"])
        self.assertEqual(attempt["cost_state"], "unknown")
        self.assertIsNone(attempt["cost_value"])
        self.assertEqual(payload["attempt_summary"]["unknown_cost_attempts"], 1)
        for column, value in (("dispatch_boundary", "not_crossed"), ("error_class", "TransportError")):
            with self.subTest(invalid=column):
                with closing(sqlite3.connect(self.db_path)) as connection, connection:
                    connection.execute("UPDATE attempts SET " + column + "=?", (value,))
                with self.assertRaises(llm_attempts.ProjectionError):
                    self.calls({"range": ["7d"]})
                with closing(sqlite3.connect(self.db_path)) as connection, connection:
                    connection.execute("UPDATE attempts SET dispatch_boundary='crossed', error_class=NULL")

    def test_closed_vocabulary_positive_control_preserves_valid_cost_groups(self):
        fixture = self.fixture()
        valid_attributions = (
            ("company", "company_paid"),
            ("personal", "personal_paid"),
            ("company", "subscription"),
        )
        all_capabilities = ("text", "image", "structured", "reasoning", "streaming")
        for index, (billing_scope, funding_type) in enumerate(valid_attributions):
            request_fk = fixture.request(
                "valid-vocabulary-%d" % index,
                "2026-08-31T%02d:00:00+00:00" % (index + 1),
                requested_capabilities=all_capabilities,
            )
            fixture.attempt(
                request_fk,
                "valid-vocabulary-attempt-%d" % index,
                "2026-08-31T%02d:01:00+00:00" % (index + 1),
                billing_scope=billing_scope,
                funding_type=funding_type,
                route_verified_capabilities=all_capabilities,
            )

        payload = self.calls({"range": ["7d"]})
        self.assertEqual(payload["attempts"]["matching_count"], 3)
        self.assertEqual(
            {row["funding_source"] for row in payload["attempts"]["items"]},
            {"company_paid", "personal_paid", "company_subscription"},
        )
        self.assertEqual(
            {
                row["funding_type"]
                for row in payload["cost_summary"]["monetary_subtotals"]
            },
            {"company_paid", "personal_paid", "subscription"},
        )

    def test_validator_noop_mutant_reopens_a_dispatch_cost_negative_control(self):
        valid_completed_ledger(self.db_path)
        corrupt_ledger(self.db_path, "dispatch_cost")

        with self.assertRaises(llm_attempts.ProjectionError):
            self.calls({"range": ["7d"]})

        with mock.patch.object(llm_attempts, "_validate_dispatch_cost", lambda row: None):
            payload = self.calls({"range": ["7d"]})

        self.assertEqual(payload["attempts"]["matching_count"], 1)

    def test_dual_time_windows_apply_to_attempt_filtered_request_slice(self):
        fixture = self.fixture()
        request_in_attempt_out = fixture.request(
            "request-in-attempt-out", "2026-08-31T01:00:00+00:00"
        )
        fixture.attempt(
            request_in_attempt_out,
            "attempt-out",
            "2026-08-20T01:00:00+00:00",
            provider="tokeneum",
        )
        request_out_attempt_in = fixture.request(
            "request-out-attempt-in", "2026-08-20T01:00:00+00:00"
        )
        fixture.attempt(
            request_out_attempt_in,
            "attempt-in-parent-out",
            "2026-08-31T02:00:00+00:00",
            provider="tokeneum",
        )
        matching = fixture.request("matching", "2026-08-31T03:00:00+00:00")
        fixture.attempt(
            matching,
            "attempt-matching",
            "2026-08-31T04:00:00+00:00",
            provider="tokeneum",
        )
        nonmatching = fixture.request("nonmatching", "2026-08-31T05:00:00+00:00")
        fixture.attempt(
            nonmatching,
            "attempt-nonmatching",
            "2026-08-31T06:00:00+00:00",
            provider="openrouter",
            profile="openrouter-api",
            route="openrouter-claude",
        )

        filtered = self.calls({"range": ["7d"], "provider": ["tokeneum"]})

        self.assertEqual(
            filtered["request_selection"]["attempt_filter_relation"],
            "matching_child_in_attempt_time_range",
        )
        self.assertEqual(
            [row["logical_request_id"] for row in filtered["requests"]["items"]],
            ["matching"],
        )
        self.assertEqual(filtered["requests"]["items"][0]["matching_attempt_count"], 1)
        self.assertEqual(
            {row["parent_request"]["logical_request_id"] for row in filtered["attempts"]["items"]},
            {"matching", "request-out-attempt-in"},
        )
        linked = next(
            row
            for row in filtered["attempts"]["items"]
            if row["parent_request"]["logical_request_id"] == "request-out-attempt-in"
        )
        self.assertEqual(linked["parent_request"]["logical_request_id"], "request-out-attempt-in")

        unfiltered = self.calls({"range": ["7d"]})
        self.assertEqual(
            unfiltered["request_selection"]["attempt_filter_relation"], "not_applied"
        )
        self.assertEqual(
            {row["logical_request_id"] for row in unfiltered["requests"]["items"]},
            {"request-in-attempt-out", "matching", "nonmatching"},
        )

    def test_request_outcomes_are_mutually_exclusive_and_in_progress_is_not_terminal(self):
        fixture = self.fixture()
        attempt_specs = {
            "success": {"outcome": "success"},
            "pre": {
                "outcome": "validation_error",
                "dispatch_boundary": "not_crossed",
                "cost_state": "not_incurred",
            },
            "failed": {"outcome": "validation_error"},
            "cancelled": {"outcome": "cancelled"},
            "unknown": {"outcome": "unknown", "cost_state": "unknown"},
            "interrupted": {
                "outcome": "interrupted",
                "cost_state": "unknown",
            },
            "flight": {"outcome": "in_flight", "cost_state": "unknown"},
        }
        outcomes = {
            "success": "success",
            "reject": "local_rejected",
            "pre": "failed_pre_dispatch",
            "failed": "failed",
            "cancelled": "cancelled",
            "unknown": "unknown",
            "interrupted": "interrupted_unknown",
            "admitted": "admitted",
            "flight": "in_flight",
        }
        for index, (request_id, outcome) in enumerate(outcomes.items()):
            attempt_id = f"{request_id}-attempt"
            request_fk = fixture.request(
                request_id,
                f"2026-08-31T{index:02d}:00:00+00:00",
                outcome=outcome,
                reject_reason="no_route" if outcome == "local_rejected" else None,
                active_attempt_id=attempt_id if outcome == "in_flight" else None,
            )
            if request_id in attempt_specs:
                fixture.attempt(
                    request_fk,
                    attempt_id,
                    f"2026-08-31T{index:02d}:01:00+00:00",
                    **attempt_specs[request_id],
                )

        payload = self.calls({"range": ["7d"]})
        summary = payload["request_summary"]

        self.assertEqual(payload["requests"]["matching_count"], 9)
        self.assertNotIn("distinct_requests", summary)
        self.assertEqual(summary["terminal_requests"], 7)
        self.assertEqual(summary["in_progress_requests"], 2)
        self.assertEqual(summary["successful_requests"], 1)
        self.assertEqual(summary["rejected_requests"], 1)
        self.assertEqual(summary["failed_requests"], 2)
        self.assertEqual(summary["cancelled_requests"], 1)
        self.assertEqual(summary["interrupted_or_unknown_requests"], 2)

    def test_cost_groups_never_mix_currency_or_exactness_and_keep_unknown_visible(self):
        fixture = self.fixture()
        exact = fixture.request("exact", "2026-08-31T01:00:00+00:00")
        fixture.attempt(exact, "exact-usd", "2026-08-31T01:01:00+00:00")
        estimated = fixture.request("estimated", "2026-08-31T02:00:00+00:00")
        fixture.attempt(
            estimated,
            "estimated-usd",
            "2026-08-31T02:01:00+00:00",
            cost_state="estimated",
            cost_value=0.5,
            cost_basis="tokens",
        )
        euro = fixture.request("euro", "2026-08-31T03:00:00+00:00")
        fixture.attempt(
            euro,
            "exact-eur",
            "2026-08-31T03:01:00+00:00",
            cost_value=2.0,
            cost_basis="per_request",
            cost_currency="EUR",
        )
        unknown = fixture.request("unknown", "2026-08-31T04:00:00+00:00")
        fixture.attempt(
            unknown,
            "unknown-cost",
            "2026-08-31T04:01:00+00:00",
            cost_state="unknown",
        )
        subscription = fixture.request("subscription", "2026-08-31T05:00:00+00:00")
        fixture.attempt(
            subscription,
            "subscription-no-charge",
            "2026-08-31T05:01:00+00:00",
            cost_state="not_incurred",
            funding_type="subscription",
        )

        payload = self.calls({"range": ["7d"]})
        subtotals = {
            (row["currency"], row["funding_type"], row["cost_basis"]): row
            for row in payload["cost_summary"]["monetary_subtotals"]
        }

        self.assertEqual(len(subtotals), 3)
        self.assertEqual(
            subtotals[("USD", "company_paid", "usd_per_request")]["exact_amount"],
            1.25,
        )
        self.assertEqual(
            subtotals[("USD", "company_paid", "tokens")]["estimated_amount"],
            0.5,
        )
        self.assertEqual(
            subtotals[("EUR", "company_paid", "per_request")]["exact_amount"],
            2.0,
        )
        self.assertEqual(payload["attempt_summary"]["unknown_cost_attempts"], 1)
        self.assertEqual(payload["cost_summary"]["no_per_call_charge_attempts"], 1)

    def test_registry_label_is_nullable_decoration_and_secret_fields_do_not_leak(self):
        fixture = self.fixture()
        known = fixture.request("known", "2026-08-31T01:00:00+00:00")
        fixture.attempt(known, "known-attempt", "2026-08-31T01:01:00+00:00")
        missing = fixture.request("missing", "2026-08-31T02:00:00+00:00")
        fixture.attempt(
            missing,
            "missing-attempt",
            "2026-08-31T02:01:00+00:00",
            profile="deleted-profile",
        )

        payload = self.calls({"range": ["7d"]})
        encoded = json.dumps(payload)
        by_id = {
            row["credential_profile_id"]: row
            for row in payload["attempts"]["items"]
        }

        self.assertEqual(
            by_id["tokeneum-claude-api"]["credential_profile_display_name"],
            "TokenEum Claude API",
        )
        self.assertEqual(
            by_id["tokeneum-claude-api"]["credential_profile_display_name_source"],
            "current_registry",
        )
        self.assertIsNone(
            by_id["deleted-profile"]["credential_profile_display_name"]
        )
        self.assertEqual(
            by_id["deleted-profile"]["credential_profile_display_name_source"],
            "current_registry_missing_profile",
        )
        self.assertNotIn("sk-test-canary-must-not-leak", encoded)
        self.assertNotIn("secret", encoded)

    def test_registry_reader_failure_is_distinct_from_a_missing_historical_profile(self):
        fixture = self.fixture()
        known = fixture.request("known", "2026-08-31T01:00:00+00:00")
        fixture.attempt(known, "known-attempt", "2026-08-31T01:01:00+00:00")
        missing = fixture.request("missing", "2026-08-31T02:00:00+00:00")
        fixture.attempt(
            missing,
            "missing-attempt",
            "2026-08-31T02:01:00+00:00",
            profile="deleted-profile",
        )

        valid_rows = {
            row["credential_profile_id"]: row
            for row in self.calls({"range": ["7d"]})["attempts"]["items"]
        }
        self.assertEqual(
            valid_rows["deleted-profile"][
                "credential_profile_display_name_source"
            ],
            "current_registry_missing_profile",
        )

        self.registry_path.write_text("{", encoding="utf-8")
        unreadable_rows = {
            row["credential_profile_id"]: row
            for row in self.calls({"range": ["7d"]})["attempts"]["items"]
        }
        self.assertEqual(
            {
                row["credential_profile_display_name_source"]
                for row in unreadable_rows.values()
            },
            {"registry_unreadable"},
        )

    def test_malformed_registry_profile_projection_is_unreadable(self):
        fixture = self.fixture()
        request_fk = fixture.request("known", "2026-08-31T01:00:00+00:00")
        fixture.attempt(request_fk, "known-attempt", "2026-08-31T01:01:00+00:00")
        malformed_payloads = {
            "missing_profile_collection": {},
            "wrong_schema_version": {
                "schema_version": 1,
                "credential_profiles": [],
            },
            "missing_display_name": {
                "schema_version": 2,
                "credential_profiles": [{"id": "tokeneum-claude-api"}],
            },
            "null_display_name": {
                "schema_version": 2,
                "credential_profiles": [
                    {"id": "tokeneum-claude-api", "display_name": None}
                ],
            },
            "duplicate_profile_id": {
                "schema_version": 2,
                "credential_profiles": [
                    {
                        "id": "tokeneum-claude-api",
                        "display_name": "First label",
                    },
                    {
                        "id": "tokeneum-claude-api",
                        "display_name": "Second label",
                    },
                ],
            },
        }

        for case, registry in malformed_payloads.items():
            with self.subTest(case=case):
                self.registry_path.write_text(json.dumps(registry), encoding="utf-8")
                rows = self.calls({"range": ["7d"]})["attempts"]["items"]
                profiles = self.filters({"range": ["7d"]})["attempt_dimensions"][
                    "account_profiles"
                ]
                self.assertEqual(
                    {
                        row["credential_profile_display_name_source"]
                        for row in rows
                    },
                    {"registry_unreadable"},
                )
                self.assertEqual(
                    {item["display_name_source"] for item in profiles},
                    {"registry_unreadable"},
                )
        self.assertEqual(
            {
                item["display_name_source"]
                for item in self.filters({"range": ["7d"]})[
                    "attempt_dimensions"
                ]["account_profiles"]
            },
            {"registry_unreadable"},
        )

    def test_each_page_uses_an_independent_latest_first_keyset_cursor(self):
        fixture = self.fixture()
        for index in range(5):
            request_fk = fixture.request(
                f"request-{index}", f"2026-08-31T0{index}:00:00+00:00"
            )
            fixture.attempt(
                request_fk,
                f"attempt-{index}",
                f"2026-08-31T0{index}:30:00+00:00",
                route=f"route-{index}",
            )

        first = self.calls({"range": ["7d"], "page_size": ["2"]})
        self.assertEqual(
            [row["logical_request_id"] for row in first["requests"]["items"]],
            ["request-4", "request-3"],
        )
        self.assertEqual(
            [row["route_id"] for row in first["attempts"]["items"]],
            ["route-4", "route-3"],
        )
        self.assertEqual(first["requests"]["matching_count"], 5)
        self.assertEqual(first["attempts"]["matching_count"], 5)
        self.assertIsNotNone(first["requests"]["next_cursor"])
        self.assertIsNotNone(first["attempts"]["next_cursor"])
        self.assertEqual(first["high_watermark"]["request_ingest_sequence"], 5)
        self.assertEqual(first["high_watermark"]["attempt_ingest_sequence"], 5)

        second = self.calls(
            {
                "range": ["7d"],
                "page_size": ["2"],
                "request_cursor": [first["requests"]["next_cursor"]],
                "attempt_cursor": [first["attempts"]["next_cursor"]],
            }
        )
        self.assertEqual(
            [row["logical_request_id"] for row in second["requests"]["items"]],
            ["request-2", "request-1"],
        )
        self.assertEqual(
            [row["route_id"] for row in second["attempts"]["items"]],
            ["route-2", "route-1"],
        )
        self.assertNotEqual(
            first["requests"]["next_cursor"], first["attempts"]["next_cursor"]
        )

    def test_every_filter_dimension_and_combination_share_one_selected_set(self):
        fixture = self.fixture()
        request_specs = {}
        attempt_specs = {}

        def add_request(request_id, timestamp, **kwargs):
            request_specs[request_id] = {
                "outcome": kwargs.get("outcome", "success"),
            }
            return fixture.request(request_id, timestamp, **kwargs)

        def add_attempt(request_fk, attempt_id, timestamp, **kwargs):
            route = kwargs.get("route", "tokeneum-claude-api")
            attempt_specs[route] = {
                "cost_state": kwargs.get("cost_state", "exact"),
                "cost_value": kwargs.get("cost_value", 1.25),
                "cost_basis": kwargs.get("cost_basis", "usd_per_request"),
                "cost_currency": kwargs.get("cost_currency", "USD"),
                "funding_type": kwargs.get("funding_type", "company_paid"),
            }
            return fixture.attempt(request_fk, attempt_id, timestamp, **kwargs)

        philo = add_request(
            "philo-claude", "2026-08-31T01:00:00+00:00",
            project="philo-prompt", model="claude-text", session_ref=SESSION,
        )
        add_attempt(
            philo, "tokeneum-success", "2026-08-31T01:01:00+00:00",
            provider="tokeneum", profile="tokeneum-claude-api",
            route="tokeneum-claude-api", actual_model="claude-sonnet-4-5",
            credential_source_ref="TOKEN_EUM_API_KEY_CLAUDE",
        )
        quant = add_request(
            "quant-deepseek", "2026-08-31T02:00:00+00:00",
            project="quant-lab", model="deepseek-text", outcome="failed",
            session_ref="codex:12345678-1234-4123-8123-123456789abc",
        )
        add_attempt(
            quant, "openrouter-http", "2026-08-31T02:01:00+00:00",
            provider="openrouter", profile="openrouter-api",
            route="openrouter-deepseek", actual_model="deepseek-v3.1",
            outcome="http_error", cost_state="unknown",
            credential_source_ref="OPENROUTER_API_KEY",
        )
        fallback = add_request(
            "philo-fallback", "2026-08-31T03:00:00+00:00",
            project="philo-prompt", model="claude-text", session_ref=SESSION,
        )
        add_attempt(
            fallback, "tokeneum-estimated", "2026-08-31T03:01:00+00:00",
            provider="tokeneum", profile="tokeneum-claude-api",
            route="tokeneum-fallback", actual_model="deepseek-v3.1",
            cost_state="estimated", cost_value=0.5, cost_basis="tokens",
            credential_source_ref="TOKEN_EUM_API_KEY_DEEPSEEK",
        )
        add_request(
            "philo-rejected", "2026-08-31T04:00:00+00:00",
            project="philo-prompt", model="deepseek-text",
            outcome="local_rejected", reject_reason="no_route", session_ref=None,
        )

        cases = [
            ({"project": ["quant-lab"]}, {"quant-deepseek"}, {"openrouter-deepseek"}),
            ({"session_ref": [SESSION]}, {"philo-claude", "philo-fallback"}, {"tokeneum-claude-api", "tokeneum-fallback"}),
            ({"logical_model": ["deepseek-text"]}, {"quant-deepseek", "philo-rejected"}, {"openrouter-deepseek"}),
            ({"request_outcome": ["local_rejected"]}, {"philo-rejected"}, set()),
            ({"actual_model": ["deepseek-v3.1"]}, {"quant-deepseek", "philo-fallback"}, {"openrouter-deepseek", "tokeneum-fallback"}),
            ({"provider": ["openrouter"]}, {"quant-deepseek"}, {"openrouter-deepseek"}),
            ({"account_profile": ["tokeneum-claude-api"]}, {"philo-claude", "philo-fallback"}, {"tokeneum-claude-api", "tokeneum-fallback"}),
            ({"credential_source_ref": ["TOKEN_EUM_API_KEY_DEEPSEEK"]}, {"philo-fallback"}, {"tokeneum-fallback"}),
            ({"route": ["tokeneum-fallback"]}, {"philo-fallback"}, {"tokeneum-fallback"}),
            ({"attempt_outcome": ["http_error"]}, {"quant-deepseek"}, {"openrouter-deepseek"}),
            ({"cost_basis": ["tokens"]}, {"philo-fallback"}, {"tokeneum-fallback"}),
            (
                {"project": ["philo-prompt"], "provider": ["tokeneum"], "cost_basis": ["tokens"]},
                {"philo-fallback"}, {"tokeneum-fallback"},
            ),
        ]

        for filters, expected_requests, expected_attempts in cases:
            query = {"range": ["7d"], **filters}
            with self.subTest(query=query):
                payload = self.calls(query)
                self.assertEqual(
                    {item["logical_request_id"] for item in payload["requests"]["items"]},
                    expected_requests,
                )
                self.assertEqual(
                    {item["route_id"] for item in payload["attempts"]["items"]},
                    expected_attempts,
                )
                self.assertEqual(payload["requests"]["matching_count"], len(expected_requests))
                self.assertEqual(payload["attempts"]["matching_count"], len(expected_attempts))
                self.assertNotIn("distinct_requests", payload["request_summary"])
                self.assertNotIn("attempts", payload["attempt_summary"])
                self.assertEqual(payload["high_watermark"]["request_ingest_sequence"], 4)
                self.assertEqual(payload["high_watermark"]["attempt_ingest_sequence"], 3)
                self.assertEqual(payload["as_of"], NOW.isoformat())
                expected_unknown = sum(
                    attempt_specs[item]["cost_state"] == "unknown"
                    for item in expected_attempts
                )
                self.assertEqual(
                    payload["attempt_summary"]["unknown_cost_attempts"],
                    expected_unknown,
                )
                grouped = {}
                for attempt_id in expected_attempts:
                    spec = attempt_specs[attempt_id]
                    if spec["cost_state"] not in {"exact", "estimated"}:
                        continue
                    key = (
                        spec["cost_currency"], spec["funding_type"], spec["cost_basis"]
                    )
                    row = grouped.setdefault(
                        key,
                        {
                            "currency": key[0], "funding_type": key[1],
                            "cost_basis": key[2], "exact_attempts": 0,
                            "exact_amount": 0.0, "estimated_attempts": 0,
                            "estimated_amount": 0.0,
                        },
                    )
                    row[spec["cost_state"] + "_attempts"] += 1
                    row[spec["cost_state"] + "_amount"] += spec["cost_value"]
                self.assertEqual(
                    payload["cost_summary"]["monetary_subtotals"],
                    [grouped[key] for key in sorted(grouped)],
                )

        options = self.filters({"range": ["7d"]})
        self.assertEqual(options["request_dimensions"]["projects"], ["philo-prompt", "quant-lab"])
        self.assertEqual(options["request_dimensions"]["session_refs"], [SESSION, "codex:12345678-1234-4123-8123-123456789abc"])
        self.assertEqual(options["request_dimensions"]["logical_models"], ["claude-text", "deepseek-text"])
        self.assertEqual(options["attempt_dimensions"]["actual_models"], ["claude-sonnet-4-5", "deepseek-v3.1"])
        self.assertEqual(options["attempt_dimensions"]["providers"], ["openrouter", "tokeneum"])
        self.assertEqual(options["attempt_dimensions"]["credential_source_kinds"], ["env_assignment_name"])
        self.assertEqual(options["attempt_dimensions"]["credential_source_refs"], ["OPENROUTER_API_KEY", "TOKEN_EUM_API_KEY_CLAUDE", "TOKEN_EUM_API_KEY_DEEPSEEK"])
        self.assertEqual(options["attempt_dimensions"]["routes"], ["openrouter-deepseek", "tokeneum-claude-api", "tokeneum-fallback"])
        self.assertEqual(options["attempt_dimensions"]["attempt_outcomes"], ["http_error", "success"])
        self.assertEqual(options["attempt_dimensions"]["cost_bases"], ["tokens", "usd_per_request"])

    def test_credential_source_kind_is_public_filter_and_separates_equal_refs(self):
        fixture = self.fixture()
        env_request = fixture.request("env-source", "2026-08-31T01:00:00+00:00")
        fixture.attempt(
            env_request,
            "env-attempt",
            "2026-08-31T01:01:00+00:00",
            route="env-route",
            credential_source_kind="env_assignment_name",
            credential_source_ref="shared-source",
        )
        inventory_request = fixture.request(
            "inventory-source", "2026-08-31T02:00:00+00:00"
        )
        fixture.attempt(
            inventory_request,
            "inventory-attempt",
            "2026-08-31T02:01:00+00:00",
            route="inventory-route",
            credential_source_kind="subscription_inventory_record",
            credential_source_ref="shared-source",
            funding_type="subscription",
            cost_state="not_incurred",
        )

        inventory = self.calls(
            {
                "range": ["7d"],
                "credential_source_kind": ["subscription_inventory_record"],
            }
        )
        self.assertEqual(
            [item["logical_request_id"] for item in inventory["requests"]["items"]],
            ["inventory-source"],
        )
        self.assertEqual(
            [item["credential_source_kind"] for item in inventory["attempts"]["items"]],
            ["subscription_inventory_record"],
        )
        self.assertEqual(
            [item["credential_source_ref"] for item in inventory["attempts"]["items"]],
            ["shared-source"],
        )

        env = self.calls(
            {"range": ["7d"], "credential_source_kind": ["env_assignment_name"]}
        )
        self.assertEqual(
            [item["logical_request_id"] for item in env["requests"]["items"]],
            ["env-source"],
        )
        options = self.filters({"range": ["7d"]})
        self.assertEqual(
            options["attempt_dimensions"]["credential_source_kinds"],
            ["env_assignment_name", "subscription_inventory_record"],
        )
        self.assertEqual(
            options["attempt_dimensions"]["credential_source_refs"],
            ["shared-source"],
        )

    def test_new_rows_and_terminal_updates_are_visible_without_snapshot_claims(self):
        fixture = self.fixture()
        in_flight = fixture.request(
            "in-flight",
            "2026-08-31T01:00:00+00:00",
            outcome="in_flight",
            active_attempt_id="in-flight-attempt",
        )
        fixture.attempt(
            in_flight, "in-flight-attempt", "2026-08-31T01:01:00+00:00",
            outcome="in_flight", cost_state="unknown",
        )
        retry = fixture.request(
            "retry", "2026-08-31T02:00:00+00:00", outcome="failed_pre_dispatch"
        )
        fixture.attempt(
            retry, "retry-pre-dispatch", "2026-08-31T02:01:00+00:00",
            outcome="validation_error", cost_state="not_incurred",
            dispatch_boundary="not_crossed",
        )

        first = self.calls({"range": ["7d"], "page_size": ["1"]})
        self.assertEqual(first["high_watermark"], {
            "request_ingest_sequence": 2, "attempt_ingest_sequence": 2,
        })
        self.assertEqual(first["request_summary"]["in_progress_requests"], 1)

        new_request = fixture.request(
            "new-request", "2026-08-31T03:00:00+00:00", outcome="success"
        )
        fixture.attempt(
            new_request, "new-attempt", "2026-08-31T03:01:00+00:00",
            cost_state="exact", cost_value=0.25,
        )
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute(
                """UPDATE attempts SET outcome='success', latency_ms=200,
                   usage_state='reported', usage_json=?, cost_state='exact',
                   cost_value=0.75, cost_basis='usd_per_request', cost_currency='USD',
                   cost_provenance='provider_reported_charge'
                   WHERE attempt_id='in-flight-attempt'""",
                ('{"prompt_tokens":2,"completion_tokens":1,"total_tokens":3}',),
            )
            connection.execute(
                "UPDATE logical_requests SET request_outcome='success', active_attempt_id=NULL WHERE logical_request_id='in-flight'"
            )
            connection.execute(
                "UPDATE logical_requests SET request_outcome='success' WHERE logical_request_id='retry'"
            )
        fixture.attempt(
            retry, "retry-success", "2026-08-31T04:01:00+00:00",
            attempt_no=2,
            parent_attempt_id="retry-pre-dispatch",
            cost_state="estimated",
            cost_value=0.5,
            cost_basis="tokens",
        )

        second = self.calls(
            {
                "range": ["7d"], "page_size": ["10"],
                "request_cursor": [first["requests"]["next_cursor"]],
                "attempt_cursor": [first["attempts"]["next_cursor"]],
            },
            now=NOW.replace(second=1),
        )

        self.assertEqual(second["high_watermark"], {
            "request_ingest_sequence": 3, "attempt_ingest_sequence": 4,
        })
        self.assertEqual(second["as_of"], NOW.replace(second=1).isoformat())
        self.assertEqual(second["request_summary"]["successful_requests"], 3)
        self.assertEqual(second["request_summary"]["failed_requests"], 0)
        self.assertEqual(second["request_summary"]["in_progress_requests"], 0)
        self.assertEqual(second["attempts"]["matching_count"], 4)
        self.assertEqual(second["attempt_summary"]["unknown_cost_attempts"], 0)

    def test_gateway_completion_payload_never_reaches_projection(self):
        from llm_gateway.ledger import Ledger
        from llm_gateway.server import GatewayApplication

        ledger = Ledger(self.db_path, run_id="projection-redaction")

        def dispatch(route, request):
            return {
                "content": "COMPLETION-PAYLOAD-MUST-NOT-LEAK",
                "finish_reason": "stop",
                "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
            }

        application = GatewayApplication(
            ledger=ledger,
            projects={"philo-prompt": {"claude-text": "tokeneum-claude-api"}},
            routes={
                "tokeneum-claude-api": {
                    "route_id": "tokeneum-claude-api",
                    "actual_model": "claude-sonnet-4-5",
                    "provider_id": "tokeneum",
                    "credential_profile_id": "tokeneum-claude-api",
                    "transport": "fake",
                    "capabilities": ["text"],
                    "credential_source": {
                        "provider_id": "tokeneum",
                        "credential_profile_id": "tokeneum-claude-api",
                        "auth_type": "bearer",
                        "credential_source_kind": "env_assignment_name",
                        "credential_source_ref": "TOKEN_EUM_API_KEY_CLAUDE",
                        "funding_source": "company_paid",
                    },
                }
            },
            dispatch=dispatch,
            file_revision="registry-rev-1",
            loaded_revision="registry-rev-1",
        )
        status, response = application.complete(
            {"X-LLM-Project": "philo-prompt", "X-LLM-Request-ID": "payload-redaction"},
            {"model": "claude-text", "messages": [{"role": "user", "content": "PROMPT-PAYLOAD-MUST-NOT-LEAK"}]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(response["choices"][0]["message"]["content"], "COMPLETION-PAYLOAD-MUST-NOT-LEAK")

        encoded = json.dumps(self.calls({"range": ["7d"]}))
        self.assertNotIn("COMPLETION-PAYLOAD-MUST-NOT-LEAK", encoded)
        self.assertNotIn("PROMPT-PAYLOAD-MUST-NOT-LEAK", encoded)

    def test_filter_options_are_range_only_and_do_not_inject_inventory_profiles(self):
        fixture = self.fixture()
        in_range = fixture.request(
            "in-range", "2026-08-31T01:00:00+00:00", project="philo-prompt"
        )
        fixture.attempt(in_range, "in-range-attempt", "2026-08-31T02:00:00+00:00")
        out_range = fixture.request(
            "out-range", "2026-07-01T01:00:00+00:00", project="old-project"
        )
        fixture.attempt(
            out_range,
            "out-range-attempt",
            "2026-07-01T02:00:00+00:00",
            profile="old-profile",
        )

        payload = self.filters({"range": ["7d"]})

        self.assertEqual(payload["request_dimensions"]["projects"], ["philo-prompt"])
        self.assertEqual(
            payload["attempt_dimensions"]["account_profiles"],
            [
                {
                    "id": "tokeneum-claude-api",
                    "display_name": "TokenEum Claude API",
                    "display_name_source": "current_registry",
                }
            ],
        )
        with self.assertRaises(llm_attempts.ProjectionError) as raised:
            self.filters({"range": ["7d"], "project": ["philo-prompt"]})
        self.assertEqual(raised.exception.status, 400)

    def test_invalid_query_values_fail_as_structured_client_errors(self):
        self.fixture()
        cases = [
            {"range": ["13d"]},
            {"page_size": ["0"]},
            {"page_size": ["101"]},
            {"request_cursor": ["not-a-cursor"]},
            {"unknown_filter": ["value"]},
            {"provider": [""]},
        ]
        for query in cases:
            with self.subTest(query=query), self.assertRaises(
                llm_attempts.ProjectionError
            ) as raised:
                self.calls(query)
            self.assertEqual(raised.exception.status, 400)


class HttpAndStaticContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.db_path = root / "missing.sqlite3"
        self.registry_path = root / "registry.json"
        self.registry_path.write_text(
            '{"schema_version": 2, "credential_profiles": []}', encoding="utf-8"
        )
        self.patchers = [
            mock.patch.object(llm_attempts, "DEFAULT_LEDGER_PATH", self.db_path),
            mock.patch.object(llm_attempts, "DEFAULT_REGISTRY_PATH", self.registry_path),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop)

    def _stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(2)

    def get(self, path):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.httpd.server_port, timeout=2
        )
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            body = response.read()
            return response.status, response.getheader("Content-Type"), body
        finally:
            connection.close()

    def test_server_exposes_json_calls_filters_and_static_page(self):
        status, content_type, body = self.get("/api/llm-calls?range=30d")
        self.assertEqual(status, 200)
        self.assertIn("application/json", content_type)
        self.assertEqual(json.loads(body)["ledger"]["state"], "missing")

        status, content_type, body = self.get("/api/llm-call-filters?range=30d")
        self.assertEqual(status, 200)
        self.assertIn("application/json", content_type)
        self.assertEqual(json.loads(body)["request_dimensions"], {})

        status, content_type, body = self.get("/llm-calls")
        self.assertEqual(status, 200)
        self.assertIn("text/html", content_type)
        self.assertIn("LLM 调用".encode("utf-8"), body)

    def test_projection_errors_are_json_and_do_not_echo_internal_exception_text(self):
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute("CREATE TABLE unrelated(value TEXT)")

        status, content_type, body = self.get("/api/llm-calls")
        payload = json.loads(body)

        self.assertEqual(status, 500)
        self.assertIn("application/json", content_type)
        self.assertEqual(payload["error"]["code"], "unsupported_ledger_schema")
        self.assertNotIn("sqlite", json.dumps(payload).lower())

    def test_each_semantic_invariant_family_fails_closed_at_http_endpoints(self):
        for family in CORRUPT_LEDGER_FAMILIES:
            with self.subTest(family=family):
                if self.db_path.exists():
                    self.db_path.unlink()
                valid_completed_ledger(self.db_path)
                corrupt_ledger(self.db_path, family)

                for endpoint in ("/api/llm-calls", "/api/llm-call-filters"):
                    status, content_type, body = self.get(endpoint)
                    payload = json.loads(body)
                    self.assertEqual(status, 500)
                    self.assertIn("application/json", content_type)
                    self.assertEqual(payload["error"]["code"], "invalid_ledger_data")

    def test_every_top_level_page_links_to_llm_calls(self):
        pages = ["index.html", "explore.html", "sessions.html", "network.html"]
        for name in pages:
            with self.subTest(page=name):
                html = (ROOT / "web" / name).read_text(encoding="utf-8")
                self.assertIn('href="/llm-calls"', html)
        llm_html = (ROOT / "web" / "llm-calls.html").read_text(encoding="utf-8")
        for href in ('href="/"', 'href="/explore"', 'href="/sessions"', 'href="/network"'):
            self.assertIn(href, llm_html)
        self.assertTrue((ROOT / "web" / "llm-calls.js").is_file())

    def test_llm_tables_expose_required_audit_dimensions_and_clipped_full_labels(self):
        html = (ROOT / "web" / "llm-calls.html").read_text(encoding="utf-8")
        javascript = (ROOT / "web" / "llm-calls.js").read_text(encoding="utf-8")

        upper_html = html.upper()
        for heading in (
            "会话", "凭据来源", "付费来源", "尝试",
        ):
            self.assertIn(f">{heading}<", upper_html)
        self.assertIn("auditLabelCell", javascript)
        for field in (
            "session_ref", "credential_source_ref", "funding_source",
            "route_selection_source", "attempt_no",
        ):
            self.assertIn(field, javascript)

    def test_llm_contract_names_composite_attempt_identity_and_all_shipped_filters(self):
        contract = (ROOT / "docs" / "contracts" / "ux-contract.md").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "API profile 的 current-label source 与 pricing state / basis",
            contract,
        )
        self.assertIn(
            "provider、API profile、credential source kind / ref、route、actual model、attempt outcome 与 cost basis",
            contract,
        )

    def test_error_state_clears_previously_rendered_audit_data(self):
        script = r'''
const fs = require("fs");

function makeNode(tagName) {
  let ownText = "";
  const node = {
    tagName,
    children: [],
    attributes: {},
    className: "",
    disabled: false,
    appendChild(child) { this.children.push(child); return child; },
    removeChild(child) { this.children.splice(this.children.indexOf(child), 1); },
    setAttribute(name, value) { this.attributes[name] = String(value); },
  };
  Object.defineProperty(node, "firstChild", { get() { return node.children[0] || null; } });
  Object.defineProperty(node, "textContent", {
    get() { return ownText + node.children.map((child) => child.textContent || "").join(""); },
    set(value) { ownText = String(value); node.children = []; },
  });
  return node;
}

const selectors = [
  "#llm-state", "#llm-state-title", "#llm-state-detail",
  "#llm-cost-body", "#llm-requests-body", "#llm-attempts-body",
  "#llm-request-count", "#llm-success-count", "#llm-attempt-count",
  "#llm-unknown-cost-count", "#llm-outcome-context", "#llm-request-context",
  "#llm-attempt-context", "#llm-cost-context", "#llm-no-charge",
  "#llm-requests-meta", "#llm-attempts-meta", "#llm-request-selection",
  "#llm-requests-prev", "#llm-requests-next", "#llm-attempts-prev",
  "#llm-attempts-next", "#llm-requests-page", "#llm-attempts-page",
];
const nodes = Object.fromEntries(selectors.map((selector) => [selector, makeNode("div")]));
["#llm-cost-body", "#llm-requests-body", "#llm-attempts-body"].forEach((selector) => {
  nodes[selector].appendChild(makeNode("tr")).textContent = "STALE ROW";
});
[
  "#llm-request-count", "#llm-success-count", "#llm-attempt-count",
  "#llm-unknown-cost-count", "#llm-outcome-context", "#llm-request-context",
  "#llm-attempt-context", "#llm-cost-context", "#llm-no-charge",
  "#llm-requests-meta", "#llm-attempts-meta", "#llm-request-selection",
].forEach((selector) => { nodes[selector].textContent = "STALE VALUE"; });
global.window = { location: { origin: "http://example.test" } };
global.document = { createElement: makeNode };
global.AgentMonitor = { qs(selector) { return nodes[selector]; } };

const source = fs.readFileSync("web/llm-calls.js", "utf8").replace(
  "window.AgentMonitorLLMCalls = { init };",
  "window.AgentMonitorLLMCalls = { init, state, setError };",
);
eval(source);
window.AgentMonitorLLMCalls.state.payload = { stale: true };
window.AgentMonitorLLMCalls.state.requestHistory = ["request-cursor"];
window.AgentMonitorLLMCalls.state.attemptHistory = ["attempt-cursor"];
window.AgentMonitorLLMCalls.state.requestPage = 2;
window.AgentMonitorLLMCalls.state.attemptPage = 3;
window.AgentMonitorLLMCalls.setError(new Error("ledger failed"));

process.stdout.write(JSON.stringify({
  payload: window.AgentMonitorLLMCalls.state.payload,
  rowText: ["#llm-cost-body", "#llm-requests-body", "#llm-attempts-body"].map((selector) => nodes[selector].textContent),
  summaryText: ["#llm-request-count", "#llm-success-count", "#llm-attempt-count", "#llm-unknown-cost-count"].map((selector) => nodes[selector].textContent),
  metaText: ["#llm-requests-meta", "#llm-attempts-meta", "#llm-request-selection"].map((selector) => nodes[selector].textContent),
  disabled: ["#llm-requests-prev", "#llm-requests-next", "#llm-attempts-prev", "#llm-attempts-next"].map((selector) => nodes[selector].disabled),
  pages: [window.AgentMonitorLLMCalls.state.requestPage, window.AgentMonitorLLMCalls.state.attemptPage],
}));
'''
        result = subprocess.run(
            ["node", "-e", script], cwd=ROOT, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)

        self.assertIsNone(payload["payload"])
        self.assertEqual(payload["rowText"], ["", "", ""])
        self.assertEqual(payload["summaryText"], ["—", "—", "—", "—"])
        self.assertEqual(payload["metaText"], ["—", "—", "—"])
        self.assertEqual(payload["disabled"], [True, True, True, True])
        self.assertEqual(payload["pages"], [1, 1])

    def test_summary_surfaces_interrupted_or_unknown_requests(self):
        script = r'''
const fs = require("fs");

const nodes = {};
[
  "#llm-request-count", "#llm-success-count", "#llm-attempt-count",
  "#llm-unknown-cost-count", "#llm-outcome-context", "#llm-request-context",
  "#llm-attempt-context", "#llm-cost-context",
].forEach((selector) => { nodes[selector] = { textContent: "" }; });
global.window = { location: { origin: "http://example.test" } };
global.document = {};
global.AgentMonitor = {
  qs(selector) { return nodes[selector]; },
  integer(value) { return String(value); },
};

const source = fs.readFileSync("web/llm-calls.js", "utf8").replace(
  "window.AgentMonitorLLMCalls = { init };",
  "window.AgentMonitorLLMCalls = { init, renderSummary };",
);
eval(source);
window.AgentMonitorLLMCalls.renderSummary({
  range: { value: "7d" },
  requests: { matching_count: 9 },
  attempts: { matching_count: 10 },
  request_summary: {
    successful_requests: 1,
    rejected_requests: 1,
    failed_requests: 2,
    in_progress_requests: 2,
    terminal_requests: 7,
    interrupted_or_unknown_requests: 2,
  },
  attempt_summary: { unknown_cost_attempts: 1 },
});
process.stdout.write(nodes["#llm-outcome-context"].textContent);
'''
        result = subprocess.run(
            ["node", "-e", script], cwd=ROOT, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "拒绝 1 · 失败 2 · 中断或未知 2 · 进行中 2",
        )

    def test_attempt_rows_render_composite_parent_profile_and_pricing_identity(self):
        script = r'''
const fs = require("fs");

function makeNode(tagName) {
  let ownText = "";
  const node = {
    tagName,
    children: [],
    attributes: {},
    listeners: {},
    className: "",
    title: "",
    disabled: false,
    value: "",
    appendChild(child) { this.children.push(child); return child; },
    removeChild(child) { this.children.splice(this.children.indexOf(child), 1); },
    setAttribute(name, value) { this.attributes[name] = String(value); },
    addEventListener(name, callback) { this.listeners[name] = callback; },
  };
  Object.defineProperty(node, "firstChild", { get() { return node.children[0] || null; } });
  Object.defineProperty(node, "textContent", {
    get() { return ownText + node.children.map((child) => child.textContent || "").join(""); },
    set(value) { ownText = String(value); node.children = []; },
  });
  return node;
}

const nodes = {
  "#llm-attempts-body": makeNode("tbody"),
  "#llm-attempts-meta": makeNode("span"),
  "#llm-attempts-prev": makeNode("button"),
  "#llm-attempts-next": makeNode("button"),
  "#llm-attempts-page": makeNode("span"),
};
global.window = { location: { origin: "http://example.test" } };
global.document = { createElement: makeNode };
global.AgentMonitor = {
  qs(selector) { return nodes[selector]; },
  integer(value) { return String(value); },
};

const source = fs.readFileSync("web/llm-calls.js", "utf8").replace(
  "window.AgentMonitorLLMCalls = { init };",
  "window.AgentMonitorLLMCalls = { init, renderAttempts };",
);
eval(source);

function item(project, profileId, displayName, displaySource, pricingState, pricingBasis, sourceKind, latencyMs) {
  return {
    attempt_timestamp: "2026-08-31T01:01:00+00:00",
    parent_request: { canonical_project_id: project, logical_request_id: "shared-id" },
    attempt_no: 1,
    route_selection_source: "policy",
    parent_attempt_id: null,
    provider_id: "tokeneum",
    credential_profile_id: profileId,
    credential_profile_display_name: displayName,
    credential_profile_display_name_source: displaySource,
    credential_source_kind: sourceKind,
    credential_source_ref: "TOKEN_EUM_API_KEY_CLAUDE",
    funding_source: "company_paid",
    route_id: "tokeneum-claude-api",
    actual_model: "claude-sonnet-4-5",
    outcome: "success",
    latency_ms: latencyMs,
    usage_state: "reported",
    usage: { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 },
    cost_state: "exact",
    cost_value: 1.25,
    cost_currency: "USD",
    cost_basis: "usd_per_request",
    pricing_state: pricingState,
    pricing_basis: pricingBasis,
  };
}

window.AgentMonitorLLMCalls.renderAttempts({
  attempts: {
    items: [
      item("project-a", "profile-a", "Company Claude", "current_registry", "fresh", "tokens", "env_assignment_name", 125),
      item("project-b", "profile-b", null, "current_registry_missing_profile", "not_consulted", null, "subscription_inventory_record", null),
      item("project-c", "profile-c", null, "registry_unreadable", "fresh", "tokens", "env_assignment_name", 50),
    ],
    matching_count: 3,
    next_cursor: null,
  },
});
process.stdout.write(JSON.stringify(nodes["#llm-attempts-body"].children.map((row) => row.textContent)));
'''
        result = subprocess.run(
            ["node", "-e", script], cwd=ROOT, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = json.loads(result.stdout)

        self.assertIn("project-a", rows[0])
        self.assertIn("shared-id", rows[0])
        self.assertIn("Company Claude", rows[0])
        self.assertIn("profile-a", rows[0])
        self.assertIn("当前 registry 标签", rows[0])
        self.assertIn("env assignment name", rows[0])
        self.assertIn("fresh", rows[0])
        self.assertIn("tokens", rows[0])
        self.assertIn("project-b", rows[1])
        self.assertIn("profile-b", rows[1])
        self.assertIn("当前标签不可用", rows[1])
        self.assertIn("subscription inventory record", rows[1])
        self.assertIn("未报告", rows[1])
        self.assertIn("project-c", rows[2])
        self.assertIn("profile-c", rows[2])
        self.assertIn("当前 registry 不可读", rows[2])

    def test_filter_registry_drives_controls_url_restore_change_clear_and_visible_profile(self):
        expected = [
            {"query": "machine", "selector": "#llm-machine", "scope": "request", "source": "machines"},
            {"query": "project", "selector": "#llm-project", "scope": "request", "source": "projects"},
            {"query": "session_ref", "selector": "#llm-session", "scope": "request", "source": "session_refs"},
            {"query": "logical_model", "selector": "#llm-logical-model", "scope": "request", "source": "logical_models"},
            {"query": "request_outcome", "selector": "#llm-request-outcome", "scope": "request", "source": "request_outcomes"},
            {"query": "provider", "selector": "#llm-provider", "scope": "attempt", "source": "providers"},
            {"query": "account_profile", "selector": "#llm-profile", "scope": "attempt", "source": "account_profiles", "kind": "profile"},
            {"query": "route", "selector": "#llm-route", "scope": "attempt", "source": "routes"},
            {"query": "actual_model", "selector": "#llm-actual-model", "scope": "attempt", "source": "actual_models"},
            {"query": "attempt_outcome", "selector": "#llm-attempt-outcome", "scope": "attempt", "source": "attempt_outcomes"},
            {"query": "cost_basis", "selector": "#llm-cost-basis", "scope": "attempt", "source": "cost_bases"},
            {"query": "credential_source_kind", "selector": "#llm-credential-source-kind", "scope": "attempt", "source": "credential_source_kinds"},
            {"query": "credential_source_ref", "selector": "#llm-credential-source", "scope": "attempt", "source": "credential_source_refs"},
        ]
        html = (ROOT / "web" / "llm-calls.html").read_text(encoding="utf-8")
        self.assertEqual(
            set(item["query"] for item in expected),
            set(__import__("re").findall(r'data-filter="([^"]+)"', html)),
        )

        script = r'''
const fs = require("fs");

const expected = JSON.parse(process.env.EXPECTED_FILTERS);
const initial = Object.fromEntries(expected.map((item) => [item.query, `${item.query}-value`]));
initial.account_profile = "profile-a";

function makeNode(tagName) {
  let ownText = "";
  const node = {
    tagName,
    children: [],
    attributes: {},
    listeners: {},
    className: "",
    value: "",
    disabled: false,
    appendChild(child) { this.children.push(child); return child; },
    removeChild(child) { this.children.splice(this.children.indexOf(child), 1); },
    setAttribute(name, value) { this.attributes[name] = String(value); },
    addEventListener(name, callback) { this.listeners[name] = callback; },
    cloneNode() { const copy = makeNode(tagName); copy.value = this.value; copy.textContent = this.textContent; return copy; },
  };
  Object.defineProperty(node, "firstChild", { get() { return node.children[0] || null; } });
  Object.defineProperty(node, "options", { get() { return node.children; } });
  Object.defineProperty(node, "textContent", {
    get() { return ownText + node.children.map((child) => child.textContent || "").join(""); },
    set(value) { ownText = String(value); node.children = []; },
  });
  return node;
}

const nodes = {};
const byFilter = {};
expected.forEach((item) => {
  const control = makeNode("select");
  const first = makeNode("option");
  first.value = "";
  first.textContent = "All";
  control.appendChild(first);
  nodes[item.selector] = control;
  byFilter[item.query] = control;
});
[
  "#llm-clear-filters", "#llm-state", "#llm-state-title", "#llm-state-detail", "#llm-scope", "#llm-scope-detail",
  "#llm-cost-body", "#llm-requests-body", "#llm-attempts-body",
  "#llm-request-count", "#llm-success-count", "#llm-attempt-count",
  "#llm-unknown-cost-count", "#llm-outcome-context", "#llm-request-context",
  "#llm-attempt-context", "#llm-cost-context", "#llm-no-charge",
  "#llm-requests-meta", "#llm-attempts-meta", "#llm-request-selection",
  "#llm-requests-prev", "#llm-requests-next", "#llm-attempts-prev",
  "#llm-attempts-next", "#llm-requests-page", "#llm-attempts-page",
].forEach((selector) => { nodes[selector] = makeNode("div"); });

const writes = [];
global.window = {
  location: { origin: "http://example.test", pathname: "/llm-calls", search: "" },
  history: { replaceState() {} },
};
global.document = {
  createElement: makeNode,
  querySelector(selector) {
    const matched = selector.match(/^\[data-filter="([^"]+)"\]$/);
    return matched ? byFilter[matched[1]] : nodes[selector];
  },
};
global.AgentMonitor = {
  qs(selector) { return nodes[selector]; },
  getRange() { return "30d"; },
  params() { return new URLSearchParams(Object.entries(initial)); },
  setParam(name, value) { writes.push([name, value]); },
  bindShell() {},
  integer(value) { return String(value); },
};
const filterPayload = {
  request_dimensions: {
    machines: [initial.machine],
    projects: [initial.project],
    session_refs: [initial.session_ref],
    logical_models: [initial.logical_model],
    request_outcomes: [initial.request_outcome],
  },
  attempt_dimensions: {
    providers: [initial.provider],
    account_profiles: [{ id: "profile-a", display_name: "Company Claude", display_name_source: "current_registry" }],
    routes: [initial.route],
    actual_models: [initial.actual_model],
    attempt_outcomes: [initial.attempt_outcome],
    cost_bases: [initial.cost_basis],
    credential_source_kinds: [initial.credential_source_kind],
    credential_source_refs: [initial.credential_source_ref],
  },
};
global.fetch = async () => ({ ok: true, json: async () => filterPayload });

const source = fs.readFileSync("web/llm-calls.js", "utf8").replace(
  "window.AgentMonitorLLMCalls = { init };",
  "window.AgentMonitorLLMCalls = { init, query, loadFilters, bindFilters, filterRegistry };",
);
eval(source);

(async () => {
  await window.AgentMonitorLLMCalls.loadFilters();
  const restored = Object.fromEntries(expected.map((item) => [item.query, nodes[item.selector].value]));
  const queryBefore = window.AgentMonitorLLMCalls.query();
  const profileText = nodes["#llm-profile"].options[1].textContent;
  const validLoadWrites = writes.slice();
  initial.project = "stale-project";
  initial.account_profile = "stale-profile";
  await window.AgentMonitorLLMCalls.loadFilters();
  const invalidRestored = {
    project: nodes["#llm-project"].value,
    account_profile: nodes["#llm-profile"].value,
    session_ref: nodes["#llm-session"].value,
  };
  const queryAfterInvalid = window.AgentMonitorLLMCalls.query();
  const invalidLoadWrites = writes.slice(validLoadWrites.length);
  const writesBeforeChanges = writes.length;
  window.AgentMonitorLLMCalls.bindFilters();
  expected.forEach((item) => {
    const control = nodes[item.selector];
    control.value = `changed-${item.query}`;
    control.listeners.change();
  });
  const changeWrites = writes.slice(writesBeforeChanges);
  const writesBeforeClear = writes.length;
  nodes["#llm-clear-filters"].listeners.click();
  const clearWrites = writes.slice(writesBeforeClear);
  process.stdout.write(JSON.stringify({
    registry: window.AgentMonitorLLMCalls.filterRegistry,
    restored,
    queryBefore,
    profileText,
    validLoadWrites,
    invalidRestored,
    queryAfterInvalid,
    invalidLoadWrites,
    changeWrites,
    clearWrites,
  }));
})().catch((error) => { console.error(error); process.exitCode = 1; });
'''
        result = subprocess.run(
            ["node", "-e", script],
            cwd=ROOT,
            env={**__import__("os").environ, "EXPECTED_FILTERS": json.dumps(expected)},
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)

        self.assertEqual(payload["registry"], expected)
        valid_completed_ledger(self.db_path)
        backend_filters = llm_attempts.llm_call_filters(
            {"range": ["7d"]},
            db_path=self.db_path,
            registry_path=self.registry_path,
            now=NOW,
        )
        self.assertEqual(
            {item["source"] for item in expected if item["scope"] == "request"},
            set(backend_filters["request_dimensions"]),
        )
        self.assertEqual(
            {item["source"] for item in expected if item["scope"] == "attempt"},
            set(backend_filters["attempt_dimensions"]),
        )
        self.assertEqual(payload["restored"], {
            item["query"]: ("profile-a" if item["query"] == "account_profile" else f"{item['query']}-value")
            for item in expected
        })
        for item in expected:
            self.assertEqual(
                payload["queryBefore"][item["query"]],
                "profile-a" if item["query"] == "account_profile" else f"{item['query']}-value",
            )
        self.assertIn("Company Claude", payload["profileText"])
        self.assertIn("profile-a", payload["profileText"])
        self.assertIn("当前 registry 标签", payload["profileText"])
        self.assertEqual(payload["validLoadWrites"], [])
        self.assertEqual(
            payload["invalidRestored"],
            {
                "project": "",
                "account_profile": "",
                "session_ref": "session_ref-value",
            },
        )
        self.assertNotIn("project", payload["queryAfterInvalid"])
        self.assertNotIn("account_profile", payload["queryAfterInvalid"])
        self.assertEqual(
            payload["invalidLoadWrites"],
            [["project", ""], ["account_profile", ""]],
        )
        self.assertEqual(
            payload["changeWrites"],
            [[item["query"], f"changed-{item['query']}"] for item in expected],
        )
        self.assertEqual(
            payload["clearWrites"],
            [[item["query"], ""] for item in expected],
        )


if __name__ == "__main__":
    unittest.main()
