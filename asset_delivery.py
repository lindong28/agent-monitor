"""Versioned static delivery without a build step or persistent cache."""

from collections import OrderedDict
import gzip
import hashlib
from pathlib import Path
import re
import threading


_cache = OrderedDict()
_lock = threading.Lock()
_MAX_BYTES = 16 * 1024 * 1024
_HTML_ASSET = re.compile(r'((?:src|href)=")(/web/[^"?]+)(")')


def read(path):
    """Reuse unchanged bytes; atomic replacement and in-place edits invalidate."""
    path = Path(path).resolve()
    stat = path.stat()
    key = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    with _lock:
        cached = _cache.get(path)
        if cached and cached[0] == key:
            _cache.move_to_end(path)
            return cached[1]
    data = path.read_bytes()
    # A concurrent edit must not associate new bytes with an old stat identity.
    after = path.stat()
    if key == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        with _lock:
            _cache[path] = (key, data)
            _cache.move_to_end(path)
            while len(_cache) > 256 or sum(len(item[1]) for item in _cache.values()) > _MAX_BYTES:
                _cache.popitem(last=False)
    return data


def files(root, ancestors=frozenset()):
    """Include supported vendor symlinks, but never follow a directory cycle."""
    root = Path(root)
    resolved = root.resolve()
    if resolved in ancestors:
        return
    try:
        children = sorted(root.iterdir())
    except (FileNotFoundError, NotADirectoryError):
        return
    for path in children:
        if path.is_dir():
            yield from files(path, ancestors | {resolved})
        elif path.is_file():
            yield path


def snapshot(root):
    """Bind the advertised version to the exact bytes served by this request."""
    digest = hashlib.sha256()
    content = {}
    for path in files(root):
        name = path.relative_to(root).as_posix()
        try:
            data = read(path)
        except (FileNotFoundError, NotADirectoryError):
            # Updating another asset must not make the requested page disappear.
            continue
        content[name] = data
        digest.update(name.encode() + b"\0")
        digest.update(hashlib.sha256(data).digest())
    return digest.hexdigest()[:16], content


def signature(root):
    return snapshot(root)[0]


def html(data, version):
    text = data.decode("utf-8")
    text = _HTML_ASSET.sub(lambda match: f'{match[1]}{match[2]}?v={version}{match[3]}', text)
    return text.replace("<head>", f'<head>\n<meta name="asset-version" content="{version}">').encode("utf-8")


def encode(data, headers):
    """Negotiate gzip for useful-sized responses without growing wire bytes."""
    if len(data) < 1024:
        return data, False
    for item in headers.get("Accept-Encoding", "").lower().split(","):
        coding, *parameters = item.strip().split(";")
        if coding.strip() != "gzip":
            continue
        quality = 1.0
        try:
            for parameter in parameters:
                key, _, value = parameter.strip().partition("=")
                if key == "q":
                    quality = float(value)
        except ValueError:
            quality = 0.0
        if 0 < quality <= 1:
            compressed = gzip.compress(data, compresslevel=6, mtime=0)
            if len(compressed) < len(data):
                return compressed, True
        break
    return data, False
