"""Installer asset transport using the installed product system-proxy runtime."""
import os
import subprocess
import sys

from agent_monitor.network import network_policy


def download(url, destination):
    try:
        result = subprocess.run(
            ['curl', '-q', '--fail', '--location', '--silent', '--show-error',
             '--connect-timeout', '15', '--max-time', '120', url, '--output', destination],
            env=network_policy.child_environment(os.environ),
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=125,
        )
    except (OSError, subprocess.TimeoutExpired, network_policy.NetworkPolicyError) as exc:
        print(f'Asset download failed ({type(exc).__name__}); no alternate route attempted.', file=sys.stderr)
        return 1
    if result.returncode:
        # curl diagnostics may contain authenticated proxy URLs. Keep the safe code.
        print(f'Asset download failed (curl exit {result.returncode}); no alternate route attempted.', file=sys.stderr)
    return result.returncode


if __name__ == '__main__':
    if len(sys.argv) != 3:
        sys.exit('usage: python -m agent_monitor.runtime.asset_download URL DESTINATION')
    sys.exit(download(*sys.argv[1:]))
