# Contributing

agent-monitor is preparing for open-source release. Licensing and publication scope are still pending; see the [preparation checklist](docs/references/open-source-preparation.md). This guide describes the current development workflow and does not claim that release preparation is complete.

## Get oriented

Read [README.md](README.md) for the product, installation and supported workflows, [AGENTS.md](AGENTS.md) for repository constraints, and [docs/architecture.md](docs/architecture.md) for runtime ownership. Read [docs/AGENTS.md](docs/AGENTS.md) before changing documentation.

Use a separate checkout or worktree for development, with its own runtime state. Do not point it at an existing installation's `state` directory or run its collector against a production database. The runtime requires an independent compatible llm-gateway checkout; set `LLM_GATEWAY_ROOT` to its absolute path. Gateway owns its ledger schema, pricing policy and inference credentials.

## Set up and test

Use Python 3.10 or newer and Node for the test suite, plus the installation prerequisites in the README. `--no-services` skips service changes and the post-install network probe, but still creates CLI links under the selected HOME and downloads runtime dependencies and browser assets. Use a disposable HOME for staging if an existing installation owns those links; a worktree alone does not isolate HOME. In that development environment, use the existing staging installer:

```bash
./install.sh --no-services
.venv/bin/python -m pip install -r requirements-dev.txt
```

This setup path has been checked against the installer source, but a clean adopter installation remains pending.

Run tests with an isolated HOME and provider input directories. Replace the Gateway path below with the prerequisite checkout's absolute path before running:

```bash
test_home="$(mktemp -d)"
HOME="$test_home" \
  CLAUDE_CONFIG_DIR="$test_home/.claude" \
  CODEX_HOME="$test_home/.codex" \
  LLM_GATEWAY_ROOT=/absolute/path/to/llm-gateway \
  .venv/bin/python -m unittest discover -s tests
```

Tests must use temporary state and synthetic or mocked provider data, not real provider stores. For a focused check, the existing unittest entry accepts a pattern such as `-p 'test_machine_config.py'`. Run the existing tests affected by a change; report the command, result and any missing prerequisites. Unit tests and source checks do not establish live deployment or browser acceptance. Backend cost work uses the separate [isolated diagnostic entry](docs/proactive-performance.md).

## Describe a change or report a problem

Provide the expected behavior, observed behavior, minimal reproduction, operating system, Python/Node versions when relevant, and the source revision. For dashboard issues, identify the page, filters and whether the installation is standalone or Hub. Include sanitized diagnostics and observation times when collection freshness matters.

Never attach credentials, device authorization codes, account profiles, raw provider stores, or the whole `state` directory. Remove account emails, private project paths, session content and private infrastructure details from examples unless their disclosure is intentional. Screenshots and exported requests can contain the same information. A private vulnerability-reporting channel has not yet been documented here; do not post exploitable details or secrets in a public issue.

Keep changes tied to the stated problem, explain the resulting behavior, and include relevant validation. Preserve snapshot schemas, account/machine identities, retained history and accounting semantics unless a change is explicitly agreed. `token_cost.py` has a byte-identical cross-project contract; arithmetic changes require coordination with its consumers. Do not modify llm-gateway as an incidental fix in this repository.

Update the README when usage changes and the affected architecture or operations documents when their facts change. Record user-visible behavior changes in [CHANGELOG.md](CHANGELOG.md); retain dated evidence and decisions rather than rewriting them as current observations.
