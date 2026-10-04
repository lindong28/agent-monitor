"""Compatibility import; all audit implementation lives in llm-gateway."""
import sys
import gateway_dependency  # resolves the independent checkout
from llm_gateway import audit

sys.modules[__name__] = audit
