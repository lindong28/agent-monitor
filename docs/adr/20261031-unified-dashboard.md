# System network page and one dashboard entry

Accepted 2026-10-31 following the user request to show only system diagnostics and complete account tasks without Hub navigation. Supersedes the presentation and account-entry decisions in [local network UI](20261031-b71a-local-network-ui.md); account ownership, identity, pause protection and egress policy remain.

The network page directly shows existing server system diagnostics. Entry reads only cached evidence; explicit detection retains its existing request policy. The process CLI and technical APIs remain, but no process UI, browser process scan or connector popup is triggered by this page.

Existing account controls operate on the current origin. A fixed-target allowlist forwards their actions to the configured owner without redirects, ambient credentials, retries or local fallback. Existing account identities, pause IDs and application capabilities pass intact. Unknown write outcomes require a state read before another submission. Statistics reads and local health/network ownership remain separate.

The connector keeps its configured service registration. Only account `/pair` may admit this installation's exact recorded loopback dashboard port; admission requires a live owned process record, matching health PID/source root, matching configured service, and an ownership recheck. Caller-provided runtime context alone is insufficient. The help command targets the configured service; capabilities are delivered to the admitted opener with the existing nonce check, never in a URL.

Independent decision review identified and resolved local-origin trust, connector registration target and recovery timeout gaps. Hiding links without actions would remove capability; unrestricted forwarding or arbitrary loopback aliases would expand trust. The selected change reuses existing handlers and storage without adding an account copy or migrating persistent data. Verification and deployment results belong to task evidence, not this decision.
