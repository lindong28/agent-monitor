# Contributing

Read [README.md](README.md), [AGENTS.md](AGENTS.md) and [architecture](docs/public-architecture.md). Contributions are covered by the [MIT license](LICENSE). Open issues and pull requests at [lindong28/agent-monitor](https://github.com/lindong28/agent-monitor); target `main` for changes.

## Development setup

Use a separate checkout or worktree. Installation writes runtime files in that checkout and CLI links under the selected HOME. Tests must use a disposable HOME and synthetic/mocked provider data; do not read real provider stores or operate production services.

```bash
test_home="$(mktemp -d)"
HOME="$test_home" CLAUDE_CONFIG_DIR="$test_home/.claude" CODEX_HOME="$test_home/.codex" \
  ./install.sh --no-services
.venv/bin/python -m pip install -r requirements-dev.txt
HOME="$test_home" CLAUDE_CONFIG_DIR="$test_home/.claude" CODEX_HOME="$test_home/.codex" \
  .venv/bin/python -m unittest discover -s tests
```

Python 3.10+ and Node are required by the existing tests. A subset can be run with `-p 'test_machine_config.py'`. Some platform-specific tests are skipped on other platforms. The test suite does not require a Gateway checkout. Installer tests isolate launchd and downloads; their pass is not evidence of a production deployment or a fresh dependency download.

[GitHub Actions](https://github.com/lindong28/agent-monitor/actions/workflows/ci.yml) runs the existing suite on Linux with Python 3.10 and macOS with Python 3.13, using Node 24 and an isolated HOME. Each job first runs the real installer with `--no-services`, including public dependency downloads. These two combinations are the CI coverage, not every supported OS, CPU or Python version. The jobs do not query provider accounts or install user services. A passing test suite does not replace the browser sample check below.

To exercise the reader's main flow, use the README's isolated sample and inspect the session in a browser. Tests, sample usage and real provider/account verification are distinct claims. Do not make paid provider requests merely to run the tests.

## Changes and reports

Keep changes tied to a concrete problem. Include expected and actual behavior, reproduction steps, OS/Python/Node versions, the source revision and relevant test results. For dashboard problems, identify the page and filters. Do not attach raw provider logs, account profiles, device codes, whole state directories or unredacted screenshots. Follow [SECURITY.md](SECURITY.md) for sensitive reports.

Preserve snapshot schemas, machine/account identities, archive retention and accounting semantics unless the change explicitly requires them. `token_cost.py` shares an arithmetic contract with its source projects; coordinate arithmetic edits and keep the shared tests intact. Runtime dependencies must remain independent of other personal projects. Never overwrite an existing state symlink or persistent lock during installation.

Update the affected public README or guides when behavior changes. Use one line per Markdown paragraph and avoid unrelated formatting changes. Run the existing tests affected by the change before proposing it for review.
