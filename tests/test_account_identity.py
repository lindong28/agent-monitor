"""Reading which account a machine is signed in as, and stamping it onto quota.

The failure this guards is disguised: if account reading breaks (a key rename, a
padding bug, a changed credential shape after a CLI upgrade), every machine
degrades to "account unknown", which is also what a legitimately old exporter
looks like. Silence is therefore not evidence, and these tests are the only
thing standing between a regression and a plausible-looking wrong page.
"""

import base64
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agent_monitor.quota import quota_service
from parsers import RateLimits, accounts


def id_token(claims):
    """A JWT whose payload segment carries `claims`. Signature is not read."""
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=")
    return b".".join([b"header", payload, b"signature"]).decode()


CODEX_CLAIMS = {
    "email": "someone@example.com",
    "https://api.openai.com/auth": {
        "chatgpt_account_id": "acct-from-token",
        "chatgpt_plan_type": "prolite",
    },
}


class CodexAccountTests(unittest.TestCase):
    def write(self, payload):
        path = Path(self.tmp) / "auth.json"
        path.write_text(json.dumps(payload))
        return path

    def setUp(self):
        self.dir = TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.tmp = self.dir.name

    def test_selected_workspace_wins_while_token_supplies_label_and_plan(self):
        path = self.write({"tokens": {"account_id": "cached", "id_token": id_token(CODEX_CLAIMS)}})

        account = accounts.codex_account(path)

        self.assertEqual(account.account_id, "cached")
        self.assertEqual(account.label, "someone@example.com")
        self.assertEqual(account.plan, "prolite")

    def test_email_uses_upstream_namespaced_profile_fallback(self):
        # Codex rust-v0.162.1 token_data.rs:174-179: email.or(profile.email).
        for top, expected in ((None, "nested@example.com"), ("top@example.com", "top@example.com")):
            with self.subTest(top=top):
                claims = {"https://api.openai.com/profile": {"email": "nested@example.com"}}
                if top is not None:
                    claims["email"] = top
                path = self.write({"tokens": {"account_id": "selected", "id_token": id_token(claims)}})
                self.assertEqual(accounts.codex_account(path).label, expected)

    def test_falls_back_to_the_cached_account_id_when_the_token_is_unreadable(self):
        path = self.write({"tokens": {"account_id": "cached", "id_token": "not-a-jwt"}})

        account = accounts.codex_account(path)

        self.assertEqual(account.account_id, "cached")
        self.assertIsNone(account.label)

    def test_api_key_mode_and_signed_out_read_as_no_account(self):
        """Not every signed-in state carries an account id. Reporting one anyway
        would put a name on a reading that has none."""
        for payload in ({"OPENAI_API_KEY": "sk-x", "tokens": {}}, {"tokens": {}}, {}):
            with self.subTest(payload=payload):
                self.assertIsNone(accounts.codex_account(self.write(payload)))

    def test_auth_mode_selects_whether_stored_tokens_describe_active_identity(self):
        # Expected values follow fixed AuthDotJson.resolved_mode/from_auth_dot_json;
        # this local JSON is not a valid credential and never reaches a provider.
        cases = (
            ({}, "selected"),
            ({"OPENAI_API_KEY": "synthetic-not-secret"}, None),
            ({"OPENAI_API_KEY": ""}, None),
            ({"OPENAI_API_KEY": None}, "selected"),
            ({"auth_mode": None, "OPENAI_API_KEY": "synthetic-not-secret"}, None),
            ({"auth_mode": "apikey", "OPENAI_API_KEY": "synthetic-not-secret"}, None),
            ({"auth_mode": "apikey"}, None),
            ({"auth_mode": "chatgpt", "OPENAI_API_KEY": "synthetic-not-secret"}, "selected"),
            ({"auth_mode": "chatgptAuthTokens", "OPENAI_API_KEY": "synthetic-not-secret"}, "selected"),
            ({"auth_mode": "unknown"}, None),
            ({"auth_mode": []}, None),
            ({"personal_access_token": "synthetic-not-secret"}, None),
            ({"bedrock_api_key": {"api_key": "synthetic-not-secret"}}, None),
            ({"bedrock_access_keys": {}}, None),
            ({"auth_mode": "agentIdentity"}, None),
            ({"auth_mode": "personalAccessToken"}, None),
            ({"auth_mode": "bedrockApiKey"}, None),
            ({"auth_mode": "bedrockAccessKeys"}, None),
            ({"auth_mode": "headers"}, None),
        )
        for fields, expected in cases:
            with self.subTest(fields=fields):
                payload = {"tokens": {"account_id": "selected", "id_token": id_token(CODEX_CLAIMS)}, **fields}
                value = accounts.codex_account(self.write(payload))
                self.assertEqual(value.account_id if value else None, expected)

    def test_a_missing_or_torn_file_is_no_account_not_an_exception(self):
        """`~/.codex/auth.json` is rewritten by the CLI, so a read can land
        mid-write. An export must not fail because of that."""
        missing = Path(self.tmp) / "absent.json"
        torn = Path(self.tmp) / "torn.json"
        torn.write_text('{"tokens": {"account_id":')

        self.assertIsNone(accounts.codex_account(missing))
        self.assertIsNone(accounts.codex_account(torn))

    def test_no_token_or_key_is_carried_out_of_the_module(self):
        """The account fields travel between machines in an export; credentials
        must not ride along with them."""
        secrets = {
            "access_token": "at-secret",
            "refresh_token": "rt-secret",
            "id_token": id_token(CODEX_CLAIMS),
            "account_id": "cached",
        }
        path = self.write({"auth_mode": "chatgpt", "OPENAI_API_KEY": "sk-secret", "tokens": secrets})

        rendered = json.dumps(accounts.codex_account(path).__dict__)

        for secret in ("at-secret", "rt-secret", "sk-secret", secrets["id_token"]):
            self.assertNotIn(secret, rendered)


class ClaudeAccountTests(unittest.TestCase):
    def setUp(self):
        self.dir = TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "claude.json"

    def write(self, payload):
        self.path.write_text(json.dumps(payload))
        return self.path

    def test_reads_uuid_email_and_tier(self):
        path = self.write(
            {
                "oauthAccount": {
                    "accountUuid": "uuid-1",
                    "emailAddress": "someone@example.com",
                    "organizationRateLimitTier": "default_claude_max_20x",
                }
            }
        )

        account = accounts.claude_account(path)

        self.assertEqual(account.account_id, "uuid-1")
        self.assertEqual(account.label, "someone@example.com")
        self.assertEqual(account.plan, "default_claude_max_20x")

    def test_signed_out_or_uuid_less_reads_as_no_account(self):
        for payload in ({}, {"oauthAccount": {}}, {"oauthAccount": {"emailAddress": "a@b.c"}}):
            with self.subTest(payload=payload):
                self.assertIsNone(accounts.claude_account(self.write(payload)))


class StampingTests(unittest.TestCase):
    """`_rate_limit_block` is what puts the account beside the numbers."""

    limits = RateLimits(1.0, 2, 3.0, 4, updated_at="2026-08-19T00:00:00Z")

    def test_a_reading_carries_the_account_it_was_stamped_with(self):
        account = accounts.Account("acct-1", "a@b.c", "plus")

        block = quota_service.rate_limit_block(self.limits, account)

        self.assertEqual(block["account_id"], "acct-1")
        self.assertEqual(block["account_label"], "a@b.c")
        self.assertEqual(block["account_plan"], "plus")
        self.assertEqual(block["seven_day_pct"], 3.0)

    def test_signed_out_still_publishes_the_key_so_it_reads_as_signed_out(self):
        """Present-and-null is what separates "signed out" from "reported by an
        exporter too old to stamp accounts", which omits the key entirely. The
        two have different remedies, so they must stay distinguishable."""
        block = quota_service.rate_limit_block(self.limits, None)

        self.assertIn("account_id", block)
        self.assertIsNone(block["account_id"])

    def test_no_reading_publishes_no_block_even_when_signed_in(self):
        self.assertIsNone(quota_service.rate_limit_block(None, accounts.Account("acct-1")))


class PlanSourceTests(unittest.TestCase):
    """Two plans travel in a block, and they are two facts, not one twice.

    `reading_plan` comes from the same stored event as the percentages;
    `credential_plan` is what the supplied account metadata says now.
    `account_plan` is derived for display. Publishing both sources lets the page state that they disagree
    instead of picking one and calling it the account's plan. See
    docs/references/metrics.md#account-attribution-and-quota-plans.
    """

    reading = RateLimits(1.0, 2, 3.0, 4, updated_at="2026-08-22T00:58:29Z", plan="pro")
    planless = RateLimits(1.0, 2, 3.0, 4, updated_at="2026-08-22T00:58:29Z")
    account = accounts.Account("acct-1", "a@b.c", "prolite")

    def test_the_shown_plan_comes_from_the_reading_not_the_credential_file(self):
        block = quota_service.rate_limit_block(self.reading, self.account)

        self.assertEqual(block["account_plan"], "pro")
        self.assertEqual(block["reading_plan"], "pro")
        self.assertEqual(block["credential_plan"], "prolite")

    def test_a_reading_without_a_plan_falls_back_to_the_credential_file(self):
        block = quota_service.rate_limit_block(self.planless, self.account)

        self.assertEqual(block["account_plan"], "prolite")
        self.assertIsNone(block["reading_plan"])
        self.assertEqual(block["credential_plan"], "prolite")

    def test_the_three_plan_states_are_all_tellable_apart_on_the_wire(self):
        """One source, two that agree, and two that differ are three states.

        The derived `account_plan` cannot express the first two apart — a
        reading with no plan takes the credential one, and the result is
        byte-identical to two independent sources that happen to match. A
        consumer handed only the derived value has no way back, so the raw
        pair travels beside it and this pins that it stays recoverable.
        """
        pro_account = accounts.Account("acct-1", "a@b.c", "pro")
        # Both of these land on account_plan == "pro" — that collision is the
        # point, not an accident of the fixture.
        both_agree = quota_service.rate_limit_block(self.reading, pro_account)
        credential_only = quota_service.rate_limit_block(self.planless, pro_account)
        reading_only = quota_service.rate_limit_block(self.reading, None)
        disagree = quota_service.rate_limit_block(self.reading, self.account)

        def state(block):
            return (block["reading_plan"], block["credential_plan"])

        seen = [state(block) for block in
                (both_agree, credential_only, reading_only, disagree)]
        self.assertEqual(len(set(seen)), len(seen), seen)

        # Name the pair the derived value collapses, so a regression that
        # merges exactly those two fails here with the reason on the label:
        # identical `account_plan`, different provenance.
        self.assertEqual(both_agree["account_plan"], credential_only["account_plan"])
        self.assertIsNotNone(both_agree["reading_plan"])
        self.assertIsNone(credential_only["reading_plan"])

    def test_the_parser_carrier_does_not_reach_the_wire(self):
        """`plan` is how the parser hands the reading's plan to this function.

        Left in, every block would publish a second, differently-named plan.
        (Not "Claude would start publishing null for a fact it cannot state" —
        it publishes `reading_plan: null` for exactly that, by design. The
        objection is the duplicate name, not the null.)
        """
        for value in (self.reading, self.planless):
            with self.subTest(reading=value):
                self.assertNotIn("plan", quota_service.rate_limit_block(value, self.account))


if __name__ == "__main__":
    unittest.main()
