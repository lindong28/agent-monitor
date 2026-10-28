"""CLI adapter extracted from the public agent-monitor command."""


def main():
    import os
    import sys
    from pathlib import Path

    root, host, port, log_file = sys.argv[1:]
    import agent_monitor.runtime.process_identity as process_identity
    pid = os.fork()
    if pid:
        try:
            process_identity.record(Path(log_file).parent, Path(root), pid)
        except Exception:
            # This unreaped child belongs to this launcher; its PID cannot recycle.
            os.kill(pid, 15)
            os.waitpid(pid, 0)
            raise
        print(pid)
        raise SystemExit(0)

    os.setsid()
    import agent_monitor.runtime.deployment_guard as deployment_guard
    deployment_guard.detach_guard()
    with open(os.devnull, "rb", buffering=0) as stdin, open(log_file, "ab", buffering=0) as log:
        os.dup2(stdin.fileno(), 0)
        os.dup2(log.fileno(), 1)
        os.dup2(log.fileno(), 2)
    # sys.executable, not a name resolved again through PATH: the server must run
    # on the same interpreter this launcher already chose.
    os.chdir(root)
    os.execv(sys.executable, [sys.executable, "-m", "agent_monitor.http_server", "--host", host, "--port", port])


if __name__ == "__main__":
    main()
