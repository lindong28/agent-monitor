# Optional repository-scoped SSH for deployment Git access

2026-11-06: Allow an operator to configure a deployment checkout's existing `origin` with SSH when its HTTPS publication queries repeatedly fail. This is an installation choice, not a change to the public HTTPS clone default or the deployment protocol. The operator approved creating a server-local, repository-only, read-only deploy key and validating the normal Hub rollout.

Deployment still reads live `origin/main`, verifies the fixed release and ancestry, and uses the existing reservations and locks. Select the key through checkout-local `core.sshCommand`, with explicit identity selection, noninteractive authentication and strict host verification against verified GitHub host keys. Keep private keys on their owning server, outside the checkout; retain only recovery metadata in the project. Do not reuse unrelated keys, forward a personal agent or disable host verification.

Keeping HTTPS with larger deadlines or retries was not selected: the observed failures do not establish that the current budget is insufficient, and a TLS interruption is a different failure. Bundle transfer remains the existing recovery for unavailable Git objects; it does not replace live publication verification. Moving all source distribution into the Hub would be a separate deployment redesign.

Before changing the URL, preserve its original value and any local SSH command, then verify the intended repository over SSH. Restore those settings on a failed switch and revoke only the new deploy key when abandoning it. A lost private key is recovered by replacing and revoking the deploy key, not by copying another machine's credential.

Independent decision review found no blocking findings. SSH authentication and normal topology deployment require live validation; this decision does not establish the cause of past HTTPS failures or promise that SSH cannot time out. See the [operator procedure](../public-usage.md#deployment-git-transport).
