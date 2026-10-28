"""CLI adapter extracted from the public agent-monitor command."""


def main():
    import argparse
    import json
    import sys
    from parsers.codex import load_session_rate_limits

    parser = argparse.ArgumentParser(prog="agent-monitor session-quota",
        description="Read this session's stored quota; no provider query or account attribution.")
    parser.add_argument("session_id")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        result = load_session_rate_limits(args.session_id)
    except (ValueError, OSError) as exc:
        print("Session quota unavailable: %s" % exc, file=sys.stderr)
        raise SystemExit(2)
    if args.json:
        print(json.dumps(result, sort_keys=True))
    elif result["status"] == "observed":
        print("Session %s recorded weekly quota: %s%% used; observed %s; reset Unix time %s."
              % (result["session_id"], result["seven_day_pct"], result["updated_at"],
                 result["seven_day_resets_at"] if result["seven_day_resets_at"] is not None else "unknown"))
        print("Stored observation only; current quota and account identity are unverified.")
    else:
        reason = "no matching rollout" if result["status"] == "no_rollout" else "no usable weekly reading"
        print("Session %s: %s; quota unverified. No provider queried or global reading substituted."
              % (result["session_id"], reason))
    raise SystemExit(0 if result["status"] == "observed" else 1)


if __name__ == "__main__":
    main()
