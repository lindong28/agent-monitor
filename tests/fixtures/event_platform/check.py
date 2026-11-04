"""Deliberate branch-only failure for bounded automated repair acceptance."""
from pathlib import Path

value = (Path(__file__).parent / 'value.txt').read_text().strip()
if value != '42':
    raise SystemExit('Acceptance value must be 42; received ' + repr(value))
print('Acceptance value is 42')
