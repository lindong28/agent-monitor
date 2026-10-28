# Recovery guidance and continuation

Reader: Developer. Decision: accepted, 2026-10-28.

The original user outcome is to understand blocked sending, choose a next action, know whether a check is possible and continue after recovery. A status endpoint and diagnostic receipt alone do not establish those outcomes. Preserve the existing provider admission and confirmation policy while adding state-specific guidance, a copyable displayed diagnostic projection and a recovery result disclosure outside the disappearing pause banner. Account selection describes inclusion, not guaranteed sendability.

The alternative of adding another unrestricted retry or pause-reset control would change provider behavior without resolving unknown causes. The implemented UI retains separate local state reads, explicitly confirmed quota checks and message sending. It does not promise that HTTP 409 or RPC -32603 establishes a root cause.

Independent decision review of the UI scope passed seven criteria before implementation. Behavioral acceptance uses the shared recovery panel through account actions and quota diagnostics, isolated synthetic responses and the existing public browser acceptance runner. Live deployment checks remain read-only; no new provider probe is authorized by this decision. Related backend observations outside this UI scope are retained in [general issues](../issues/general.md).
