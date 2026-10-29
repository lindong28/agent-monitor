# Contributing

Read [README.md](README.md), [AGENTS.md](AGENTS.md) and [architecture](docs/public-architecture.md). Contributions are covered by the [MIT license](LICENSE). Open issues and pull requests at [lindong28/agent-monitor](https://github.com/lindong28/agent-monitor); target `main` for changes.

## Development setup

Use a separate checkout or worktree. Installation writes runtime files in that checkout and CLI links under the selected HOME. Tests must use explicit fixture state and synthetic/mocked provider sources; do not read real provider stores or operate production services. Isolate the entire source-discovery chain, including managed-account discovery and session links, before running collector/loader tests. A disposable HOME or patched default path alone does not establish isolation: account discovery and previously loaded defaults can still resolve other roots. Use an access guard against real provider roots when validating these fixtures; `tests/test_export_work_reuse.py` covers the rollup loader fixtures.

```bash
test_home="$(mktemp -d)"
HOME="$test_home" CLAUDE_CONFIG_DIR="$test_home/.claude" CODEX_HOME="$test_home/.codex" \
  ./install.sh --no-services
.venv/bin/python -m pip install -r requirements-dev.txt
HOME="$test_home" CLAUDE_CONFIG_DIR="$test_home/.claude" CODEX_HOME="$test_home/.codex" \
  .venv/bin/python -m unittest discover -s tests
```

Python 3.10+ and Node are required by the existing tests. A subset can be run with `-p 'test_machine_config.py'`. Some platform-specific tests are skipped on other platforms. The test suite does not require a Gateway checkout. Installer tests isolate launchd and downloads; their pass is not evidence of a production deployment or a fresh dependency download.

When reusing an interpreter from another development checkout instead of installing this checkout's `.venv`, set `AGENT_MONITOR_PYTHON` to that interpreter's absolute path for CLI subprocesses too. This is dependency reuse, not a clean-install check. Use the current checkout as the working directory so imports exercise your changes. Internal responsibilities and source-registration requirements are in the [architecture boundaries](docs/public-architecture.md#dependency-and-state-boundaries); Node tests call frontend factories directly rather than modifying source text before evaluation.

[GitHub Actions](https://github.com/lindong28/agent-monitor/actions/workflows/ci.yml) runs the existing suite on Linux with Python 3.10 and macOS with Python 3.13, using Node 24 and an isolated HOME. Each job first runs the real installer with `--no-services`, including public dependency downloads. These two combinations are the CI coverage, not every supported OS, CPU or Python version. The jobs do not query provider accounts or install user services. A passing test suite does not replace the browser sample check below.

To exercise the reader's main flow, use the README's isolated sample and inspect the session in a browser. Tests, sample usage and real provider/account verification are distinct claims. Do not make paid provider requests merely to run the tests.

## Local task evidence

Runtime state belongs to the checkout's `state/` directory and may be a deployment-owned symlink. Its ownership and maintenance rules are in [architecture](docs/public-architecture.md#persistence-and-maintenance). Do not move it into a task directory.

Developer task materials are separate from source and business data. This maintainer environment uses `~/task-artifacts/`; each task records its inputs, logs, results and continuation state in its own directory. At completion, retain what is needed for delivery, investigation, continuation or recovery, and remove intermediates whose purpose has ended within the task's cleanup authorization. A completed task does not authorize deleting unrelated or explicitly retained materials. Existing historical task locations stay in place and can be linked from that entry; the entry does not imply that every older artifact has been indexed.

The archive-maintenance, incremental-archive, normalized-path and service-log unit tests use `tests.artifacts.ArtifactTestCase`. Each case removes only its own newly created synthetic workspace after unittest reports success and finishes teardown and cleanup. Failures, skips and interruptions retain the workspace for diagnosis; normal failure output identifies its path. Set `AGENT_MONITOR_KEEP_TEST_ARTIFACTS=1` to retain successful cases too. Use `AGENT_MONITOR_TEST_ARTIFACTS` for the workspace root; the incremental-archive and normalized-path tests retain their existing `ARCHIVE_TEST_ROOT` override. Runner logs and reports should be written outside individual case workspaces. No historical root or production backup is automatically purged. See the [lifecycle decision](docs/adr/20261029-4424-test-artifact-lifecycle.md).

An ignored checkout-local `.local-data` symlink may point to an existing external task root. Verify its target before use; a missing or broken link does not mean the data has moved into the checkout. Contributors may use their own external location and record it in their task notes; no maintainer path or harness is required to run this project. Do not put credentials, provider profiles or production state in the evidence directory.

## Choose a validation entry

Start with the [responsibility map](docs/public-architecture.md#responsibility-map), then run the existing checks for the behavior you changed. Keep the fixture state and source-discovery isolation from development setup for each command that loads product code. When reusing an interpreter, use explicit fixture paths and mocks without changing the invoking session's HOME or CODEX_HOME. Use Node 24 to match CI; Python-only commands use this checkout's `.venv/bin/python`.

Follow [where new code belongs](docs/public-architecture.md#where-new-code-belongs) when adding or moving a responsibility. Construct isolated identity/load/view instances for stateful tests; use the normal frontend factory and route interfaces for Node checks. Keep compatibility aliases only where an existing caller needs them, and include the moved owners in source/fingerprint checks.

| Purpose | Command / guide | Scope |
| --- | --- | --- |
| Full regression suite | `.venv/bin/python -m unittest discover -s tests` | Python tests and the JavaScript checks invoked by their Python wrappers; mocks do not establish provider availability |
| One affected Python test module | `.venv/bin/python -m unittest discover -s tests -p 'test_machine_config.py'` | Replace the pattern with the affected module from the map |
| Shared models and import boundaries | `.venv/bin/python -m unittest discover -s tests -p 'test_shared_contracts.py'` | Model compatibility, metadata isolation and dependency-light generation imports |
| Frontend factories and navigation | `node tests/web_modules.cjs && node tests/web_routes.cjs` | Node fixtures using normal module/route interfaces; real browser loading remains separate |
| Acceptance runner's failure/cleanup regression | `node --test scripts/accept_device_connection.test.mjs` | Separate Node test, not included by unittest discovery; uses synthetic browser-tool failures |
| First-use browser check | [README sample](README.md#try-the-included-sample-without-your-accounts) | Real parser/dashboard with fabricated logs; inspect 1,800 tokens and two records |
| Connector/device browser acceptance | [Explicit procedure below](#explicit-browser-and-connector-acceptance) | Optional browser tooling; `--launchd` changes an isolated macOS test job |
| Backend and background measurements | [Isolated benchmark commands](docs/operations/services.md#isolated-backend-investigation) | Synthetic snapshots/history, not production latency or live provider evidence |

## Explicit browser and connector acceptance

`scripts/accept_device_connection.mjs` is an optional developer acceptance runner, not a runtime dependency or part of the default CI command. It uses the public [agent-browser](https://github.com/vercel-labs/agent-browser) CLI pinned in `package-lock.json`; no maintainer helper or private harness is required. Run the development setup above first, then install the optional Node dependency and supply an installed Chrome/Chromium executable:

```bash
npm ci
node scripts/accept_device_connection.mjs \
  --browser /absolute/path/to/Chrome \
  --python "$PWD/.venv/bin/python"
```

For Google Chrome on macOS the executable is usually `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`; on Linux, use the absolute path returned by `command -v chromium` or `command -v google-chrome`. A browser installation and its system libraries are prerequisites; `npm ci` installs the CLI, not Chrome. Use a normal non-root desktop host. The runner rejects any sandbox-disabling arguments, including defaults the upstream CLI can add in containers; it does not repair system sandbox configuration. See [the dependency decision](docs/adr/20261027-a39d-public-browser-acceptance.md) for this boundary.

The runner creates isolated HOME/state and browser configuration under `~/task-artifacts/agent-monitor/device-acceptance/` (or `AGENT_MONITOR_TEST_ARTIFACTS`) and a dedicated session; it does not inherit browser/provider configuration or attach to your browser. It checks six synthetic accounts, preview/cancel, one confirmed batch, pairing failure and pairing cancellation without provider-account requests. It should exit zero after its assertions and teardown; a cleanup failure is a failure, not a successful acceptance. Child processes are stopped, while the printed evidence directory remains available for inspection and manual cleanup. On macOS with a desktop login, explicitly adding `--launchd` also exercises installation/removal of a temporary connector job through `scripts/accept_connector_lifecycle.py`. Do not use that option on a production host as a routine unit-test step. Real provider quotas, login and message delivery remain separate, authorized checks.

## Changes and reports

Keep changes tied to a concrete problem. Include expected and actual behavior, reproduction steps, OS/Python/Node versions, the source revision and relevant test results. For dashboard problems, identify the page and filters. Do not attach raw provider logs, account profiles, device codes, whole state directories or unredacted screenshots. Follow [SECURITY.md](SECURITY.md) for sensitive reports.

Preserve snapshot schemas, machine/account identities, archive retention and accounting semantics unless the change explicitly requires them. `token_cost.py` shares an arithmetic contract with its source projects; coordinate arithmetic edits and keep the shared tests intact. Runtime dependencies must remain independent of other personal projects. Never overwrite an existing state symlink or persistent lock during installation.

Update the affected public README or guides when behavior changes. Use one line per Markdown paragraph and avoid unrelated formatting changes. Run the existing tests affected by the change before proposing it for review.
