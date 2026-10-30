"""User-owned macOS connector lifecycle; never reads provider credentials."""
import argparse
import contextlib
from agent_monitor.runtime import deployment_guard
import json
import os
from pathlib import Path
import platform
import plistlib
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
import urllib.request
from agent_monitor.runtime import service_runtime

from agent_monitor.paths import PROJECT_ROOT

ROOT = PROJECT_ROOT
LABEL = "com.agent-monitor.connector"


def paths():
    home = Path.home()
    return (home / "Library/LaunchAgents" / (LABEL + ".plist"),
            home / "Library/Application Support/agent-monitor/connector-status.json")


def origin(value):
    p = urlsplit(value)
    if (p.scheme not in ("http", "https") or not p.hostname or p.username or p.password
            or p.path not in ("", "/") or p.query or p.fragment):
        raise ValueError("服务地址必须是页面的 http(s) 根地址。")
    # Force validation of malformed ports before saving a launch job.
    p.port
    return value.rstrip("/")


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.write("\n")
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def record(path, stage, *, server, instance_id=None, failure=None):
    if not path:
        return
    previous = read_state(path)
    value = {"stage": stage, "server": server, "pid": os.getpid(),
             "observed_at": time.time(), "instance_id": instance_id}
    if failure is not None:
        value["last_failure"] = dict(failure, observed_at=value["observed_at"])
    elif previous.get("last_failure"):
        value["last_failure"] = previous["last_failure"]
    atomic_json(path, value)


def read_state(path):
    try:
        value = json.loads(Path(path).read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def health():
    # This is a loopback management check, never an outbound route probe.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open("http://127.0.0.1:39002/health", timeout=2) as response:
            value = json.loads(response.read(8192))
        if isinstance(value, dict) and value.get("service") == LABEL and value.get("source_root") == str(ROOT):
            return value
    except (OSError, ValueError):
        pass
    return None


def definition(server, state_path):
    return {"Label": LABEL,
            "ProgramArguments": [str(ROOT / ".venv/bin/python"), "-m", "agent_monitor.network.client_connector",
                                 "--server", origin(server), "--state-file", str(state_path)],
            "WorkingDirectory": str(ROOT), "RunAtLoad": True, "KeepAlive": True,
            "ThrottleInterval": 30, "LimitLoadToSessionType": "Aqua",
            "StandardOutPath": "/dev/null", "StandardErrorPath": "/dev/null",
            "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                "AGENT_MONITOR_DEPLOYMENT_SIGNATURE": service_runtime.configured_signature(
                    service_runtime.signature(ROOT), [origin(server), str(state_path)])}}


def launch(*args):
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, timeout=15)


def job_target():
    return f"gui/{os.getuid()}/{LABEL}"


def owned_definition(path):
    if not path.exists() and not path.is_symlink():
        return None
    try:
        value = plistlib.loads(path.read_bytes())
        argv = value.get("ProgramArguments", []) if isinstance(value, dict) else []
        if (isinstance(argv, list) and value.get("Label") == LABEL
                and argv[:1] == [str(ROOT / ".venv/bin/python")]
                and (argv[1:2] == [str(ROOT / "client_connector.py")] and len(argv) == 6
                     or argv[1:3] == ["-m", "agent_monitor.network.client_connector"] and len(argv) == 7
                     and value.get("WorkingDirectory") == str(ROOT))
                and argv[-4] == "--server" and argv[-2] == "--state-file"):
            origin(argv[-3])
            return value
    except (OSError, ValueError, plistlib.InvalidFileException):
        pass
    raise ValueError("连接服务配置属于其他安装或无法识别；已保留，请从原安装管理它。")


def inspect():
    plist, state_path = paths()
    managed = owned_definition(plist)
    loaded = owned_loaded(managed)
    live = health()
    saved = read_state(managed["ProgramArguments"][-1] if managed else state_path)
    configured = managed["ProgramArguments"][-3] if managed else None
    connected = bool(live and live.get("connected") and (not configured or live.get("server") == configured))
    return {"installed": bool(managed), "loaded": loaded, "connected": connected,
            "server": configured or (live or {}).get("server"),
            "device": (live or {}).get("label"), "observation": live, "last_status": saved}


def owned_loaded(managed):
    result = launch("print", job_target())
    if result.returncode == 113:
        return False
    if result.returncode:
        raise ValueError("无法核实连接服务加载状态；现有服务及配置已保留。")
    if not managed or service_runtime.arguments(result.stdout) != managed["ProgramArguments"]:
        raise ValueError("已加载连接服务属于其他安装或无法识别；未停止或替换它。")
    return True


def show_status(value, as_json=False):
    if as_json:
        print(json.dumps(value, ensure_ascii=False))
        return
    if value["connected"]:
        print(f"本机网络通道已连接：{value['device']} → {value['server']}。")
        print("可在网络页点击「读取本机 Agent」；账号页可点击「开始新一轮」，发送前仍需确认出口。")
    elif not value["installed"] and not value["loaded"] and value.get("observation") is None:
        print("可选连接服务未启用；无需修复。需要读取本机 Agent 网络或通过本机发送账号请求时再安装。")
    else:
        print("本机网络通道尚未就绪；当前无法读取本机 Agent 网络或通过本机发送账号请求。")
        print("运行 agent-monitor connect install --server <页面根地址> 启用或恢复连接服务。")
    print("登录自动启动：" + ("已启用" if value["installed"] else "未启用")
          + "；服务加载：" + ("已加载" if value["loaded"] else "未加载"))
    previous = value["last_status"]
    if previous.get("last_failure"):
        print("最近失败诊断（历史记录，不代表当前仍失败）：" + json.dumps(previous["last_failure"], ensure_ascii=False))
    print("本次只检查内网连接；未检查上游账号或公网出口。")


def install(server):
    plist, state_path = paths()
    if not (ROOT / ".venv/bin/python").is_file():
        raise ValueError("先在此安装目录运行 ./install.sh --no-services，再启用连接服务。")
    old = owned_definition(plist)
    if old:
        state_path = Path(old["ProgramArguments"][-1])
    wanted = definition(server, state_path)
    loaded = owned_loaded(old)
    expected = wanted["EnvironmentVariables"]["AGENT_MONITOR_DEPLOYMENT_SIGNATURE"]
    if loaded and old == wanted and service_runtime.matches(health(), ROOT, expected):
        print("连接服务已启用，保留现有连接。若通道不可用，可运行 agent-monitor connect restart。")
        return inspect()
    if not loaded:
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", 39002))
            except OSError:
                raise ValueError("本机39002端口已被占用；未停止该进程。请先关闭此前的前台连接命令，或检查端口占用。") from None
    if loaded and launch("bootout", job_target()).returncode:
        raise ValueError("旧连接服务卸载失败；原配置保留，请用 connect status 检查。")
    if loaded:
        deadline = time.monotonic() + 30
        while owned_loaded(old):
            if time.monotonic() >= deadline:
                raise ValueError("旧连接服务卸载尚未完成；原配置保留，未加载新服务。请用 agent-monitor service status 检查后再重试。")
            time.sleep(0.25)
    plist.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=LABEL + ".", dir=plist.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            plistlib.dump(wanted, stream)
        os.replace(temporary, plist)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    # Match the existing Hub's bounded local restart budget. A failed command
    # does not establish absence; only retry after checking the actual job.
    for attempt in range(1, 4):
        try:
            result = launch("bootstrap", f"gui/{os.getuid()}", str(plist))
        except subprocess.TimeoutExpired:
            raise ValueError("连接服务加载超时，结果未核实；未重复加载。请运行 agent-monitor service status 检查。") from None
        if result.returncode == 0:
            break
        print(f"连接服务加载尝试 {attempt}/3：launchctl bootstrap exit={result.returncode}；正在核对加载状态。", file=sys.stderr)
        if owned_loaded(wanted):
            break
        if attempt == 3:
            raise ValueError(f"连接服务配置已保存，加载失败（exit={result.returncode}）；服务未加载。请检查 launchctl print {job_target()}，再运行 agent-monitor service restart connector。")
        time.sleep(1)
    service_runtime.wait_for_identity(health, ROOT, expected)
    print("本机连接服务已启用，登录后自动启动；关闭终端不会停止。")
    print("正在建立内网连接，可用 agent-monitor connect status 查看；停用使用 agent-monitor connect stop。")
    # Bootstrap is not a readiness proof; status reports pending separately.
    return inspect()


def stop():
    plist, state_path = paths()
    managed = owned_definition(plist)
    loaded = owned_loaded(managed)
    if loaded and launch("bootout", job_target()).returncode:
        raise ValueError("连接服务卸载失败；未删除配置，请用 connect status 检查。")
    if managed:
        plist.unlink()
    print("连接服务已停用，登录后不会自动启动；账号请求不会改走服务器出口。")


def main(argv=None):
    parser = argparse.ArgumentParser(description="管理浏览器设备的后台网络连接，不执行账号请求。")
    parser.add_argument("action", choices=("install", "converge", "status", "stop", "restart"))
    parser.add_argument("--server")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.json and args.action != "status":
        parser.error("--json is supported by status only")
    try:
        guard = deployment_guard.mutation_guard(ROOT) if args.action in ('install', 'converge', 'stop', 'restart') else contextlib.nullcontext()
        with guard:
            if platform.system() != "Darwin":
                raise ValueError("自动连接服务当前支持macOS；其他平台可运行 agent-monitor connect --server <页面根地址>。")
            if args.action == "install":
                if not args.server:
                    parser.error("install requires --server")
                show_status(install(args.server), args.json)
            elif args.action == "converge":
                plist, _ = paths()
                managed = owned_definition(plist)
                if managed:
                    show_status(install(managed["ProgramArguments"][-3]))
                else:
                    owned_loaded(None)
                    print("连接服务未安装；保留未启用状态。")
            elif args.action == "stop":
                stop()
            elif args.action == "restart":
                plist, _ = paths()
                managed = owned_definition(plist)
                if not managed:
                    raise ValueError("连接服务尚未安装；先运行 connect install --server <页面根地址>。")
                if not owned_loaded(managed):
                    show_status(install(managed["ProgramArguments"][-3]))
                    return 0
                if launch("kickstart", "-k", job_target()).returncode:
                    raise ValueError("连接服务重启失败；请运行 connect install --server <页面根地址> 恢复。")
                print("已请求重启连接服务；旧批次连接失效，不会重发。用 connect status 检查就绪状态。")
            else:
                result = inspect()
                show_status(result, args.json)
                disabled = not result["installed"] and not result["loaded"] and result["observation"] is None
                return 0 if result["connected"] or disabled else 1
            return 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        # Do not print raw subprocess diagnostics, URLs or request exceptions.
        print(str(exc) if isinstance(exc, ValueError) else "连接服务管理失败；请检查本机launchd和安装目录。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
