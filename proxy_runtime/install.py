#!/usr/bin/env python3
"""Install the pinned resolver binary. Bootstrap only; no account traffic."""
import fcntl
import hashlib
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

VERSION = "45.0.0"
ROOT = Path(__file__).resolve().parents[1]
CHECKSUMS = {
    "darwin-arm64": "896c3a50b148e89a158dca08a6996a022ddf39b23aac871e107f5b4087f8caa9",
    "darwin-x64": "7877c282e74a52f8d87949536a25735cf9982de1265b544c9cd258dd14eff4de",
    "linux-arm64": "a80d92b1ad6c111545a12632b8a75cedce614a41866e500810d4050d947f90e9",
    "linux-x64": "22f6a10bd3f7fef987ddbb379d3375ce664ad0c98a9a50f80c89f1718a0c3e3a",
}


def platform_key():
    arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x64", "AMD64": "x64"}.get(platform.machine())
    key = platform.system().lower() + "-" + str(arch)
    if key not in CHECKSUMS:
        raise RuntimeError("system proxy runtime is unsupported on this platform")
    return key


def runtime_directory():
    return ROOT / ".runtime" / ("electron-" + VERSION + "-" + platform_key())


def executable(directory=None):
    directory = directory or runtime_directory()
    return directory / ("Electron.app/Contents/MacOS/Electron" if platform.system() == "Darwin" else "electron")


def unpack(archive, target):
    with zipfile.ZipFile(archive) as package:
        for entry in package.infolist():
            path = target / entry.filename
            if not path.resolve().is_relative_to(target.resolve()):
                raise RuntimeError("unsafe runtime archive path")
            mode = entry.external_attr >> 16
            if entry.is_dir():
                path.mkdir(parents=True, exist_ok=True)
            elif stat.S_ISLNK(mode):
                link = package.read(entry).decode()
                if not (path.parent / link).resolve().is_relative_to(target.resolve()):
                    raise RuntimeError("unsafe runtime archive link")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.symlink_to(link)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with package.open(entry) as source, path.open("wb") as output:
                    shutil.copyfileobj(source, output)
                path.chmod(0o755 if mode & 0o111 else 0o644)


def install():
    destination = runtime_directory()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with (destination.parent / "install.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        receipt = destination / ".archive-sha256"
        expected = CHECKSUMS[platform_key()]
        if executable(destination).is_file() and receipt.exists() and receipt.read_text().strip() == expected:
            return "present"
        with tempfile.TemporaryDirectory(prefix=".electron-", dir=destination.parent) as scratch:
            scratch = Path(scratch)
            archive = scratch / "runtime.zip"
            url = f"https://github.com/electron/electron/releases/download/v{VERSION}/electron-v{VERSION}-{platform_key()}.zip"
            start = time.monotonic()
            digest = hashlib.sha256()
            with urllib.request.urlopen(url, timeout=30) as response, archive.open("wb") as output:
                while True:
                    if time.monotonic() - start > 600:
                        raise TimeoutError("runtime download exceeded bootstrap budget")
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                    output.write(chunk)
            if digest.hexdigest() != expected:
                raise RuntimeError("runtime archive checksum mismatch")
            extracted = scratch / "extracted"
            extracted.mkdir()
            unpack(archive, extracted)
            if not executable(extracted).is_file():
                raise RuntimeError("runtime archive missing executable")
            (extracted / ".archive-sha256").write_text(expected + "\n")
            if destination.exists():
                # Only this pinned generated runtime, never HOME/state/config.
                shutil.rmtree(destination)
            os.replace(extracted, destination)
    return "installed"


if __name__ == "__main__":
    try:
        result = install()
        if platform.system() == "Linux":
            dependencies = subprocess.run(["ldd", str(executable())], capture_output=True, text=True, timeout=10)
            missing = [line.split()[0] for line in dependencies.stdout.splitlines() if "=> not found" in line]
            if missing:
                raise SystemExit("system proxy runtime needs Linux libraries: " + ", ".join(sorted(set(missing))))
        # Real startup before service replacement: no account or target request.
        sys.path.insert(0, str(ROOT))
        from agent_monitor.network import network_policy
        os.environ.pop(network_policy.INTERNAL, None)
        try:
            network_policy.endpoint()
        except network_policy.NetworkPolicyError as error:
            raise SystemExit("system proxy runtime startup failed (" + error.kind + "). "
                "On Linux, provide Chromium shared libraries and a usable OS sandbox; "
                "see README system proxy requirements. No service was replaced by this check.") from None
        finally:
            network_policy.close()
        print("system proxy runtime startup verified", file=sys.stderr)
        print(result)
    except Exception:
        # Download exceptions may contain an authenticated bootstrap proxy URL.
        raise SystemExit("system proxy runtime installation failed; check bootstrap network/disk and rerun ./install.sh")
