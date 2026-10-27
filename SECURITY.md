# Security

agent-monitor is a personal dashboard for trusted networks. It does not authenticate dashboard clients. Usage records, project paths, account identities and account actions can be sensitive. Do not expose a running dashboard directly to the Internet or an untrusted LAN. Use `AGENT_MONITOR_BIND=127.0.0.1` for a local-only instance.

Keep `state`, local configuration, provider credentials, screenshots and diagnostic captures private. Same-origin checks protect some requests; they do not authenticate a person. Publishing this source does not make a deployment safe to expose publicly.

For a security report, use [GitHub's private vulnerability report form](https://github.com/lindong28/agent-monitor/security/advisories/new), also available under **Security → Report a vulnerability**. Private reporting is enabled for this repository. Include the affected revision, impact and reproduction steps using synthetic data. Never post secrets or raw provider logs in a public issue. If you cannot access the private form, open an issue asking for a private contact without including exploit details, credentials or affected account information.

This project has no stated response-time commitment or supported-release window. Keep dependency and application updates under review before deployment.
