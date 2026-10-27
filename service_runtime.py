"""Deployment identity and bounded local readiness; no provider operations."""
import hashlib
import json
from pathlib import Path
import time
import urllib.request
from agent_monitor.source_inventory import deployment_sources


def signature(root):
    root = Path(root)
    files = deployment_sources(root)
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes() if path.exists() else b'<absent>')
    return digest.hexdigest()


def arguments(output):
    """Parse launchctl print's arguments block, never infer ownership from disk."""
    inside = False
    result = []
    for line in output.splitlines():
        line = line.strip()
        if line == 'arguments = {':
            inside = True
        elif inside:
            if line == '}':
                return result
            result.append(line)
    return []


def read_health(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=2) as response:
            value = json.loads(response.read(16384))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def matches(value, root, expected):
    return bool(value and value.get('source_root') == str(root)
                and value.get('deployment_signature') == expected)


def configured_signature(source_signature, settings):
    return hashlib.sha256((source_signature + json.dumps(settings, sort_keys=True)).encode()).hexdigest()


def wait_for_identity(read, root, expected, *, timeout=30):
    deadline = time.monotonic() + timeout
    while True:
        value = read()
        if matches(value, root, expected):
            return value
        if time.monotonic() >= deadline:
            raise ValueError('Target process identity was not confirmed before the readiness deadline; startup configuration and saved data remain. Service availability is unverified; inspect service status and logs before retrying.')
        time.sleep(min(0.25, max(0, deadline - time.monotonic())))
