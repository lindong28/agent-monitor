"""Selected Hub configuration and local role, independent of service lifecycle."""
import json
import re
from pathlib import Path
from urllib.parse import urlparse
from project_paths import PROJECT_ROOT

ROOT = PROJECT_ROOT
CONFIG_PATH = ROOT / "hub.json"
_LOCAL_CONFIG_PATH = ROOT / "hub.local.json"
if _LOCAL_CONFIG_PATH.exists() or _LOCAL_CONFIG_PATH.is_symlink():
    CONFIG_PATH = _LOCAL_CONFIG_PATH
MACHINE_PATH = ROOT / "state" / "hub-machine"


def configuration(path=None):
    data = json.loads(Path(path or CONFIG_PATH).read_text())
    fields = {"schema_version", "hub", "url", "port", "sync_interval_seconds",
              "quota_interval_seconds", "machines"}
    if not isinstance(data, dict) or set(data) != fields or data["schema_version"] != 1:
        raise ValueError("unsupported hub configuration")
    names = data["machines"]
    if not isinstance(names, list) or not names or any(
        not isinstance(n, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", n) for n in names
    ) or len(set(names)) != len(names) or data["hub"] not in names:
        raise ValueError("hub machines must be unique machine names containing the hub")
    for key in ("port", "sync_interval_seconds", "quota_interval_seconds"):
        if type(data[key]) is not int or data[key] < 1:
            raise ValueError("%s must be a positive integer" % key)
    url = urlparse(data["url"])
    if (data["port"] > 65535 or url.scheme not in {"http", "https"} or not url.hostname
            or url.username or url.password or url.query or url.fragment or url.path not in {"", "/"}):
        raise ValueError("hub URL must be an HTTP origin without credentials")
    if url.scheme != "http" or (url.port or 80) != data["port"]:
        raise ValueError("hub URL must use HTTP and the configured listening port")
    return data


def local_machine():
    if not MACHINE_PATH.exists():
        return None
    name = MACHINE_PATH.read_text().strip()
    if name not in configuration()["machines"]:
        raise ValueError("installed hub machine is absent from hub.json")
    return name


def enabled():
    return local_machine() is not None


def is_hub():
    return enabled() and local_machine() == configuration()["hub"]


def origin():
    return configuration()["url"].rstrip("/") if enabled() else None


def interval():
    return configuration()["sync_interval_seconds"] if enabled() else 600
