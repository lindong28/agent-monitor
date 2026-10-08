"""Run full-history Hub HTTP projections outside the request-serving heap."""

from contextlib import redirect_stdout
import json
import sys
import threading

import sync_process


PATHS = frozenset({
    "/api/llm-calls", "/api/llm-calls-page", "/api/llm-call-filters",
    "/api/llm-calls-export",
})
# User-approved bound; detail and health requests never acquire these slots.
_slots = threading.BoundedSemaphore(2)


def response(path, query):
    if path not in PATHS:
        raise ValueError("unsupported query worker route")
    with _slots, sync_process.supervised_worker(__file__, text=False) as process:
        process.stdin.write(json.dumps({"path": path, "query": query}).encode() + b"\n")
        process.stdin.flush()
        header = json.loads(process.stdout.readline(16384))
        if (not isinstance(header, dict) or set(header) != {"status", "length"}
                or type(header["status"]) is not int or not 100 <= header["status"] <= 599
                or type(header["length"]) is not int or header["length"] < 0):
            raise ValueError("invalid query worker header")
        # Read before wait: exports can exceed the pipe buffer. Never decode the
        # business JSON in the parent, and never send partial output as success.
        data = process.stdout.read()
        code = process.wait()
        if code != 0 or len(data) != header["length"]:
            raise RuntimeError("query worker returned an incomplete response (exit %s)" % code)
        return header["status"], data


def _worker():
    request = json.loads(sys.stdin.readline())
    if request["path"] not in PATHS:
        raise ValueError("unsupported query worker route")
    sync_process.watch_parent()
    output = sys.stdout.buffer
    with redirect_stdout(sys.stderr):
        import server
        try:
            payload = server.ROUTES[request["path"]](request["query"])
            status = 200
        except server.llm_attempts.ProjectionError as exc:
            payload, status = exc.payload(), exc.status
        data = json.dumps(payload, default=server._json_default).encode("utf-8")
    output.write(json.dumps({"status": status, "length": len(data)}).encode() + b"\n")
    output.write(data)
    output.flush()


if __name__ == "__main__":
    if sys.argv[1:] != ["--worker"]:
        raise SystemExit("internal query worker")
    _worker()
