# Check the sandbox mount as well as file permissions

Accepted 2026-10-27. Supersedes only the `/run` destination in [the CI sandbox decision](20261027-b648-ci-linux-sandbox.md); its disposable-runner scope, archive integrity checks and product boundaries remain unchanged.

Implementation review identified that systemd normally mounts `/run` with `nosuid`. File mode 4755 alone cannot distinguish a usable setuid helper from one on a mount that ignores setuid. The actual hosted runner's mount had not been inspected when this correction was accepted.

Use `/agent-monitor-ci/chrome-sandbox`, under a root-owned mode-0755 directory directly on the root filesystem. Check the root and destination directory ownership and permissions, the helper's root ownership and mode 4755, and the actual mount options. Fail if the mount has `nosuid` or `noexec`; do not remount it or alter host policy. A job that does not satisfy these prerequisites must fail visibly. Normal installer startup and the complete suite remain the acceptance checks.
