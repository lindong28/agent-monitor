# Link the sandbox at Chromium's selected path

Accepted 2026-10-27 after independent review. Supersedes the environment-variable-only selection in [the CI sandbox decision](20261027-b648-ci-linux-sandbox.md); the [protected root-filesystem destination](20261027-c291-ci-sandbox-mount.md) and disposable-runner scope remain unchanged.

The hosted job satisfied the protected copy's ownership, permissions and mount checks, but Electron still failed against the original adjacent `chrome-sandbox`. Chromium's `GetSandboxBinaryPath()` prefers that adjacent file and consults `CHROME_DEVEL_SANDBOX` only when the adjacent candidate is absent.

After preparing the protected copy, replace the generated adjacent file with a symbolic link to that exact copy. Check the link target and resolved ownership/mode, then run the normal installer and suite. The protected executable remains root-owned; no sandbox is disabled. Actual Linux startup and tests were still unverified at this correction's acceptance.
