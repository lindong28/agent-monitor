"""Private quota request, receipt and observation-file publication protocol."""
import json
import os
import uuid


def write_message(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(path.name + "." + uuid.uuid4().hex)
    try:
        with staging.open("x") as handle:
            os.chmod(staging, 0o600)
            json.dump(value, handle, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        staging.replace(path)
    finally:
        staging.unlink(missing_ok=True)
