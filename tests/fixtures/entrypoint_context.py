"""Report synthetic child context without importing providers or credentials."""
import json
import os
from pathlib import Path
import sys


names = ("CLAUDE_CONFIG_DIR", "CLAUDE_SECURESTORAGE_CONFIG_DIR", "CODEX_HOME",
         "ENTRYPOINT_LITERAL")
value = {
    "cwd": str(Path.cwd()),
    "environment": {name: os.environ.get(name) for name in names},
    "identities": {
        name: (Path(os.environ[name]) / "identity.txt").read_text()
        if (Path(os.environ[name]) / "identity.txt").is_file() else None
        for name in names[:3]
    },
    "arguments": sys.argv[1:],
    "implementation": str(Path(__file__).resolve()),
}
Path(os.environ["ENTRYPOINT_RECORD"]).write_text(json.dumps(value))
if os.environ.get("ENTRYPOINT_KIND") == "quota":
    print(json.dumps({"reading": {"five_hour_pct": 12, "five_hour_resets_at": None,
                                  "seven_day_pct": None, "seven_day_resets_at": None}}))
elif os.environ.get("ENTRYPOINT_KIND") == "egress":
    print(json.dumps({"host": sys.argv[1]}))
else:
    print(json.dumps(value), flush=True)
