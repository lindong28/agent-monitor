"""Purpose-specific source sets for drift, compilation and export admission.

Register application package roots here when adding one. Runtime state, vendor
dependencies, tests and task artifacts are not application source roots.
"""
from pathlib import Path

from project_paths import PROJECT_ROOT


PACKAGE_ROOTS = ("agent_monitor", "parsers", "ip_check", "proxy_runtime")


def python_sources(root=PROJECT_ROOT):
    root = Path(root)
    files = list(root.glob("*.py"))
    for package in PACKAGE_ROOTS:
        files.extend((root / package).rglob("*.py"))
    return sorted(path for path in files if path.is_file())


def deployment_sources(root=PROJECT_ROOT):
    root = Path(root)
    files = python_sources(root)
    files.extend((root / "proxy_runtime").rglob("*.cjs"))
    files.extend((root / "requirements.txt", root / "state/hub-machine"))
    for name in ("machines", "hub"):
        local = root / (name + ".local.json")
        files.append(local if local.exists() or local.is_symlink() else root / (name + ".json"))
    return sorted(files)


def authority_pathspecs():
    """Git paths allowed to determine the exporter code identity.

    Selected deployment configuration belongs to service identity, not the
    exporter commit. Whole package pathspecs also catch added/deleted sources.
    """
    return (
        ":(glob)*.py",
        *(f":(glob){name}/**" for name in PACKAGE_ROOTS),
        "pricing.json", "agent-monitor", "install.sh", "requirements.txt",
        ":(glob)lib/**",
    )


def compile_sources(root=PROJECT_ROOT):
    """Validate runtime Python without writing bytecode into the source tree."""
    for path in python_sources(root):
        compile(path.read_bytes(), str(path), "exec")


if __name__ == "__main__":
    compile_sources()
