"""Account-bound quota acquisition, cache and approved recovery.

Callers choose whether to use the Hub cache or request its GUI collector. This
module never imports Hub or snapshot export; provider execution stays in
quota_refresh with its existing deadlines and egress policy.
"""
import dataclasses
import fcntl
import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from agent_monitor.quota import quota_applicability
from agent_monitor.quota import quota_refresh
from agent_monitor.network import automatic_egress
from agent_monitor.contracts.usage import RateLimits
from parsers import accounts, claude_status, codex
from agent_monitor.paths import PROJECT_ROOT

ROOT = PROJECT_ROOT


def gui_rate_limits(*, requester, request_error, blocked_accounts=None, root=None,
                    managed_refresh=False):
    root = Path(root) if root is not None else ROOT
    from parsers import codex_profiles
    links = codex_profiles.session_links()
    try:
        result = requester(**({"blocked_accounts": blocked_accounts} if blocked_accounts else {}))
        if not links.accepts_cached_quota(result.get("codex")):
            previous = result["codex"]
            result["codex"] = {**rate_limit_block(RateLimits(None, None, None, None)),
                               **{key: previous[key] for key in ("account_id", "account_label", "credential_plan", "signed_out", "managed_accounts") if key in previous},
                               "account_plan": previous.get("credential_plan"),
                               "query_state": "unknown", "refresh_error": "共享或跨账号会话历史的旧日志额度归属无法核实。"}
        if links.errors and ((result.get("codex") or {}).get("quota_observation") or {}).get("source") != "active_query":
            result["codex"] = dict(result.get("codex") or {}, query_state="unknown", refresh_error=links.errors[0]["error"])
        # The collector owns active reads. The adapter must not repeat them,
        # including when an older collector omitted optional profiles.
        managed = managed_codex_rate_limits(links, root=root, blocked_accounts=blocked_accounts)
        if managed and "managed_accounts" not in (result.get("codex") or {}):
            result["codex"] = dict(result.get("codex") or {}, managed_accounts=managed)
        return result
    except request_error as exc:
        cached = read_cache(root / "state" / "quota_snapshot.json")
        if not links.accepts_cached_quota(cached.get("codex")):
            cached["codex"] = {"managed_accounts": (cached.get("codex") or {}).get("managed_accounts", [])}
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
        managed = managed_codex_rate_limits(links, root=root)
        for block in managed:
            block.update(query_state="unknown", refresh_error=str(exc))
        if managed:
            result["codex"] = dict(result.get("codex") or {}, managed_accounts=managed)
        return result


def rate_limits(refresh=False, *, blocked_accounts=None, use_cache=False, root=None,
                managed_refresh=False, scheduled=False, automatic=False):
    root = Path(root) if root is not None else ROOT
    from parsers import codex_profiles
    links = codex_profiles.session_links()
    cache_path = root / "state" / "quota_snapshot.json"
    # The last active observation belongs to this machine, including standalone.
    cached = read_cache(cache_path)
    deadline = time.monotonic() + 100
    automatic_batch = automatic_egress.Batch() if automatic else None
    rejected_codex_cache = not links.accepts_cached_quota(cached.get("codex"))
    if rejected_codex_cache:
        cached["codex"] = {"managed_accounts": (cached.get("codex") or {}).get("managed_accounts", [])}

    def query_state(provider, account, block):
        from agent_monitor.accounts.upstream_controls import Controls
        if provider == "codex" and rejected_codex_cache and block is None:
            block = rate_limit_block(RateLimits(None, None, None, None), account)
            block.update(query_state="unknown", refresh_error="共享或跨账号会话历史的旧日志额度归属无法核实。", signed_out=account is None)
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
            remember_pre_pause_query(block)
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
            clear_pause_projection(block)
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
            if previous and accounts.quota_identity(provider, previous.get("account_id"), previous.get("account_label")) == accounts.quota_identity(provider, account.account_id, account.label) and quota_time(previous, provider) > quota_time(block, provider):
                block = dict(previous)
            block.update(subscription_confirmation=note, refresh_error=None, signed_out=False)
            if provider == "codex" and account:
                block = _with_credential_plan(block, {"credential_plan": account.plan})
            return provider, query_state(provider, account, block)
        from agent_monitor.runtime import hub as runtime_hub
        if not refresh or (scheduled and not runtime_hub.native_quota_due(root=root)):
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
                    for field in ("query_state", "refresh_skip", "refresh_attempted_at"):
                        if field in previous:
                            block[field] = previous[field]
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
            if provider == "codex" and account and block is not None:
                block = _with_credential_plan(block, {"credential_plan": account.plan})
            block = query_state(provider, account, block)
            return provider, block
        error = None
        failure = None
        signed_out = False
        state = "succeeded"
        skipped = None
        try:
            exclusions = (blocked_accounts or {}).get(provider, [])
            if "*" in exclusions or quota_refresh.account_fingerprint(provider, account) in exclusions:
                block = rate_limit_block(passive() or RateLimits(None, None, None, None), account)
                block.update(refresh_error="本轮同服务账号已有失败或结果未知；本机未再次查询。", query_state="blocked", signed_out=False)
                return provider, query_state(provider, account, block)
            value = quota_refresh.load_rate_limits(provider, account, root=root,
                **({"automatic_batch": automatic_batch} if automatic_batch else {}))
        except automatic_egress.Skipped as exc:
            state, error, skipped = "skipped", str(exc), exc.details
            value = passive() or RateLimits(None, None, None, None)
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
        block["refresh_skip"] = skipped
        previous = cached.get(provider)
        if skipped:
            if previous and accounts.quota_identity(provider, previous.get("account_id"), previous.get("account_label")) == accounts.quota_identity(provider, account.account_id, account.label):
                block["refresh_attempted_at"] = previous.get("refresh_attempted_at")
        else:
            block["refresh_attempted_at"] = datetime.now(timezone.utc).isoformat()
        if previous and account and accounts.quota_identity(provider, previous.get("account_id"), previous.get("account_label")) == accounts.quota_identity(provider, account.account_id, account.label) and quota_time(previous, provider) > quota_time(block, provider):
            block = {**previous, **{key: block.get(key) for key in ("refresh_error", "refresh_failure", "signed_out", "query_state", "refresh_attempted_at", "refresh_skip")}}
        if provider == "codex" and account:
            block = _with_credential_plan(block, {"credential_plan": account.plan})
        return provider, query_state(provider, account, block)

    # There are two independent provider endpoints, each with one bounded child.
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = dict(pool.map(read, ("claude", "codex")))
    claude = result.get("claude")
    if claude and claude.get("account_id"):
        note = quota_applicability.Store(root / "state").get("claude", claude["account_id"])
        if note:
            claude["subscription_confirmation"] = note
    if refresh:
        save_cache(cache_path, result, session_links=links)
    from agent_monitor.quota import quota_settings
    settings = quota_settings.read(root, activity_records=True)
    managed_enabled = managed_refresh or any(row.get("effective_enabled", row["enabled"]) for row in settings["accounts"])
    managed = managed_codex_rate_limits(
        links, refresh=refresh and managed_enabled, root=root, blocked_accounts=blocked_accounts,
        deadline=deadline, scheduled=scheduled, enrollment=True, automatic_batch=automatic_batch, settings=settings,
    )
    if managed:
        result["codex"] = dict(result.get("codex") or {}, managed_accounts=managed)
    if refresh:
        save_cache(cache_path, result, session_links=links)
    return result


def managed_codex_rate_limits(session_links=None, *, refresh=False, root=None, blocked_accounts=None, deadline=None, scheduled=False, enrollment=False, automatic_batch=None, settings=None):
    """Read managed profiles, optionally using the shared account quota reader.

    Passive log observations remain the default.  Active reads are bounded,
    serial, account-bound and use the same local Agent Monitor route bridge as
    interactive account actions.
    """
    from parsers import codex_profiles
    from agent_monitor.accounts.upstream_controls import Controls
    links = session_links if session_links is not None else codex_profiles.session_links()
    root = Path(root) if root is not None else ROOT
    cached = {}
    if root is not None:
        cached_codex = read_cache(root / "state" / "quota_snapshot.json").get("codex") or {}
        for block in (cached_codex.get("managed_accounts", []) or []):
            if isinstance(block, dict) and isinstance(block.get("account_profile"), str):
                cached[block["account_profile"]] = block
    deadline = deadline if deadline is not None else time.monotonic() + 100
    result = []
    from agent_monitor.quota import quota_settings
    settings = settings if settings is not None else quota_settings.read(root, activity_records=True)
    queried = {}
    native = read_cache(root / "state/quota_snapshot.json").get("codex") or {}
    profiles = sorted(codex_profiles.discover(), key=lambda p: (
        refresh_check_time(cached.get((p.account or {}).get("name")) or {}), str(p.home)))
    for profile in profiles:
        if profile.account is None:
            continue
        account = accounts.Account(profile.account["account_id"], profile.account["email"], None)
        credential = accounts.codex_account(profile.home / "auth.json")
        if credential and accounts.quota_identity("codex", credential.account_id, credential.label) == accounts.quota_identity("codex", account.account_id, account.label):
            account = dataclasses.replace(account, plan=credential.plan)
        values = [codex.load_rate_limits(profile.home / child, profile.home / "state_5.sqlite", session_links=links)
                  for child in ("sessions", "archived_sessions")
                  if not codex_profiles.shared_source(profile.home / child)]
        value = max((v for v in values if v is not None), key=lambda v: v.updated_at, default=None)
        block = rate_limit_block(value or RateLimits(None, None, None, None), account)
        cached_block = cached.get(profile.account["name"])
        same_identity = cached_block and accounts.quota_identity(
            "codex", cached_block.get("account_id"), cached_block.get("account_label")
        ) == accounts.quota_identity("codex", account.account_id, account.label)
        block["account_profile"] = profile.account["name"]
        if same_identity:
            block = _merge_managed_accounts([cached_block], [block])[0]
        block.update(signed_out=False)
        block.setdefault("query_state", "passive")
        from agent_monitor import hub_config
        selection = next((row for row in settings['accounts']
                          if (row['profile'], row['account_id'], row['account_label']) ==
                          (profile.account['name'], account.account_id, account.label)), {})
        fixed = selection.get('enabled', False)
        enrolled = not enrollment or selection.get('effective_enabled', fixed)
        identity_key = accounts.quota_identity('codex', account.account_id, account.label)
        same_account = [row for row in [native, *cached.values(), *queried.values()]
                        if accounts.quota_identity('codex', row.get('account_id'), row.get('account_label')) == identity_key]
        latest = max(same_account, key=lambda row: refresh_check_time(row) or '', default={})
        last_attempt = refresh_check_time(latest)
        try:
            fresh = (datetime.now(timezone.utc) - datetime.fromisoformat(last_attempt)).total_seconds() < hub_config.quota_interval()
        except (ValueError, TypeError):
            fresh = False
        do_refresh = refresh and enrolled and identity_key not in queried and not (scheduled and fresh)
        # A configured native home may itself be one of the discovered profiles.
        same_home = profile.home.resolve() == accounts.CODEX_AUTH.parent.resolve()
        if same_home and refresh_check_time(native) and accounts.quota_identity("codex", native.get("account_id"), native.get("account_label")) == accounts.quota_identity("codex", account.account_id, account.label):
            block = dict(native)
            block.pop("managed_accounts", None)
            block["account_profile"] = profile.account["name"]
            do_refresh = False
        if (scheduled and fresh) or identity_key in queried:
            shared = dict(latest, account_profile=profile.account['name'])
            shared.pop('managed_accounts', None)
            block = _merge_managed_accounts([shared], [block])[0]
        block = _with_credential_plan(block, {"credential_plan": account.plan})
        pause = Controls(root / "state/upstream_controls").read("codex").get("pause")
        if not pause:
            clear_pause_projection(block)
        exclusions = (blocked_accounts or {}).get("codex", [])
        blocked = "*" in exclusions or quota_refresh.account_fingerprint("codex", account) in exclusions
        if blocked:
            block.update(query_state="blocked", refresh_error="本轮同服务账号已有失败或结果未知；本机未再次查询。",
                         refresh_failure=None, refresh_pause=None)
        elif pause:
            remember_pre_pause_query(block)
            block.update(query_state="paused", refresh_pause={key: pause[key] for key in ("id", "at", "probe_attempt") if key in pause})
            if not block.get("refresh_error"):
                from agent_monitor.accounts import upstream_controls
                block["refresh_error"] = "本机已暂停该服务的自动请求；" + upstream_controls.pause_summary(pause)
        elif do_refresh and time.monotonic() + quota_refresh.TIMEOUT + 2 > deadline:
            block.update(query_state="pending", refresh_error="本轮查询时间已用完，此账号尚未查询；后续采集将继续。")
        elif do_refresh and (not enrollment or quota_settings.dispatch_selected(profile, selection.get('_records', []), root)):
            attempted = datetime.now(timezone.utc).isoformat()
            skipped = None
            try:
                active = quota_refresh.load_rate_limits("codex", account, codex_home=profile.home, root=root, account_profile=profile.account["name"],
                    **({"automatic_batch": automatic_batch} if automatic_batch else {}))
                current = accounts.codex_account(profile.home / "auth.json")
                if current is None or accounts.quota_identity("codex", current.account_id, current.label) != accounts.quota_identity("codex", account.account_id, account.label):
                    raise quota_refresh.LocalUnavailable("查询期间账号已变化；未保存读数，请核对登录状态。")
                block = rate_limit_block(active, account)
                block.update(account_profile=profile.account["name"], signed_out=False,
                            query_state="succeeded", refresh_error=None, refresh_failure=None)
            except automatic_egress.Skipped as exc:
                skipped = exc.details
                block.update(query_state="skipped", refresh_error=str(exc), refresh_failure=None, refresh_pause=None)
            except Exception as exc:
                # Keep the newest passive or prior active reading while exposing
                # this attempt's bounded failure to the dashboard.
                if isinstance(exc, quota_refresh.RefreshError):
                    detail = str(exc)
                else:
                    detail = "管理账号配额查询失败：" + type(exc).__name__
                pause = Controls(root / "state/upstream_controls").read("codex").get("pause")
                block.update(query_state="local_unavailable" if isinstance(exc, quota_refresh.LocalUnavailable) else "failed",
                             refresh_error=detail, refresh_failure=getattr(exc, "failure", None), refresh_pause=pause)
            block["refresh_skip"] = skipped
            if not skipped:
                block["refresh_attempted_at"] = attempted
            queried[identity_key] = block
            # Preserve each finished attempt even if the outer caller times out.
            save_cache(root / "state/quota_snapshot.json", {"codex": {"managed_accounts": [block]}},
                       session_links=links)
        if links.errors and (block.get("quota_observation") or {}).get("source") != "active_query":
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
            managed = (previous.pop("codex", None) or {}).get("managed_accounts")
            if managed:
                previous["codex"] = {"managed_accounts": managed}
        for provider, block in result.items():
            old = previous.get(provider)
            if provider == "codex" and set(block or {}) == {"managed_accounts"}:
                previous[provider] = dict(old or {}, managed_accounts=_merge_managed_accounts(
                    (old or {}).get("managed_accounts"), block["managed_accounts"]))
                continue
            if block is not None and (old is None or accounts.quota_identity(
                    provider, block.get("account_id"), block.get("account_label")) != accounts.quota_identity(
                    provider, old.get("account_id"), old.get("account_label"))
                                      or quota_time(block, provider) >= quota_time(old, provider)):
                previous[provider] = block
                if provider == "codex" and old and "credential_plan" not in block and accounts.quota_identity(
                        provider, block.get("account_id"), block.get("account_label")) == accounts.quota_identity(
                        provider, old.get("account_id"), old.get("account_label")):
                    previous[provider] = _with_credential_plan(block, old)
            elif block is not None:
                # Keep the newer reading, but report this query's outcome —
                # both halves of it. `{**old, ...}` alone carried the old
                # `signed_out` forward under the new error, so a machine
                # that timed out once went on being republished as "not
                # signed in" every round while its error was dropped.
                previous[provider] = {
                    **(_with_credential_plan(old, block) if provider == "codex" else old),
                    "refresh_error": block["refresh_error"],
                    "signed_out": block.get("signed_out", False),
                    "refresh_failure": block.get("refresh_failure"),
                    "refresh_pause": block.get("refresh_pause"),
                    "query_state": block.get("query_state"),
                    "refresh_skip": block.get("refresh_skip"),
                    "refresh_attempted_at": block.get("refresh_attempted_at"),
                    "subscription_confirmation": block.get("subscription_confirmation"),
                }
            if provider == "codex" and block is not None and ("managed_accounts" in block or "managed_accounts" in (old or {})):
                managed = _merge_managed_accounts(
                    (old or {}).get("managed_accounts"), block.get("managed_accounts")
                )
                target = dict(previous.get(provider) or {})
                target["managed_accounts"] = managed
                previous[provider] = target
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


def _with_credential_plan(reading, metadata):
    """Retain the quota observation while updating independently read metadata.

    Callers admit the account identity first. Key absence supports older
    exporters; explicit null clears a previously known credential plan.
    """
    result = dict(reading)
    if "credential_plan" in metadata:
        result.setdefault("reading_plan", None)
        result["credential_plan"] = _text(metadata["credential_plan"])
        result["account_plan"] = _text(result.get("reading_plan")) or result["credential_plan"]
    return result


def refresh_check_time(block):
    """Scheduling/outcome clock; never substitute it for quota observation time."""
    return max(block.get("refresh_attempted_at") or "", (block.get("refresh_skip") or {}).get("checked_at") or "")


def _merge_managed_accounts(old_rows, new_rows, *, incoming_metadata=True):
    """Merge managed rows without losing nested reads or crossing identities."""
    if not isinstance(new_rows, list):
        return old_rows if isinstance(old_rows, list) else []
    merged = {}
    for row in old_rows if isinstance(old_rows, list) else []:
        if isinstance(row, dict) and isinstance(row.get("account_profile"), str):
            merged[row["account_profile"]] = row
    for row in new_rows:
        if not isinstance(row, dict) or not isinstance(row.get("account_profile"), str):
            continue
        profile = row["account_profile"]
        previous = merged.get(profile)
        if previous is None:
            merged[profile] = row
            continue
        same_identity = accounts.quota_identity(
            "codex", previous.get("account_id"), previous.get("account_label")
        ) == accounts.quota_identity("codex", row.get("account_id"), row.get("account_label"))
        if not same_identity:
            merged[profile] = row
            continue
        newest = _with_credential_plan(
            row if quota_time(row, "codex") >= quota_time(previous, "codex") else previous,
            row if incoming_metadata and "credential_plan" in row else previous)
        # A failed attempt has no newer quota observation, but its outcome must
        # remain visible beside the retained reading.
        attempt = max((row, previous), key=refresh_check_time)
        for field in ("refresh_attempted_at", "refresh_skip", "refresh_error", "refresh_failure", "refresh_pause", "query_state", "pre_pause_query"):
            if field in attempt:
                newest[field] = attempt[field]
        merged[profile] = newest
    return list(merged.values())


def remember_pre_pause_query(block):
    if block.get("query_state") != "paused":
        block["pre_pause_query"] = {key: block.get(key) for key in
                                    ("query_state", "refresh_error", "refresh_failure")}


def clear_pause_projection(block):
    """Restore the account outcome beneath a machine-wide pause overlay."""
    block["refresh_pause"] = None
    if block.get("query_state") != "paused":
        return
    prior = block.pop("pre_pause_query", None)
    if isinstance(prior, dict):
        for key in ("query_state", "refresh_error", "refresh_failure"):
            block[key] = prior.get(key)
        block["query_state"] = block["query_state"] or "unknown"
    elif block.get("refresh_failure"):
        block["query_state"] = "failed"
    else:
        # Legacy cache did not separate the account outcome from the pause.
        # Retain that diagnostic without asserting a successful query or a live pause.
        block["legacy_pause_outcome"] = {"refresh_error": block.get("refresh_error")}
        block["query_state"] = "unknown"
        block["refresh_error"] = None


def monitor_profile_record(profile, account_id, label, *, profile_root):
    """Admit local Monitor metadata without consulting credentials or collectors."""
    try:
        if not isinstance(profile, str) or not profile.startswith("monitor-"):
            return None
        identity = profile.removeprefix("monitor-")
        if str(uuid.UUID(identity)) != identity or not account_id or not label:
            return None
        directory = Path(profile_root) / identity
        path = directory / "profile.json"
        if directory.is_symlink() or path.is_symlink():
            return None
        record = json.loads(path.read_text())
        if (record.get("id") == identity and accounts.quota_identity("codex", record.get("account_id"), record.get("email"))
                == accounts.quota_identity("codex", account_id, label)):
            return record
        return None
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def monitor_profile_matches(profile, account_id, label, *, profile_root):
    return monitor_profile_record(profile, account_id, label, profile_root=profile_root) is not None


def record_codex_reading(cache_path, account, reading, *, account_profile=None, profile_root=None):
    """Publish an account action's observation to already known collection targets."""
    value = RateLimits(reading.get("five_hour_used_pct"), reading.get("five_hour_resets_at"),
                       reading.get("seven_day_used_pct"), reading.get("seven_day_resets_at"),
                       updated_at=reading["observed_at"], plan=reading.get("plan"),
                       quota_observation=quota_applicability.observation({"source": "active_query", "observed_at": reading["observed_at"],
                           "quota_retry": reading.get("quota_retry")}),
                       codex_quota_available=reading.get("codex_quota_available"),
                       ordinary_usage_allowed=reading.get("ordinary_usage_allowed"),
                       spend_control_reached=reading.get("spend_control_reached"),
                       rate_limit_reached_type=reading.get("rate_limit_reached_type"),
                       reset_credits=reading.get("reset_credits"))
    fresh = rate_limit_block(value, account)
    fresh.update(refresh_attempted_at=reading["observed_at"], refresh_skip=None, refresh_error=None, refresh_failure=None,
                 signed_out=False, query_state="succeeded")
    previous = read_cache(cache_path).get("codex") or {}
    identity = accounts.quota_identity("codex", account.account_id, account.label)
    def matches(row):
        return accounts.quota_identity("codex", row.get("account_id"), row.get("account_label")) == identity
    rows = [dict(fresh, account_profile=row["account_profile"]) for row in previous.get("managed_accounts", []) if matches(row)]
    if account_profile is not None:
        if profile_root is None or not monitor_profile_matches(account_profile, account.account_id, account.label, profile_root=profile_root):
            raise ValueError("Monitor 账号来源已变化，未保存配额结果。")
        if not any(row["account_profile"] == account_profile for row in rows):
            rows.append(dict(fresh, account_profile=account_profile))
    result = dict(fresh) if matches(previous) else {}
    if rows:
        result["managed_accounts"] = rows
    if result:
        # The existing rows are admitted metadata. Do not rediscover credential
        # sources from a UI operation or change account membership here.
        from parsers.codex_profiles import SessionLinks
        save_cache(cache_path, {"codex": result}, session_links=SessionLinks())


def recovery_home(profile, root=None):
    if not profile:
        return None
    if profile.startswith("monitor-"):
        import uuid
        try:
            identity = str(uuid.UUID(profile.removeprefix("monitor-")))
        except ValueError:
            raise quota_refresh.LocalUnavailable("账号来源无效。") from None
        home = (Path(root) if root is not None else ROOT) / "state/codex-accounts" / identity
        if home.is_symlink() or not (home / "profile.json").is_file():
            raise quota_refresh.LocalUnavailable("所选账号目录已变化或不可用，请更新状态。")
        return home / "codex"
    from parsers import codex_profiles
    match = next((p for p in codex_profiles.discover() if p.account and p.account["name"] == profile), None)
    if match is None:
        raise quota_refresh.LocalUnavailable("所选账号目录已变化或不可用，请更新状态。")
    return match.home


def resolve_recovery_source(failure, root=None):
    """Resolve existing credentials, never alter the pause or current login."""
    profile = failure.get("account_profile")
    fingerprint = failure.get("account_fingerprint")
    auth = accounts.CODEX_AUTH
    if profile:
        try:
            auth = recovery_home(profile, root) / "auth.json"
        except quota_refresh.RefreshError:
            return profile, None, None, "missing"
    account = accounts.codex_account(auth)
    if account and (not fingerprint or quota_refresh.account_fingerprint("codex", account) == fingerprint):
        return profile, auth, account, None
    reason = "identity_changed" if account else "missing"
    # An explicit source never falls back, nor can an unidentified legacy pause
    # establish which independent profile is allowed to repair it.
    if profile or not fingerprint:
        return profile, auth, None, reason
    from parsers import codex_profiles
    candidates = {}
    inconsistent = False
    for candidate in codex_profiles.discover():
        if not candidate.account:
            continue
        path = candidate.home / "auth.json"
        identity = accounts.codex_account(path)
        if identity and quota_refresh.account_fingerprint("codex", identity) == fingerprint:
            if accounts.quota_identity("codex", candidate.account["account_id"], candidate.account["email"]) != accounts.quota_identity("codex", identity.account_id, identity.label):
                inconsistent = True
                continue
            candidates.setdefault(path.resolve(), (candidate.account["name"], path, identity, None))
    if inconsistent:
        return None, auth, None, "source_inconsistent"
    if len(candidates) == 1:
        return next(iter(candidates.values()))
    return None, auth, None, "ambiguous" if candidates else reason


def recover_codex_quota(expected_label, pause_id, *, expected_id=None, root=None, admitted_probe=None, profile=None, renew_auth=False, pause_scope=None, source_digest=None):
    """One operator-approved probe; retain the pause until its reading is saved."""
    root = Path(root) if root is not None else ROOT
    from agent_monitor.accounts.upstream_controls import Controls
    home = recovery_home(profile, root)
    account = accounts.codex_account(home / "auth.json") if home else accounts.codex_account()
    if (not expected_label or account is None or account.label != expected_label
            or (expected_id is not None and account.account_id != expected_id)):
        raise quota_refresh.RefreshError("当前 Codex 账号与批准目标不一致，未开始探测。")
    from agent_monitor.accounts import account_holds
    selected_controls = (account_holds.controls(root / "state", account.account_id, account.label)
                         if pause_scope == "account" else Controls(root / "state/upstream_controls"))
    pause = selected_controls.read("codex").get("pause") or {}
    failure = pause.get("failure") or {}
    fingerprint = quota_refresh.account_fingerprint("codex", account)
    legacy_source = bool(failure.get("account_fingerprint") and not failure.get("account_profile") and pause_scope != "account")
    if legacy_source:
        resolved_profile, _, resolved_account, _ = resolve_recovery_source(failure, root)
        if resolved_account is None or (resolved_profile or "") != (profile or ""):
            raise quota_refresh.LocalUnavailable("暂停账号来源缺失、有歧义或已变化；未开始探测，请更新状态。")
    if (pause.get("id") != pause_id or
            (profile and not legacy_source and (failure.get("account_profile") != profile or failure.get("account_fingerprint") != fingerprint)) or
            (not profile and failure.get("account_profile")) or
            (failure.get("account_fingerprint") and failure["account_fingerprint"] != fingerprint)):
        raise quota_refresh.LocalUnavailable("暂停来源或账号已变化；未开始探测，请更新状态。")
    options = {} if admitted_probe is None else {"admitted_probe": admitted_probe}
    if pause_scope == "account":
        if not profile or not profile.startswith("monitor-"):
            raise quota_refresh.LocalUnavailable("账号恢复范围无效，未发出请求。")
        options["pause_scope"] = "account"
    recovery_operation = None
    if renew_auth:
        from agent_monitor.quota import codex_quota
        if not codex_quota.authentication_quota_failure(failure):
            raise quota_refresh.LocalUnavailable("当前暂停不支持此认证修复；未发出上游请求。")
        options["renew_auth"] = True
        if profile and profile.startswith("monitor-"):
            from agent_monitor.accounts.codex_accounts import Manager
            manager = Manager(root / "state/codex-accounts")
            record = manager.read(manager.directory(profile.removeprefix("monitor-")))
            operation = record.get("operation") or {}
            if (record.get("account_id") != account.account_id or record.get("email", "").casefold() != account.label.casefold()
                    or not operation.get("refresh_only") or operation.get("login_only")
                    or operation.get("message_status") != "not_sent" or operation.get("stage") not in {"failed", "interrupted"}):
                raise quota_refresh.LocalUnavailable("此账号没有可接续的配额操作，未开始认证修复。")
            recovery_operation = operation.get("id")
        else:
            auth = home / "auth.json" if home else accounts.CODEX_AUTH
            if not source_digest or source_digest != codex_quota.recovery_source_digest(auth):
                raise quota_refresh.LocalUnavailable("认证来源已变化，请更新状态；未发出请求。")
            try:
                codex_quota.file_auth_recovery_preflight(auth)
            except codex_quota.ReadingError as exc:
                raise quota_refresh.LocalUnavailable(str(exc), failure=exc.failure) from None
            home = auth.absolute().parent
            options["source_digest"] = source_digest
    value = quota_refresh.load_rate_limits("codex", account, probe_pause_id=pause_id, root=root,
                                           codex_home=home, account_profile=profile or None, **options)
    current = accounts.codex_account(home / "auth.json") if home else accounts.codex_account()
    if source_digest and codex_quota.recovery_source_digest(home / "auth.json") != source_digest:
        raise quota_refresh.LocalUnavailable("探测期间认证来源已变化；未保存读数，暂停保留。")
    if current is None or accounts.quota_identity("codex", current.account_id, current.label) != accounts.quota_identity(
            "codex", account.account_id, account.label):
        raise quota_refresh.RefreshError("探测期间账号已变化；未保存读数，暂停保留。")
    block = rate_limit_block(value, account)
    if not quota_applicability.current_window(block):
        raise quota_refresh.RefreshError("探测未返回当前有效额度窗口；暂停保留。")
    block.update(refresh_error=None, refresh_failure=None, refresh_pause=None, refresh_skip=None, query_state="succeeded", signed_out=False)
    block["refresh_attempted_at"] = datetime.now(timezone.utc).isoformat()
    if profile:
        block["account_profile"] = profile
    save_cache(root / "state/quota_snapshot.json", {"codex": {"managed_accounts": [block]} if profile else block})
    if profile and profile.startswith("monitor-") and (recovery_operation or (pause.get("source") or {}).get("action") == "quota"):
        from agent_monitor.accounts.codex_accounts import ActionError, Manager
        from agent_monitor.accounts.upstream_controls import Busy
        source = pause.get("source") or {}
        reading = dict(five_hour_used_pct=value.five_hour_pct, five_hour_resets_at=value.five_hour_resets_at,
                       seven_day_used_pct=value.seven_day_pct, seven_day_resets_at=value.seven_day_resets_at)
        reading["plan"] = value.plan
        reading.update(observed_at=value.updated_at,
                       **{key: getattr(value, key, None) for key in (
                           "codex_quota_available", "ordinary_usage_allowed", "spend_control_reached", "rate_limit_reached_type", "reset_credits")})
        try:
            Manager(root / "state/codex-accounts").record_quota_recovery(
                profile.removeprefix("monitor-"), recovery_operation or source.get("operation_id"), pause_id, reading,
                **({"pause_scope": "account"} if pause_scope == "account" else {}))
        except (ActionError, Busy) as exc:
            raise quota_refresh.LocalUnavailable(str(exc)) from None
    selected_controls.resume("codex", pause_id)
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
    """A quota reading, stamped with the account supplied by its collector.

    The stamp is what lets the dashboard group readings by account instead of
    picking whichever machine reported last. A default-home passive stamp is
    not proof of the reading's historical account. Managed-home collectors
    supply their profile identity instead. See
    docs/references/metrics.md#account-attribution-and-quota-plans.

    Two plans travel here, and they are two different facts, not one fact
    twice: `reading_plan` is the plan the quota reading itself reported, and
    `credential_plan` is what this machine's credential file says right now.
    They disagree whenever the two clocks have drifted — a plan upgrade the
    credential file has not refreshed through, or a reading left behind by a
    previous sign-in — and the page says so rather than picking one and calling
    it the account's plan. Which of the two is the stale one is not knowable
    from the pair, so nothing here claims it. The same maintenance guide above
    describes the two sources and compatibility behavior.

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
