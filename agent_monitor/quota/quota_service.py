"""Account-bound quota acquisition, cache and approved recovery.

Callers choose whether to use the Hub cache or request its GUI collector. This
module never imports Hub or snapshot export; provider execution stays in
quota_refresh with its existing deadlines and egress policy.
"""
import dataclasses
import fcntl
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from agent_monitor.quota import quota_applicability
from agent_monitor.quota import quota_refresh
from agent_monitor.contracts.usage import RateLimits
from parsers import accounts, claude_status, codex
from project_paths import PROJECT_ROOT

ROOT = PROJECT_ROOT


def gui_rate_limits(*, requester, request_error, blocked_accounts=None, root=None):
    root = Path(root) if root is not None else ROOT
    from parsers import codex_profiles
    links = codex_profiles.session_links()
    try:
        result = requester(**({"blocked_accounts": blocked_accounts} if blocked_accounts else {}))
        if not links.accepts_cached_quota(result.get("codex")):
            previous = result["codex"]
            result["codex"] = {**rate_limit_block(RateLimits(None, None, None, None)),
                               **{key: previous[key] for key in ("account_id", "account_label", "credential_plan", "signed_out") if key in previous},
                               "account_plan": previous.get("credential_plan"),
                               "query_state": "unknown", "refresh_error": "跨账号恢复后，旧日志额度归属无法核实。"}
        if links.errors and ((result.get("codex") or {}).get("quota_observation") or {}).get("source") != "active_query":
            result["codex"] = dict(result.get("codex") or {}, query_state="unknown", refresh_error=links.errors[0]["error"])
        managed = managed_codex_rate_limits(links)
        if managed:
            result["codex"] = dict(result.get("codex") or {}, managed_accounts=managed)
        return result
    except request_error as exc:
        cached = read_cache(root / "state" / "quota_snapshot.json")
        if not links.accepts_cached_quota(cached.get("codex")):
            cached.pop("codex", None)
        result = {}
        for provider, account in (("claude", accounts.claude_account()), ("codex", accounts.codex_account())):
            previous = cached.get(provider)
            if previous and account and accounts.quota_identity(
                provider, previous.get("account_id"), previous.get("account_label")
            ) == accounts.quota_identity(provider, account.account_id, account.label):
                block = dict(previous)
            else:
                block = rate_limit_block(RateLimits(None, None, None, None), account)
            block["refresh_error"] = str(exc)
            # A block never says "nothing to fix here" and "here is what went
            # wrong" at once. The restored cache can carry a signed-out verdict
            # from an earlier round, and stamping this round's error beside it
            # produces exactly that contradiction — which a consumer has to
            # resolve, and one resolution silently drops the error.
            block["signed_out"] = False
            block["query_state"] = "unknown"
            result[provider] = block
        managed = managed_codex_rate_limits(links)
        if managed:
            result["codex"] = dict(result.get("codex") or {}, managed_accounts=managed)
        return result


def rate_limits(refresh=False, *, blocked_accounts=None, use_cache=False, root=None):
    root = Path(root) if root is not None else ROOT
    from parsers import codex_profiles
    links = codex_profiles.session_links()
    cache_path = root / "state" / "quota_snapshot.json"
    cached = read_cache(cache_path) if use_cache else {}
    rejected_codex_cache = not links.accepts_cached_quota(cached.get("codex"))
    if rejected_codex_cache:
        cached.pop("codex", None)

    def query_state(provider, account, block):
        from agent_monitor.accounts.upstream_controls import Controls
        if provider == "codex" and rejected_codex_cache and block is None:
            block = rate_limit_block(RateLimits(None, None, None, None), account)
            block.update(query_state="unknown", refresh_error="跨账号恢复后，旧日志额度归属无法核实。", signed_out=account is None)
        if provider == "codex" and links.errors and (not block or (block.get("quota_observation") or {}).get("source") != "active_query"):
            block = block or rate_limit_block(RateLimits(None, None, None, None), account)
            block["refresh_error"] = links.errors[0]["error"]
            block["query_state"] = "unknown"
        pause = Controls(root / "state/upstream_controls").read(provider).get("pause")
        if block is None:
            if not pause:
                return None
            block = rate_limit_block(RateLimits(None, None, None, None), account)
            block["signed_out"] = account is None
        if pause:
            block["refresh_pause"] = {key: pause[key] for key in ("id", "at", "probe_attempt") if key in pause}
            block["query_state"] = "paused"
            if not block.get("refresh_error"):
                from agent_monitor.accounts import upstream_controls
                block["refresh_error"] = "本机已暂停该服务的自动请求；" + upstream_controls.pause_summary(pause)
            from agent_monitor import diagnostics
            failure = diagnostics.safe_failure(pause.get("failure"))
            if failure.get("account_fingerprint") == quota_refresh.account_fingerprint(provider, account):
                block["refresh_failure"] = failure
        else:
            block["refresh_pause"] = None
        return block

    def read(provider):
        source = claude_status if provider == "claude" else codex
        def passive():
            return source.load_rate_limits(session_links=links) if provider == "codex" else source.load_rate_limits()
        account = accounts.claude_account() if provider == "claude" else accounts.codex_account()
        note = quota_applicability.Store(root / "state").get(provider, account.account_id) if account else None
        if quota_applicability.unavailable(note):
            value = passive() or RateLimits(None, None, None, None)
            block = rate_limit_block(value, account)
            previous = cached.get(provider)
            if previous and previous.get("account_id") == account.account_id and quota_time(previous, provider) > quota_time(block, provider):
                block = dict(previous)
            block.update(subscription_confirmation=note, refresh_error=None, signed_out=False)
            return provider, query_state(provider, account, block)
        if not refresh:
            block = rate_limit_block(passive(), account)
            if block is None and account is None:
                # The one case that has to stop returning None. A machine with no
                # credential file and no reading used to publish nothing at all
                # for the provider, and an absent block cannot say which kind of
                # nothing it is — so the server read it as "this exporter is too
                # old to confirm quota refresh" and sent the reader to update an
                # exporter that was already current. An empty block can say it.
                #
                # Only this case. A machine that is signed in but has no reading
                # yet still publishes None, exactly as before: that block would
                # carry no fact the server does not already have, and the shape
                # is one other readers rely on.
                block = rate_limit_block(RateLimits(None, None, None, None), None)
            previous = cached.get(provider)
            if previous and account and accounts.quota_identity(
                provider, previous.get("account_id"), previous.get("account_label")
            ) == accounts.quota_identity(provider, account.account_id, account.label):
                if block is None or quota_time(previous, provider) > quota_time(block, provider):
                    block = previous
                elif "refresh_error" in previous:
                    # A newer local reading does not make another active query.
                    block["refresh_error"] = previous["refresh_error"]
                    block["refresh_failure"] = previous.get("refresh_failure")
            if block is not None:
                # `refresh_error` is deliberately not defaulted alongside it.
                # Absent means "this export ran no active query"; null means "it
                # ran one and it succeeded" — the exact claim the server reads
                # that key for, and one a passive export must not make.
                #
                # `signed_out` is different: without an active query this path
                # can still read the credential file, so it reports the one form
                # of "not signed in" that is knowable from here. A lapsed
                # credential looks signed in from here, and saying otherwise
                # would be a claim this path cannot support.
                block.setdefault("signed_out", account is None)
            block = query_state(provider, account, block)
            return provider, block
        error = None
        failure = None
        signed_out = False
        state = "succeeded"
        try:
            exclusions = (blocked_accounts or {}).get(provider, [])
            if "*" in exclusions or quota_refresh.account_fingerprint(provider, account) in exclusions:
                block = rate_limit_block(passive() or RateLimits(None, None, None, None), account)
                block.update(refresh_error="本轮同服务账号已有失败或结果未知；本机未再次查询。", query_state="blocked", signed_out=False)
                return provider, query_state(provider, account, block)
            value = quota_refresh.load_rate_limits(provider, account)
        except quota_refresh.NoSubscription:
            # The confirmation may have arrived after this export's precheck.
            value = passive() or RateLimits(None, None, None, None)
        except quota_refresh.NotSignedIn:
            # A state, not a failure: this machine does not use this provider, or
            # its sign-in has lapsed. It is published as a state so the server can
            # say so, and with no `refresh_error` so nothing downstream reports a
            # fault for the reader to chase.
            #
            # The account stamp, when there is one, stays. It says which account
            # the machine was last signed into, and dropping it would make a
            # lapsed sign-in indistinguishable from a machine that never had one
            # — two facts the reader may well want told apart later, even though
            # neither is a failure today.
            signed_out = True
            value = passive() or RateLimits(None, None, None, None)
        except (quota_refresh.RefreshError, OSError) as exc:
            state = "local_unavailable" if isinstance(exc, quota_refresh.LocalUnavailable) else "failed"
            failure = getattr(exc, "failure", None)
            if isinstance(exc, quota_refresh.RefreshError):
                error = str(exc)
            else:
                from agent_monitor import diagnostics
                error = "配额采集失败：" + diagnostics.failure_summary(diagnostics.failure_details(exc))
                if getattr(exc, "diagnostic_save_error", None):
                    error += "；暂停诊断保存失败：" + exc.diagnostic_save_error
            value = passive() or RateLimits(None, None, None, None)
        block = rate_limit_block(value, account)
        block["refresh_error"] = error
        block["refresh_failure"] = failure
        block["signed_out"] = signed_out
        block["query_state"] = state
        return provider, query_state(provider, account, block)

    # There are two independent provider endpoints, each with one bounded child.
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = dict(pool.map(read, ("claude", "codex")))
    claude = result.get("claude")
    if claude and claude.get("account_id"):
        note = quota_applicability.Store(root / "state").get("claude", claude["account_id"])
        if note:
            claude["subscription_confirmation"] = note
    managed = managed_codex_rate_limits(links)
    if managed:
        result["codex"] = dict(result.get("codex") or {}, managed_accounts=managed)
    if refresh and use_cache:
        save_cache(cache_path, result, session_links=links)
    return result


def managed_codex_rate_limits(session_links=None):
    """Passive observations from account-bound homes; no login/refresh calls."""
    from parsers import codex_profiles
    links = session_links if session_links is not None else codex_profiles.session_links()
    result = []
    for profile in codex_profiles.discover():
        if profile.account is None:
            continue
        account = accounts.Account(profile.account["account_id"], profile.account["email"], None)
        values = [codex.load_rate_limits(profile.home / child, profile.home / "state_5.sqlite", session_links=links)
                  for child in ("sessions", "archived_sessions")]
        value = max((v for v in values if v is not None), key=lambda v: v.updated_at, default=None)
        block = rate_limit_block(value or RateLimits(None, None, None, None), account)
        block.update(account_profile=profile.account["name"], signed_out=False, query_state="passive")
        if links.errors:
            block.update(refresh_error=links.errors[0]["error"], query_state="unknown")
        result.append(block)
    return result


def save_cache(cache_path, result, *, session_links=None):
    from parsers import codex_profiles
    links = session_links if session_links is not None else codex_profiles.session_links()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with cache_path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        previous = read_cache(cache_path)
        if not links.accepts_cached_quota(previous.get("codex")):
            previous.pop("codex", None)
        for provider, block in result.items():
            old = previous.get(provider)
            if block is not None and (old is None or accounts.quota_identity(
                    provider, block.get("account_id"), block.get("account_label")) != accounts.quota_identity(
                    provider, old.get("account_id"), old.get("account_label"))
                                      or quota_time(block, provider) >= quota_time(old, provider)):
                previous[provider] = block
            elif block is not None:
                # Keep the newer reading, but report this query's outcome —
                # both halves of it. `{**old, ...}` alone carried the old
                # `signed_out` forward under the new error, so a machine
                # that timed out once went on being republished as "not
                # signed in" every round while its error was dropped.
                previous[provider] = {
                    **old,
                    "refresh_error": block["refresh_error"],
                    "signed_out": block.get("signed_out", False),
                    "refresh_failure": block.get("refresh_failure"),
                    "refresh_pause": block.get("refresh_pause"),
                    "query_state": block.get("query_state"),
                    "subscription_confirmation": block.get("subscription_confirmation"),
                }
        staging = cache_path.with_name(cache_path.name + "." + uuid.uuid4().hex)
        try:
            with staging.open("x") as handle:
                os.chmod(staging, 0o600)
                json.dump(previous, handle, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            staging.replace(cache_path)
        finally:
            staging.unlink(missing_ok=True)


def recover_codex_quota(expected_label, pause_id, *, expected_id=None, root=None):
    """One operator-approved probe; retain the pause until its reading is saved."""
    root = Path(root) if root is not None else ROOT
    from agent_monitor.accounts.upstream_controls import Controls
    account = accounts.codex_account()
    if (not expected_label or account is None or account.label != expected_label
            or (expected_id is not None and account.account_id != expected_id)):
        raise quota_refresh.RefreshError("当前 Codex 账号与批准目标不一致，未开始探测。")
    value = quota_refresh.load_rate_limits("codex", account, probe_pause_id=pause_id)
    current = accounts.codex_account()
    if current is None or accounts.quota_identity("codex", current.account_id, current.label) != accounts.quota_identity(
            "codex", account.account_id, account.label):
        raise quota_refresh.RefreshError("探测期间账号已变化；未保存读数，暂停保留。")
    block = rate_limit_block(value, account)
    if not quota_applicability.current_window(block):
        raise quota_refresh.RefreshError("探测未返回当前有效额度窗口；暂停保留。")
    block.update(refresh_error=None, refresh_failure=None, refresh_pause=None, query_state="succeeded", signed_out=False)
    save_cache(root / "state/quota_snapshot.json", {"codex": block})
    Controls(root / "state/upstream_controls").resume("codex", pause_id)
    return block


def recover_claude_quota(expected_id, expected_label, pause_id, *, root=None):
    """One explicitly approved probe, retaining the pause until a usable result."""
    root = Path(root) if root is not None else ROOT
    from agent_monitor.accounts.upstream_controls import Controls
    account = accounts.claude_account()
    if not expected_id or not expected_label or account is None or (account.account_id, account.label) != (expected_id, expected_label):
        raise quota_refresh.RefreshError("当前 Claude 账号与批准目标不一致，未开始探测。")
    store = quota_applicability.Store(root / "state")
    note_before = store.get("claude", expected_id)
    started = datetime.now(timezone.utc).isoformat()
    value = quota_refresh.load_rate_limits("claude", account, probe_pause_id=pause_id, recovery_account_id=expected_id)
    current = accounts.claude_account()
    if current is None or (current.account_id, current.label) != (expected_id, expected_label):
        raise quota_refresh.RefreshError("探测期间账号已变化；未保存读数，暂停保留。")
    block = rate_limit_block(value, account)
    block.update(refresh_error=None, refresh_failure=None, signed_out=False)
    if not quota_applicability.supersedes(block, {"confirmed_at": started}):
        raise quota_refresh.RefreshError("未取得本次查询的有效配额窗口，未解除订阅标记或暂停。")
    store = quota_applicability.Store(root / "state")
    # Save before clearing the marker. A crash leaves the conservative state.
    save_cache(root / "state/quota_snapshot.json", {"claude": block})
    note = store.get("claude", expected_id)
    if note != note_before or (quota_applicability.unavailable(note) and not store.restore("claude", expected_id, block, expected_note=note_before)):
        raise quota_refresh.RefreshError("订阅确认已变化，暂停保留；请重新核对。")
    block["subscription_confirmation"] = store.get("claude", expected_id)
    save_cache(root / "state/quota_snapshot.json", {"claude": block})
    if pause_id is not None:
        Controls(root / "state/upstream_controls").resume("claude", pause_id)
    return block


def read_cache(path):
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict) and set(data) <= {"claude", "codex"}:
            return {key: block for key, block in data.items() if isinstance(block, dict)}
    except (FileNotFoundError, ValueError):
        pass
    return {}


def quota_time(block, provider="claude"):
    evidence = quota_applicability.observation(block.get("quota_observation")) if provider == "claude" else None
    try:
        return (1 if evidence and evidence["observed_at"] else 0,
                datetime.fromisoformat((evidence["observed_at"] if evidence and evidence["observed_at"] else block["updated_at"]).replace("Z", "+00:00")).timestamp())
    except (KeyError, TypeError, ValueError, AttributeError):
        return (0, 0)


def rate_limit_block(value, account=None):
    """A quota reading, stamped with the account this machine is signed in as.

    The stamp is what lets the dashboard group readings by account instead of
    picking whichever machine reported last. It is an assumption, not a proof —
    see ADR-024 for the window it is wrong in and why that was accepted.

    Two plans travel here, and they are two different facts, not one fact
    twice: `reading_plan` is the plan the quota reading itself reported, and
    `credential_plan` is what this machine's credential file says right now.
    They disagree whenever the two clocks have drifted — a plan upgrade the
    credential file has not refreshed through, or a reading left behind by a
    previous sign-in — and the page says so rather than picking one and calling
    it the account's plan. Which of the two is the stale one is not knowable
    from the pair, so nothing here claims it. See ADR 20260822-586a.

    Both are published raw, and separately, because the derived value alone
    cannot be undone: a machine whose reading carries no plan falls back to the
    credential one, and the result is then byte-identical to a machine where
    two independent sources happen to agree. Those are not the same state —
    one has a second source and the other has none — and a consumer that
    receives only the derived value has no way back to the difference.

    `account_plan` is the derived display value, co-published for one reason
    only: a server older than these fields reads it and has nothing else to
    show. It is not the authority — this is one writer among several, since
    every machine runs its own exporter at its own version and nothing between
    them checks that a published `account_plan` agrees with the pair beside it.
    The server therefore re-derives it on the way in and falls back to this
    value only when the pair is absent; see `_account_entry`. Once no exporter
    predates the pair, this field has no remaining reason to exist.
    """
    if value is None:
        return None
    block = dataclasses.asdict(value)
    # Parser-side carrier, not a wire field: it exists to reach the lines below
    # under the name the wire uses.
    reading_plan = _text(block.pop("plan", None))
    credential_plan = _text(account.plan) if account else None
    block["account_id"] = account.account_id if account else None
    block["account_label"] = account.label if account else None
    block["account_plan"] = reading_plan or credential_plan
    block["reading_plan"] = reading_plan
    block["credential_plan"] = credential_plan
    return block


def _text(value):
    """A usable string, or None. The last guard before a value reaches the wire.

    Both plan sources already narrow to this, each in its own parser. Repeating
    it at the one place that writes the contract means a future parser cannot
    quietly widen it: a truthy non-string would survive `or`, be published, and
    end up both in a rendered label and in the equality that decides whether the
    two sources disagree.
    """
    return value if isinstance(value, str) and value else None
