# Security

agent-monitor is a personal dashboard for trusted networks. It does not authenticate dashboard clients. Usage records, project paths, account identities and account actions can be sensitive. Do not expose a running dashboard directly to the Internet or an untrusted LAN. Use `AGENT_MONITOR_BIND=127.0.0.1` for a local-only instance.

Keep `state`, local configuration, provider credentials, screenshots and diagnostic captures private. Same-origin checks protect some requests; they do not authenticate a person. Publishing this source does not make a deployment safe to expose publicly.

For a security report, use the repository's **Security → Report a vulnerability** button if available. A dedicated private reporting channel has not yet been established for this release candidate. If that button is absent, open an issue asking for a private contact without including exploit details, credentials or affected account information. Never post secrets or raw provider logs in a public issue.

This project has no stated response-time commitment or supported-release window. Keep dependency and application updates under review before deployment.
