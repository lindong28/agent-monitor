"""Checkout locations shared by entrypoints and internal packages.

Anchor the checkout from this package. State paths intentionally retain the
``state`` symlink rather than resolving it or creating/replacing directories.
"""
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def project_root():
    return PROJECT_ROOT


def resource_path(*parts, root=None):
    return (PROJECT_ROOT if root is None else Path(root)).joinpath(*parts)


def state_path(*parts, root=None):
    return resource_path("state", *parts, root=root)


def configuration_path(name, *, root=None):
    """Select one operator override without silently changing machine identity."""
    base = PROJECT_ROOT if root is None else Path(root)
    local = base / "config" / (name + ".local.json")
    legacy = base / (name + ".local.json")
    present = [p for p in (local, legacy) if p.exists() or p.is_symlink()]
    if len(present) > 1:
        raise ValueError(
            f"Conflicting configuration: {local} and {legacy}. "
            "Preserve both, reconcile their contents and keep one override before restarting."
        )
    return present[0] if present else base / "config" / (name + ".json")
