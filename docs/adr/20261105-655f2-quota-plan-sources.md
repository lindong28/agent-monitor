# Quota plan names and source details

Decision: display distinct Codex plan tiers using upstream names, and explain independent quota and login metadata in accessible source details without treating raw-string disagreement as an account failure.

The user reported `promax` from a quota observation alongside `pro` from local login metadata and requested repair and acceptance. The baseline is `adde3b09504aaec1eba47999f210d700128789d7`. The existing Web comparison only checks string inequality; credential metadata comes from a local JWT, not a second live subscription query. These facts do not establish which source is older or whether an upgrade failed.

Use the provider-specific names from [Codex auth.rs at e7f32cf](https://github.com/openai/codex/blob/e7f32cf1928a35a9232a633a51c2a03b5a7e2c72/codex-rs/protocol/src/auth.rs#L135): `pro` is `Pro (More)`, `prolite` is `Pro`, and `promax` is `Pro (Max)`. Preserve the raw values and unknown identifiers. Do not infer dollar prices or merge the tiers. Other providers retain their existing labels.

The selected quota reading remains the first display source. Its plan, percentages, source and observation time move together when a newer account-action reading replaces a snapshot. Source details distinguish active queries, session observations, login metadata and legacy records. Credential update time is unknown in the current payload; quota time and source machine must not stand in for it. Differences remain available in details, without asserting stale credentials or prescribing login. Independent authentication failures, restrictions and subscription confirmations keep their existing behavior.

Alternatives rejected: merging tiers loses upstream distinctions; adding only a label leaves the misleading failure implication; automatic authentication or credential refresh adds unnecessary external effects; adding a timestamp schema is unnecessary when unavailable evidence can be stated explicitly. No API, persistence, provider request, scheduling or admission behavior changes. Old exporters and historical records remain supported.

An independent read-only decision review on 2026-11-05 approved all seven criteria in one round, with no unresolved findings. This approves the scoped decision, not implementation, deployment, billing status or live provider availability. Acceptance covers the affected Web consumers, source/clock cases and deployed browser resources. Ordinary source rollback remains available without a data migration.
