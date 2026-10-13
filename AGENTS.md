# AGENTS.md

This repository owns agent-monitor: CLI, Web dashboard, usage collection, snapshots, quota lookup and network diagnostics. Read [README.md](README.md) and [docs/architecture.md](docs/architecture.md) for runtime boundaries; read [docs/AGENTS.md](docs/AGENTS.md) before editing documentation.

- Keep runtime dependencies independent of ai-agent-config and llm-gateway. Gateway calls belong to that separate product; do not reintroduce its reader, UI or service dependency.
- Install with `./install.sh`; `.venv`, `state` and `web/vendor` are runtime files and stay untracked. Use `--no-services` for staging. Existing state may be a symlink; never replace it or its persistent locks during ordinary installation.
- Preserve snapshot schemas, machine/account identities, archive retention and accounting semantics unless explicitly asked to change them. `token_cost.py` has a byte-identical cross-project contract; coordinate any arithmetic edit with its consumers.
- Use isolated HOME/state for tests, and report live verification separately. Do not print credentials or read real provider stores in tests.
- For backend development and performance requests, follow the user-scope performance entry and [the backend cost map](docs/architecture.md#backend-performance-checks). Reuse [the isolated diagnostic and regression entry](docs/proactive-performance.md); track shared-path residual costs explicitly rather than treating one page's result as whole-backend coverage.
- Preserve other writers' work. The user grants standing authorization to deploy agent-monitor and perform the service changes/restarts required by those deployments; do not ask for deployment approval again. Git push and other remote publication still require explicit authorization. Deploy from the main worktree at main HEAD; development worktrees are not persistent deployment roots. Ordinary source edits are not deployment proof.
- Before a commit, run the applicable review gate and the existing tests affected by the change. Use the repository's interpreter and Node for Web tests: `.venv/bin/python -m unittest discover -s tests`.
