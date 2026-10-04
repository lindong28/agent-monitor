"""Read-only Claude OAuth helpers extracted from ai-agent-config statusline-usage.py.

Source: 3d61487d5386dfe9bb6ba7d26807005bbf0bd7f2 (2026-10-04 extraction).
Credentials remain on their owner machine; tokens are sent only in HTTPS headers.
The caller quota_refresh bounds the complete child process, including body reads.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from datetime import datetime, timezone

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
OAUTH_BETA = "oauth-2025-04-20"
TIMEOUT_S = 10
CLAUDE_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
CREDENTIALS = os.path.join(CLAUDE_DIR, ".credentials.json")
KEYCHAIN_TOOL = "/usr/bin/security"
KEYCHAIN_SERVICE = "Claude Code-credentials"
KEYCHAIN_TIMEOUT_S = 5


def to_epoch(value: object) -> int:
    """ISO-8601 instant as epoch seconds; 0 when absent or unparseable.

    The payload dates these in ISO while the statusline's `fmt_reset` speaks
    epoch seconds, same as the `rate_limits` windows already do.
    """
    if not isinstance(value, str) or not value.strip():
        return 0
    text = value.strip()
    if text.endswith("Z"):
        # fromisoformat rejects the military-zone suffix before Python 3.11.
        text = text[:-1] + "+00:00"
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return 0
    if stamp.tzinfo is None:
        # These are instants, not local wall-clock times; reading a naive one as
        # local time would shift the countdown by the host's UTC offset.
        stamp = stamp.replace(tzinfo=timezone.utc)
    # Rounded up, not truncated. These carry sub-second precision in practice and
    # it straddles the whole second — two consecutive responses gave
    # `...T00:00:00.228772+00:00` and `...T23:59:59.590324+00:00` for the same
    # weekly reset. Under the current contract only whole seconds can be stored,
    # since the countdown is computed in bash integer arithmetic
    # (`$((resets_at - now))`), so the instant lands under a second off in one
    # direction and the only choice is which. Neither is free, and both last
    # until whatever render lands in that sub-second window is replaced by the
    # next one: truncating hides a window that is still live, rounding up keeps
    # showing one that has just reset — and that stale percentage can be far
    # from the new period's, which starts at 0. Rounding up, because a bar that
    # disappears reads as the feature being broken, which is the failure this
    # whole path exists to have stopped.
    return math.ceil(stamp.timestamp())


def token_from_blob(raw: str) -> str | None:
    """The live token a credential blob carries, or None when it has none.

    Both stores hold the same JSON, so the parsing and the freshness check are
    shared rather than duplicated per store.
    """
    try:
        oauth = json.loads(raw).get("claudeAiOauth") or {}
    except Exception:
        return None
    if not isinstance(oauth, dict):
        return None
    token = oauth.get("accessToken")
    if not isinstance(token, str) or not token:
        return None
    # A token that cannot go in a header is not a usable credential, and saying
    # so here is what keeps that judgement local to the store. Left to the
    # request, an embedded newline or non-ASCII byte raises `ValueError` before
    # anything is sent, which the caller can only read as the network having
    # failed — and it would then stop rather than try the store that does hold
    # a working credential.
    if not token.isascii() or not token.isprintable():
        return None
    expires_at = oauth.get("expiresAt")  # milliseconds
    if isinstance(expires_at, (int, float)) and not isinstance(expires_at, bool):
        # An expired token only earns 401s. Refreshing it is Claude Code's job:
        # racing its refresh from here risks spending the one-shot refresh token
        # the live sessions depend on.
        if expires_at / 1000.0 <= time.time():
            return None
    return token


def blob_from_file() -> str | None:
    """The credentials file's contents, or None when there is no reading it."""
    try:
        with open(CREDENTIALS, encoding="utf-8") as fh:
            return fh.read()
    except Exception:
        return None


def blob_from_keychain() -> str | None:
    """The login keychain's copy of the same blob, or None when unavailable.

    The secret comes back on stdout and is captured, never echoed: `-w` prints
    the password alone, and the service name is the only thing on the command
    line, which `ps` shows to every user on the host.
    """
    try:
        completed = subprocess.run(
            [KEYCHAIN_TOOL, "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            # Nothing to read, and a child inheriting this process's stdin could
            # consume input meant for whoever spawned it.
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=KEYCHAIN_TIMEOUT_S,
        )
    except Exception:
        # No such binary — the ordinary non-macOS case — or a read that blocked
        # past the timeout and was killed with it.
        return None
    if completed.returncode != 0:
        # No such item, a locked keychain, a denied ACL. All the same here.
        return None
    return completed.stdout


def read_tokens() -> Iterator[str]:
    """Every token this host offers, in the order they should be tried.

    Lazy on purpose. A store is only opened once the caller has run out of
    earlier candidates, so a host whose first credential is accepted never
    touches the second store at all — which on macOS is the one that can cost
    seconds, or raise a dialog, for an answer nobody needed.

    Every store is read on every host rather than selected by platform. The
    file is where Linux keeps the blob and the keychain is where macOS keeps
    it, but a host can carry either — a macOS checkout with a leftover
    credentials file, say — and the probe that does not apply costs a failed
    open or a failed exec.

    Deliberately not narrowed to one winner here, because locally *unexpired*
    is not the same as *accepted*: a token whose `expiresAt` is still in the
    future can already have been revoked, superseded, or issued for a different
    account, and nothing on this side of the request can tell. Returning the
    first one would let a stale file that merely looks fresh permanently shadow
    the live keychain entry beside it — the same silent, self-renewing blank
    this refresher exists to have fixed. Only the server settles it, so the
    caller tries them in turn.
    """
    seen: list[str] = []
    for store in (blob_from_file, blob_from_keychain):
        raw = store()
        if not raw:
            continue
        token = token_from_blob(raw)
        # A host can hold the same blob in both stores; asking the server twice
        # with one it already rejected buys nothing.
        if token and token not in seen:
            seen.append(token)
            yield token


class _RefuseRedirect(urllib.request.HTTPRedirectHandler):
    """Fail on any 3xx rather than following it.

    urllib's default handler carries the original request's headers onto the
    redirected request, so a cross-origin redirect would hand this account's
    bearer token to whatever host the response names — and one to `http://`
    would put it on the wire in the clear. The usage endpoint has no legitimate
    reason to redirect, so the safe response to one is a failed refresh.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


_OPENER = urllib.request.build_opener(_RefuseRedirect)


def fetch(token: str) -> object:
    request = urllib.request.Request(
        USAGE_URL,
        headers={
            "Authorization": "Bearer " + token,
            "anthropic-beta": OAUTH_BETA,
            "Accept": "application/json",
        },
    )
    with _OPENER.open(request, timeout=TIMEOUT_S) as response:
        return json.load(response)
