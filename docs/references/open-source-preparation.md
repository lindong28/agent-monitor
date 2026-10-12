# Open-source preparation

Audience: maintainers preparing the repository for public reuse. This is a mutable preparation record, linked from the root README; it is not a release approval or a completed security review.

## Scope and current status

On 2026-10-12, the owner requested that the discussed use cases, target users and capabilities be documented and that preparation for a later open-source release begin. The owner confirmed that the license and Git history publication scope are not yet decided. This round prepares documentation and inventories concrete release gaps; it does not publish, change repository visibility, rewrite history, rotate credentials or alter deployed services.

The source baseline inspected was `ec3a69024c8a3cb25be98e65d6fb11f61474ec34`. This preparation changes only documentation. The final preparation commit is the Git commit that introduces this file; identify it from file history rather than treating the baseline as the release candidate. The intended public refs, reachable history and release artifacts remain unbound until the owner chooses the publication scope.

## Prepared materials

| Material | Result |
| --- | --- |
| [README](../../README.md) | Product positioning, target users, use-case/capability mapping and limitations; entry points for existing workflows |
| [CONTRIBUTING](../../CONTRIBUTING.md) | Existing setup and test commands, isolation requirements, useful bug-report information and sensitive-data handling |
| Installation guidance | Calls out the Gateway prerequisite, maintainer-specific fleet configuration and trusted-network runtime boundary |
| [Documentation index](../AGENTS.md) | Links this preparation record for the next release-preparation session |

## Release gaps and next actions

| Item | Current evidence / status | Owner and next action |
| --- | --- | --- |
| License and copyright | No root LICENSE or COPYING file in the inspected tracked tree; selection and copyright attribution are pending | Owner chooses the license and confirms attribution; implementer then adds the selected text and checks inherited-source obligations |
| Public scope and personal information | License/history question remains undecided; current configuration and historical documents contain maintainer-specific machine names, paths and deployment history | Owner chooses public refs/history/artifacts and the acceptable exposure of personal metadata; implementer inventories that exact set before cleanup |
| Secrets and history | No complete secret scan or reachable-history/artifact review was performed in this round | Release reviewer checks the selected tree, full reachable history and artifacts with redacted findings; any history rewriting or credential remediation requires its own authorization |
| Runtime state exclusion | `.gitignore` contains `state/`; the main checkout's existing `state` symlink still appears as untracked | Implementer covers both directory and symlink forms in ignore rules and verifies both cases before packaging; preserve live state and locks |
| Neutral deployment defaults | `machines.json` and `hub.json` ship the maintainer's fleet and Hub address; README now explains configuration before installation | Implementer prepares adopter defaults/examples and verifies that default setup does not contact maintainer infrastructure, preserving existing deployment identities |
| Gateway prerequisite | `gateway_dependency.py` requires a compatible external `src/llm_gateway/audit.py`; the installer does not provision it | Implementer verifies a publicly obtainable, compatible Gateway revision and documents its acquisition/setup, or proposes dependency changes if unavailable |
| Dashboard exposure | CLI defaults to `0.0.0.0`; account-action code explicitly treats the service as trusted-network and does not authenticate LAN clients | README now states the current boundary; release reviewer verifies onboarding follows it. Any new authentication or hosting design is a separate product change |
| Third-party materials | Installer downloads Chart.js 4.4.0 and IBM Plex fonts, including a font license file; extraction provenance points to ai-agent-config | Implementer checks the selected distribution's required notices and inherited-source rights before packaging; this round does not assert license compatibility |
| Vulnerability reporting | No tracked SECURITY.md was found; a private reporting channel is not confirmed | Owner designates or confirms a private reporting channel; implementer documents it without inventing an address or response commitment |
| Adopter installation | No clean-machine installation, public dependency access check or live browser validation in this round | Implementer runs the documented path from an isolated copy of the final candidate using obtainable prerequisites, records actual source identity/results, and resolves demonstrated setup failures |

The runtime/configuration items above are recorded for release preparation because this round preserves the running product and the owner's existing deployment. They are not waived and do not constitute an open-source-ready verdict. Public scope and license decisions can wait while the documentation preparation is completed.

## Verification boundary

This round compared documentation with the existing installer, machine configuration parser, Gateway dependency loader, CLI defaults and account-action request handler. Checks covered 4 changed/new Markdown files and 52 local links, fenced Bash syntax, and changed-text whitespace. The README's one-machine JSON example was accepted by the existing configuration parser; a copy with no self machine was rejected. These checks ran in the isolated preparation worktree with the repository's existing Python interpreter, not a fresh adopter environment.

No runtime implementation, schema, accounting or service behavior is changed. The documentation-only change did not require a runtime regression suite or production/provider calls. No independent reviewer or full open-source readiness review was run.

Preparation is complete only for the materials listed above. A later readiness review must bind its findings to the exact candidate commit, selected public history and artifacts, and separate static inspection, isolated adopter execution and actual publication. An accessible maintainer deployment or a successful local unit test does not establish a clean adopter installation.

## 2026-10-12 UI preparation follow-up

The dashboard now includes a discoverable read-only usage/onboarding guide with source boundaries, current collection status, standalone/multi-machine entry points and the external Gateway prerequisite. Fixed-window drilldowns, session tracing, period comparison and quota sorting were deployed and checked on the existing Hub. This improves adopter guidance; clean installation, neutral defaults, public dependency availability and the release decisions above remain unverified or pending. Private live screenshots are not release/demo assets.
