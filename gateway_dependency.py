"""Locate the independent Gateway checkout used by this consumer."""
import os
from pathlib import Path
import sys

ROOT = Path(os.environ.get("LLM_GATEWAY_ROOT", Path.home() / "research/llm-gateway")).expanduser().resolve()
PACKAGE = ROOT / "src/llm_gateway"
if not (PACKAGE / "audit.py").is_file():
    raise RuntimeError("Independent llm-gateway checkout unavailable; set LLM_GATEWAY_ROOT to its root")
sys.path.insert(0, str(ROOT / "src"))


def source_files():
    return sorted(PACKAGE.rglob("*.py"))
