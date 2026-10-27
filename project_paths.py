"""Checkout locations shared by entrypoints and internal packages.

Keep this bootstrap at the checkout root. State paths intentionally retain the
``state`` symlink rather than resolving it or creating/replacing directories.
"""
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


def project_root():
    return PROJECT_ROOT


def resource_path(*parts, root=None):
    return (PROJECT_ROOT if root is None else Path(root)).joinpath(*parts)


def state_path(*parts, root=None):
    return resource_path("state", *parts, root=root)
