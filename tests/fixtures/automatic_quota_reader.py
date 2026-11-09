"""Synthetic quota child: send only to the caller's loopback HTTP counter."""
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from agent_monitor.network import network_policy

url = sys.argv[1]
assert urlsplit(url).hostname == '127.0.0.1'
with network_policy.session() as client:
    response = client.get(url, timeout=2)
    response.raise_for_status()
print(json.dumps({'reading': {'five_hour_pct': 12, 'five_hour_resets_at': None,
    'seven_day_pct': 34, 'seven_day_resets_at': None, 'updated_at': '2026-11-09T00:00:00Z'}}))
