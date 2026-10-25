"""Real isolated child/helper; synthetic reading, no provider or credential IO."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import quota_refresh as qr

root = Path(os.environ["LIFETIME_TEST_ROOT"])
if os.environ["LIFETIME_TEST_MODE"] == "delayed_start":
    time.sleep(1.0)
with qr._reader_lifetime(int(sys.argv[2]), float(sys.argv[3])):
    json.load(sys.stdin)  # Normal EOF must not end the lifetime.
    helper_code = "import time; print('ready', flush=True); time.sleep(20)"
    if os.environ["LIFETIME_TEST_MODE"] == "stuck_cleanup":
        helper_code = "import signal; signal.signal(signal.SIGTERM, signal.SIG_IGN); " + helper_code
    helper = subprocess.Popen([sys.executable, "-c", helper_code], stdout=subprocess.PIPE)
    helper.stdout.readline()
    (root / "pids.json").write_text(json.dumps([os.getpid(), helper.pid]))
    try:
        if os.environ["LIFETIME_TEST_MODE"] == "normal":
            time.sleep(.3)
            print(json.dumps({"reading": {"five_hour_pct": 12, "five_hour_resets_at": None, "seven_day_pct": None, "seven_day_resets_at": None}}), flush=True)
        else:
            time.sleep(20)
    finally:
        helper.terminate()
        helper.wait(timeout=2)
        (root / "helper-reaped").write_text(str(helper.returncode))
