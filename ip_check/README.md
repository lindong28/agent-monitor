# ip_check

Local network diagnostics used by the `ip-check` command and the agent-monitor
`/network` page.

## Usage

```bash
ip-check
ip-check --json
```

`ip-check` prints the upstream colored table report. `ip-check --json` emits a
structured snapshot for scripts and for agent-monitor's `/api/network` endpoint.

## JSON Schema Summary

Top-level fields:

- `version`: local integration version.
- `timestamp`: UTC collection time.
- `system`: platform, Python, and timezone context.
- `local`: LAN IP, DNS servers, IPv6 availability, and interface hints.
- `public`: public IP and geolocation data.
- `risk`: proxy or hosting risk lookup, collected only when needed.
- `spam`: spam database lookup, collected only when needed.
- `proxy_envs`: proxy-related environment variables.
- `tz_check`: timezone consistency result.
- `conclusions`: human-readable findings.
- `verdict`: summarized status such as `low`, `medium`, `high`, or `unknown`.
- `errors`: per-section collection errors; partial failures stay in the JSON
  body instead of becoming process-level failures.

## Attribution

Based on the `ai-ipcheck` 0.2.0 package by stormzhang, matching source snapshot `efd9d1a446de2bae9e5304f53dd96439368cadbe` of stormzhang/ipcheck. Local changes add structured JSON collection, network-policy routing and dashboard integration. The upstream license is [Apache 2.0](../LICENSES/ipcheck-Apache-2.0.txt); the package's MIT metadata conflicts with its bundled license. See [third-party notices](../THIRD-PARTY-NOTICES.md) for the source binding.
