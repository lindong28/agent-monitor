#!/usr/bin/env python3
"""
ipcheck — 网络环境诊断工具
检测本机 IP、IPv6、DNS、公网信息、代理状态、时区
支持 macOS / Linux / Windows
"""

import socket
import ipaddress
import os
import sys
import subprocess
import concurrent.futures
import datetime
import re
import platform
import math
import threading
from urllib.parse import urlsplit
from email.utils import parsedate_to_datetime

import requests
import network_policy

try:
    from zoneinfo import ZoneInfo as _ZI
except ImportError:
    _ZI = None

# ── 编码修正（Windows cmd 默认非 UTF-8）────────────────────
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ('utf-8', 'utf8'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        pass

IS_WIN = platform.system() == 'Windows'


# ── 已知 DNS ──────────────────────────────────────────────
KNOWN_DNS = {
    '1.1.1.1':         'Cloudflare (US)',
    '1.0.0.1':         'Cloudflare (US)',
    '1.1.1.2':         'Cloudflare for Families (US)',
    '1.0.0.2':         'Cloudflare for Families (US)',
    '1.1.1.3':         'Cloudflare for Families (US)',
    '1.0.0.3':         'Cloudflare for Families (US)',
    '8.8.8.8':         'Google Public DNS (US)',
    '8.8.4.4':         'Google Public DNS (US)',
    '9.9.9.9':         'Quad9 (US)',
    '149.112.112.112': 'Quad9 (US)',
    '208.67.222.222':  'OpenDNS/Cisco (US)',
    '208.67.220.220':  'OpenDNS/Cisco (US)',
    '223.5.5.5':       'AliDNS 阿里 (CN)',
    '223.6.6.6':       'AliDNS 阿里 (CN)',
    '119.29.29.29':    'DNSPod 腾讯 (CN)',
    '182.254.116.116': 'DNSPod 腾讯 (CN)',
    '114.114.114.114': '114DNS (CN)',
    '114.114.115.115': '114DNS (CN)',
    '180.76.76.76':    'BaiduDNS 百度 (CN)',
    '1.2.4.8':         'CNNIC (CN)',
    '210.2.4.8':       'CNNIC (CN)',
    '94.140.14.14':    'AdGuard (CY)',
    '94.140.15.15':    'AdGuard (CY)',
    '185.228.168.9':   'CleanBrowsing (US)',
    '185.228.169.9':   'CleanBrowsing (US)',
    '76.76.2.0':       'Alternate DNS (US)',
    '76.76.10.0':      'Alternate DNS (US)',
}


def dns_label(ip):
    if ip in KNOWN_DNS:
        return f"{ip}  {KNOWN_DNS[ip]}"
    try:
        if ipaddress.ip_address(ip).is_private:
            return f"{ip}  局域网路由器"
    except Exception:
        pass
    return ip


def make_zone(name):
    if not _ZI or not name:
        return None
    try:
        return _ZI(name)
    except Exception:
        return None


def _val(v, fallback="未知"):
    return v if v else warn(fallback)


# ── 颜色 ─────────────────────────────────────────────────
def _init_color():
    if IS_WIN:
        try:
            import colorama
            colorama.init()
            return True
        except ImportError:
            pass
        try:
            import ctypes
            h = ctypes.windll.kernel32.GetStdHandle(-11)
            m = ctypes.c_ulong()
            ctypes.windll.kernel32.GetConsoleMode(h, ctypes.byref(m))
            ctypes.windll.kernel32.SetConsoleMode(h, m.value | 0x0004)
            return True
        except Exception:
            return False
    return True

_COLOR = _init_color()


class C:
    RESET  = "\033[0m"  if _COLOR else ""
    BOLD   = "\033[1m"  if _COLOR else ""
    RED    = "\033[91m" if _COLOR else ""
    GREEN  = "\033[92m" if _COLOR else ""
    YELLOW = "\033[93m" if _COLOR else ""
    GRAY   = "\033[90m" if _COLOR else ""

ANSI_RE = re.compile(r'\033\[[0-9;]*m')


def char_width(c):
    cp = ord(c)
    if (0x2E80 <= cp <= 0x303E or 0x3040 <= cp <= 0x33FF or
        0x3400 <= cp <= 0x4DBF or 0x4E00 <= cp <= 0x9FFF or
        0xAC00 <= cp <= 0xD7AF or 0xF900 <= cp <= 0xFAFF or
        0xFE30 <= cp <= 0xFE6F or 0xFF00 <= cp <= 0xFF60 or
        0x20000 <= cp <= 0x2FFFD):
        return 2
    return 1


def display_len(s):
    return sum(char_width(c) for c in ANSI_RE.sub('', s))


def ok(v):   return f"{C.GREEN}{v}{C.RESET}"
def warn(v): return f"{C.YELLOW}{v}{C.RESET}"
def bad(v):  return f"{C.RED}{v}{C.RESET}"


def risk_color(score):
    if score < 30:
        return C.GREEN, "低风险"
    if score < 70:
        return C.YELLOW, "中风险"
    return C.RED, "高风险"


# ── 表格渲染 ──────────────────────────────────────────────
COL_LABEL, COL_VALUE = 18, 46

def tbl_top(): print(f"  ╔{'═'*(COL_LABEL+2)}╤{'═'*(COL_VALUE+2)}╗")
def tbl_sep(): print(f"  ╠{'═'*(COL_LABEL+2)}╪{'═'*(COL_VALUE+2)}╣")
def tbl_bot(): print(f"  ╚{'═'*(COL_LABEL+2)}╧{'═'*(COL_VALUE+2)}╝")


def tbl_row(label, value):
    value = str(value)
    lpad = ' ' * max(0, COL_LABEL - display_len(label))
    vpad = ' ' * max(0, COL_VALUE - display_len(value))
    lstr = f"{label}{lpad}" if label else ' ' * COL_LABEL
    print(f"  ║ {lstr} │ {value}{vpad} ║")


# ── 数据采集 ─────────────────────────────────────────────
def get_lan_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return warn("获取失败")


def get_ipv6():
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as s:
            s.connect(("2001:4860:4860::8888", 80))
            ip = s.getsockname()[0]
            if ip and ip not in ('', '::'):
                return ip
    except Exception:
        pass
    return None


def get_dns_servers():
    servers = []
    if IS_WIN:
        try:
            r = subprocess.run(
                ['powershell', '-NoProfile', '-Command',
                 'Get-DnsClientServerAddress -AddressFamily IPv4 | '
                 'Select-Object -ExpandProperty ServerAddresses'],
                capture_output=True, text=True, timeout=5, encoding='utf-8',
            )
            seen = set()
            for line in r.stdout.splitlines():
                ip = line.strip()
                if not ip:
                    continue
                try:
                    ipaddress.ip_address(ip)
                    if ip not in seen:
                        seen.add(ip)
                        servers.append(ip)
                except ValueError:
                    pass
        except Exception:
            pass
    else:
        try:
            seen = set()
            with open('/etc/resolv.conf') as f:
                for line in f:
                    if line.strip().startswith('nameserver'):
                        ip = line.split()[1]
                        if ip not in seen:
                            seen.add(ip)
                            servers.append(ip)
        except Exception:
            pass
        if not servers:
            try:
                r = subprocess.run(
                    ['scutil', '--dns'], capture_output=True, text=True, timeout=3,
                )
                seen = set()
                for line in r.stdout.splitlines():
                    line = line.strip()
                    if line.startswith('nameserver['):
                        ip = line.split(':', 1)[1].strip()
                        if ip not in seen:
                            seen.add(ip)
                            servers.append(ip)
            except Exception:
                pass
    return servers


def resolve_route():
    """Describe policy, not a guessed universal endpoint or public IP."""
    return {"address": "", "source": "system", "reason": "按目标URL采用Chromium系统代理解析",
            "path": "", "probe_url": "http://ip-api.com/json/"}


def _egress_session(route=None):
    # The bridge resolves each target, including redirects, from system policy.
    # A previous diagnostic reading must never become a routing override.
    return network_policy.session()


def _require_success(response, on_retry_after=None):
    if 200 <= response.status_code < 300:
        return
    raw = response.headers.get("Retry-After", "")
    delay = None
    if isinstance(raw, str) and len(raw) <= 128:
        value = raw.strip()
        if value.isascii() and value.isdecimal() and len(value) <= 20:
            delay = int(value)
        elif value:
            try:
                date = parsedate_to_datetime(value)
                if date.tzinfo is not None:
                    delay = max(0, math.ceil((date - datetime.datetime.now(datetime.timezone.utc)).total_seconds()))
            except (ValueError, TypeError, OverflowError):
                pass
    message = f"HTTP {response.status_code}，未重试"
    if delay is not None:
        message += f"；服务端 Retry-After：至少 {delay} 秒后再试"
        if on_retry_after is not None:
            on_retry_after(delay)
    elif raw:
        message += "；服务端 Retry-After 无法解析"
    raise ValueError(message)


def get_public_info(route=None, on_retry_after=None):
    try:
        resp = _egress_session(route).get(
            "http://ip-api.com/json/",
            params={"fields": "status,message,country,regionName,city,isp,org,proxy,hosting,query,timezone"},
            timeout=6,
            allow_redirects=False,
        )
        _require_success(resp, on_retry_after)
        return resp.json()
    except Exception as e:
        return {"status": "fail", "message": str(e)}


def get_ip_risk(ip, route=None, on_retry_after=None):
    try:
        resp = _egress_session(route).get(
            f"https://proxycheck.io/v2/{ip}",
            params={"risk": 1, "vpn": 1, "asn": 1},
            timeout=6,
            allow_redirects=False,
        )
        _require_success(resp, on_retry_after)
        data = resp.json().get(ip, {})
        risk  = data.get("risk")
        itype = data.get("type", "")
        proxy = data.get("proxy", "")
        parts = []
        score = None
        if risk is not None:
            score = int(risk)
            color, level = risk_color(score)
            parts.append(f"{color}{score}/100 {level}{C.RESET}")
        if itype:
            parts.append(f"类型 {itype}")
        if proxy == "yes":
            parts.append(bad("已标记为代理"))
        display = "  ".join(parts) if parts else warn("暂无数据")
        return display, score
    except Exception as e:
        return warn(f"查询失败（{e}）"), None


def get_stopforumspam(ip, route=None, on_retry_after=None):
    try:
        resp = _egress_session(route).get(
            "https://api.stopforumspam.org/api",
            params={"json": 1, "ip": ip},
            timeout=(6, 10),
            allow_redirects=False,
        )
        _require_success(resp, on_retry_after)
        payload = resp.json()
        if not isinstance(payload, dict) or payload.get("success") != 1:
            raise ValueError("信誉库未返回成功结果")
        data = payload.get("ip")
        if not isinstance(data, dict) or data.get("appears") not in (0, 1):
            raise ValueError("信誉库未返回明确收录状态")
        if data["appears"] == 0:
            return [ok("未收录  低风险 ✓")]
        confidence = data.get("confidence")
        frequency = data.get("frequency")
        if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
                or not math.isfinite(confidence) or not 0 <= confidence <= 100
                or isinstance(frequency, bool) or not isinstance(frequency, int)
                or frequency < 0):
            raise ValueError("信誉库未返回有效置信度或举报次数")
        last_seen  = (data.get("lastseen") or "")[:10]
        color, level = risk_color(confidence)
        lines = [f"{color}{confidence:.1f}/100 {level}{C.RESET}  举报 {frequency} 次"]
        if last_seen:
            lines.append(f"最近举报 {last_seen}")
        return lines
    except Exception as e:
        return [warn(f"查询失败（{e}）")]


def _collect_risk_probes(ip, route=None, on_retry_after=None):
    """Run the two independent reputation lookups with a bounded fan-out."""
    results = {}
    futures = {}
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            for name, probe in (
                ("risk", get_ip_risk),
                ("spam", get_stopforumspam),
            ):
                try:
                    futures[name] = executor.submit(probe, ip, route, on_retry_after=on_retry_after)
                except Exception as exc:
                    results[name] = (None, exc)
        for name, future in futures.items():
            try:
                results[name] = (future.result(), None)
            except Exception as exc:
                results[name] = (None, exc)
    except Exception as exc:
        for name in ("risk", "spam"):
            results.setdefault(name, (None, exc))
    return results


def get_proxy_envs():
    seen = {}
    internal = os.environ.get(network_policy.INTERNAL)
    for key in ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                "http_proxy", "https_proxy", "all_proxy"]:
        val = os.environ.get(key)
        if not val or val == internal:
            continue
        try:
            parsed = urlsplit(val)
            if not parsed.scheme or not parsed.hostname:
                raise ValueError()
            host = parsed.hostname
            if ':' in host:
                host = '[' + host + ']'
            safe = parsed.scheme + '://' + host + (':' + str(parsed.port) if parsed.port else '')
        except ValueError:
            safe = 'configured (address unavailable)'
        if safe not in seen.values():
            seen[key.upper()] = safe
    return seen


def _dns_entries(addresses):
    entries = []
    for address in addresses:
        label = KNOWN_DNS.get(address, "")
        country = next((code for code in ("CN", "US", "CY") if f"({code})" in label), None)
        entries.append({"ip": address, "label": label or None, "country": country})
    return entries


def _network_assessment(local, public, risk, tz):
    """Missing reputation alone keeps E1c's grade; missing critical inputs do not."""
    missing = []
    if (not public.get("ok") or not public.get("ip") or not isinstance(public.get("proxy"), bool)
            or not isinstance(public.get("hosting"), bool)):
        missing.append("public")
    dns = local.get("dns") or []
    if not dns or any(not entry.get("country") for entry in dns):
        missing.append("dns")
    if (public.get("ok") and (public.get("proxy") or public.get("hosting"))
            and risk.get("score") is None):
        missing.append("risk")
    if not isinstance(tz.get("matched"), bool):
        missing.append("timezone")
    high = (bool(local.get("ipv6")) or local.get("dns_has_cn")
            or (risk.get("score") is not None and risk["score"] >= 70)
            or tz.get("matched") is False)
    if high:
        verdict = "high"
    elif any(key in missing for key in ("public", "dns", "timezone")):
        verdict = "unknown"
    elif public.get("proxy") or risk.get("marked_proxy"):
        verdict = "proxy-in-use"
    else:
        verdict = "low"
    return verdict, {"missing": missing, "limitations": ["ipv6_leak_unverified"]}


def collect_all() -> dict:
    """Collect all data and return as structured dict. Used by --json mode and
    by external consumers like agent-monitor. Mirrors the same get_* functions main()
    uses, but normalizes output to JSON-friendly types (ANSI strings stripped).

    Per-section partial failure: if any get_* call raises, the corresponding
    section is set to None (or {"ok": False}) and an entry is appended to the
    "errors" list, so callers can render partial data gracefully.
    """
    import datetime as _dt
    import re as _re

    try:
        from ip_check import __version__ as _pkg_version
    except ImportError:
        _pkg_version = "0.1.0"

    ansi_strip = _re.compile(r'\033\[[0-9;]*m')

    def _strip(s):
        return ansi_strip.sub('', str(s)) if s is not None else None

    def _risk_level(score):
        if score is None:
            return None
        if score < 30:
            return "low"
        if score < 70:
            return "medium"
        return "high"

    def _parse_risk_type(display):
        if not display:
            return None
        match = _re.search(r"(?:类型|type)\s+([A-Za-z0-9_-]+)", display, _re.IGNORECASE)
        return match.group(1) if match else None

    def _parse_spam_lines(lines):
        raw_lines = [_strip(line) for line in lines]
        spam = {"raw_lines": raw_lines}
        joined = " ".join(line for line in raw_lines if line)

        score_match = _re.search(
            r"([0-9]+(?:\.[0-9]+)?)/100\s+([A-Za-z]+|低风险|中风险|高风险)",
            joined,
            _re.IGNORECASE,
        )
        if score_match:
            spam["score"] = float(score_match.group(1))
            level = score_match.group(2).lower()
            if level == "low" or "低" in level:
                spam["level"] = "low"
            elif level == "medium" or "中" in level:
                spam["level"] = "medium"
            elif level == "high" or "高" in level:
                spam["level"] = "high"

        frequency_match = _re.search(
            r"(?:举报|reports?)\s*([0-9]+)\s*(?:次|times?)?",
            joined,
            _re.IGNORECASE,
        )
        if frequency_match:
            spam["frequency"] = int(frequency_match.group(1))

        last_seen_match = _re.search(
            r"(?:最近举报|last(?:\s+spam)?\s+report(?:ed)?|last\s+seen)\s*([0-9]{4}-[0-9]{2}-[0-9]{2})",
            joined,
            _re.IGNORECASE,
        )
        if last_seen_match:
            spam["last_seen"] = last_seen_match.group(1)

        return spam

    errors = []
    retry_delays = []
    retry_lock = threading.Lock()

    def record_retry_after(delay):
        with retry_lock:
            retry_delays.append(delay)

    out = {
        "version": _pkg_version,
        "timestamp": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "system": {"platform": platform.system(), "python": platform.python_version()},
        "local": None,
        "public": None,
        "risk": None,
        "spam": None,
        "proxy_envs": {},
        "tz_check": None,
        "conclusions": [],
        "verdict": "unknown",
        "errors": errors,
    }

    try:
        lan = get_lan_ip()
        ipv6 = get_ipv6()
        dns_raw = get_dns_servers()
        dns_entries = _dns_entries(dns_raw)
        out["local"] = {
            "lan_ip": _strip(lan),
            "ipv6": ipv6,
            # Deprecated: local address presence, not a public leak observation.
            "ipv6_leaked": ipv6 is not None,
            "dns": dns_entries,
            "dns_has_cn": any(e["country"] == "CN" for e in dns_entries),
        }
    except Exception as e:
        errors.append({"section": "local", "message": str(e)})

    # Shared policy does not mean different destinations share one route/IP.
    route = resolve_route()
    out["proxy_effective"] = dict(route)

    try:
        pub = get_public_info(route, on_retry_after=record_retry_after)
        if pub.get("status") == "success":
            tz_name = pub.get("timezone")
            tz_off = None
            if tz_name:
                zi = make_zone(tz_name)
                if zi:
                    tz_off = _utc_str(_dt.datetime.now(zi).utcoffset())
            out["public"] = {
                "ok": True,
                "probe_url": "http://ip-api.com/json/",
                "ip": pub.get("query"),
                "country": pub.get("country"),
                "region": pub.get("regionName"),
                "city": pub.get("city"),
                "isp": pub.get("isp"),
                "org": pub.get("org"),
                "timezone": tz_name,
                "tz_offset": tz_off,
                "proxy": pub.get("proxy") if isinstance(pub.get("proxy"), bool) else None,
                "hosting": pub.get("hosting") if isinstance(pub.get("hosting"), bool) else None,
            }
        else:
            message = pub.get("message", "unknown")
            out["public"] = {"ok": False, "error": message}
            errors.append({"section": "public", "message": message})
    except Exception as e:
        errors.append({"section": "public", "message": str(e)})

    # Match upstream main() gating: only call proxycheck and stopforumspam when
    # ip-api marks the public IP as proxy or hosting, preserving free quotas.
    pub_data = out.get("public") or {}
    if pub_data.get("ok") and pub_data.get("ip") and (pub_data.get("proxy") or pub_data.get("hosting")):
        ip = pub_data.get("ip")
        probes = _collect_risk_probes(ip, route, on_retry_after=record_retry_after)
        risk_result, risk_error = probes["risk"]
        if risk_error is None:
            risk_display, risk_score = risk_result
            risk_display = _strip(risk_display)
            out["risk"] = {
                "score": risk_score,
                "level": _risk_level(risk_score),
                "type": _parse_risk_type(risk_display),
                "marked_proxy": pub_data.get("proxy", False),
                "display": risk_display,
            }
        else:
            errors.append({"section": "risk", "message": str(risk_error)})
        spam_lines, spam_error = probes["spam"]
        if spam_error is None:
            out["spam"] = _parse_spam_lines(spam_lines)
        else:
            errors.append({"section": "spam", "message": str(spam_error)})

    if retry_delays:
        # Conservative whole-round wait: never shorten any endpoint's instruction.
        out["retry_after_seconds"] = max(retry_delays)

    try:
        out["proxy_envs"] = get_proxy_envs()
    except Exception as e:
        errors.append({"section": "proxy_envs", "message": str(e)})

    try:
        cli_dt = _dt.datetime.now().astimezone()
        cli_offset = cli_dt.utcoffset()
        tz_name, is_iana = get_cli_tz_name()
        matched = None
        match_label = None
        if pub_data.get("ok") and pub_data.get("timezone"):
            pub_zi = make_zone(pub_data["timezone"])
            pub_offset = _dt.datetime.now(pub_zi).utcoffset() if pub_zi else None
            if is_iana:
                matched = (tz_name == pub_data["timezone"])
                match_label = "一致" if matched else "不一致"
            elif pub_offset is not None:
                matched = (cli_offset == pub_offset)
                match_label = "UTC 偏移一致" if matched else "不一致"
        out["tz_check"] = {
            "cli_tz": tz_name,
            "cli_offset": _utc_str(cli_offset),
            "matched": matched,
            "match_label": match_label,
        }
    except Exception as e:
        errors.append({"section": "tz_check", "message": str(e)})

    conclusions = []
    local = out.get("local") or {}
    risk = out.get("risk") or {}
    tz = out.get("tz_check") or {}
    out["verdict"], out["coverage"] = _network_assessment(local, pub_data, risk, tz)
    proxy_detected = bool(pub_data.get("proxy") or risk.get("marked_proxy"))

    if local.get("ipv6_leaked"):
        conclusions.append({"level": "bad", "text": "检出本地 IPv6 地址；公网泄漏未核实"})
    elif local:
        conclusions.append({"level": "warn", "text": "未检出本地 IPv6 地址；公网泄漏未核实"})
    if local.get("dns_has_cn"):
        conclusions.append({"level": "bad", "text": "检出已知国内 DNS 解析器"})
    elif "dns" in out["coverage"]["missing"]:
        conclusions.append({"level": "warn", "text": "DNS 解析器地域未知或列表缺失，无法完整判断"})
    else:
        conclusions.append({"level": "ok", "text": "未命中已知国内 DNS 解析器"})
    if pub_data.get("ok"):
        score = risk.get("score")
        if score is not None:
            if score < 30:
                conclusions.append({"level": "ok", "text": f"IP 风险低（{score}/100）"})
            elif score < 70:
                conclusions.append({"level": "warn", "text": f"IP 风险中等（{score}/100）"})
            else:
                conclusions.append({"level": "bad", "text": f"IP 风险高（{score}/100）"})
        elif pub_data.get("proxy") is False and pub_data.get("hosting") is False:
            conclusions.append({"level": "ok", "text": "IP 无代理或机房标记"})
        if proxy_detected and out["verdict"] != "high":
            conclusions.append({
                "level": "warn",
                "text": "IP 被标记为代理/VPN——走隧道时属正常，但 Claude 的反爬可能误判",
            })
    if tz.get("matched") is True:
        conclusions.append({"level": "ok", "text": "时区一致"})
    elif tz.get("matched") is False:
        conclusions.append({"level": "bad", "text": "时区不一致"})
    out["conclusions"] = conclusions
    return out


def _utc_str(offset):
    total = int(offset.total_seconds())
    h, r  = divmod(abs(total), 3600)
    sign  = "+" if total >= 0 else "-"
    return f"UTC{sign}{h:02d}:{r//60:02d}"


def get_cli_tz_name():
    tz_env = os.environ.get('TZ', '')
    if tz_env:
        return tz_env, True

    if IS_WIN:
        try:
            r = subprocess.run(
                ['powershell', '-NoProfile', '-Command',
                 '[System.TimeZoneInfo]::Local.Id'],
                capture_output=True, text=True, timeout=3, encoding='utf-8',
            )
            win_id = r.stdout.strip()
            if win_id:
                return win_id, False
        except Exception:
            pass

    name = datetime.datetime.now().astimezone().tzname() or "Unknown"
    return name, False


# ── 主程序 ────────────────────────────────────────────────
def main():
    if len(sys.argv) > 1 and sys.argv[1] in ('--version', '-v', '-V'):
        from ipcheck import __version__
        print(f"ipcheck {__version__}")
        return

    if "--json" in sys.argv:
        import json as _json
        print(_json.dumps(collect_all(), ensure_ascii=False, indent=2, default=str))
        return

    # Each request resolves its destination through the same product policy.
    route = resolve_route()
    pub = get_public_info(route)
    pub_ok = pub.get("status") == "success"

    print(f"\n  {C.BOLD}ipcheck — 网络环境诊断工具{C.RESET}  "
          f"{C.GRAY}({platform.system()} / Python {platform.python_version()}){C.RESET}\n")
    tbl_top()

    # 本机网络
    tbl_row("局域网 IP", get_lan_ip())
    ipv6_addr = get_ipv6()
    ipv6_leaked = ipv6_addr is not None
    tbl_row("IPv6 地址", ipv6_addr if ipv6_leaked else warn("未检出地址"))
    dns = get_dns_servers()
    if dns:
        tbl_row("DNS 服务器", dns_label(dns[0]))
        for d in dns[1:]:
            tbl_row("", dns_label(d))
    else:
        tbl_row("DNS 服务器", warn("获取失败"))
    dns_entries = _dns_entries(dns)
    dns_cn = any(entry["country"] == "CN" for entry in dns_entries)

    tbl_sep()

    # 公网信息
    if pub_ok:
        pub_ip = pub.get("query")
        tbl_row("公网 IP",          pub_ip or bad("获取失败"))
        tbl_row("国家 / 省份",      f"{_val(pub.get('country'))} / {_val(pub.get('regionName'))}")
        tbl_row("城市",              _val(pub.get("city")))
        tbl_row("ISP(互联网服务商)", _val(pub.get("isp")))
        tbl_row("组织",              _val(pub.get("org")))
        pub_tz_name = pub.get("timezone")
        if pub_tz_name:
            zi = make_zone(pub_tz_name)
            if zi:
                off = datetime.datetime.now(zi).utcoffset()
                tbl_row("所处时区", f"{pub_tz_name}  ({_utc_str(off)})")
            else:
                tbl_row("所处时区", pub_tz_name)
        else:
            tbl_row("所处时区", _val(None))
    else:
        tbl_row("公网请求", bad(pub.get("message") or "未知错误"))

    tbl_sep()

    # 代理检测
    risk_score = None
    proxy_envs = get_proxy_envs()
    if proxy_envs:
        for k, v in proxy_envs.items():
            tbl_row(k, warn(v))
    else:
        tbl_row("环境变量代理", ok("未设置"))
    if pub_ok:
        for label, key in (("IP 标记为代理", "proxy"), ("机房 / 托管", "hosting")):
            flag = pub.get(key)
            tbl_row(label, bad("是 ✗") if flag is True else ok("否 ✓") if flag is False else warn("未知"))
        if (pub.get("hosting") or pub.get("proxy")) and pub_ip:
            probes = _collect_risk_probes(pub_ip, route)
            risk_result, risk_error = probes["risk"]
            if risk_error is None:
                risk_display, risk_score = risk_result
            else:
                risk_display = warn(f"查询失败（{risk_error}）")
            tbl_row("IP 风险查询",  risk_display)
            spam_lines, spam_error = probes["spam"]
            if spam_error is not None:
                spam_lines = [warn(f"查询失败（{spam_error}）")]
            tbl_row("垃圾滥用记录", spam_lines[0])
            for line in spam_lines[1:]:
                tbl_row("", line)

    tbl_sep()

    # 时区
    tz_matched = None
    cli_dt     = datetime.datetime.now().astimezone()
    cli_offset = cli_dt.utcoffset()
    tz_name, is_iana = get_cli_tz_name()
    tbl_row("CLI 时区", f"{tz_name}  ({_utc_str(cli_offset)})")

    pub_tz_name = pub.get("timezone") if pub_ok else None
    if pub_tz_name:
        pub_zi     = make_zone(pub_tz_name)
        pub_offset = datetime.datetime.now(pub_zi).utcoffset() if pub_zi else None

        if is_iana:
            tz_matched = tz_name == pub_tz_name
            match = ok("一致 ✓") if tz_matched else bad("不一致 ✗")
        elif pub_offset is not None:
            tz_matched = cli_offset == pub_offset
            if tz_matched:
                match = warn("UTC 偏移一致（建议设置 $TZ=IANA 名称精确比对）")
            else:
                match = bad("不一致 ✗（UTC 偏移不同）")
        else:
            match = warn("无法比对（tzdata 未安装？pip install tzdata）")
        tbl_row("时区一致性", match)

    tbl_sep()
    conclusions = []
    if ipv6_leaked:
        conclusions.append(bad("✗ 检出本地 IPv6 地址；公网泄漏未核实"))
    else:
        conclusions.append(warn("- 未检出本地 IPv6 地址；公网泄漏未核实"))
    if dns_cn:
        conclusions.append(bad("✗ 检出已知国内 DNS 解析器"))
    elif not dns:
        conclusions.append(warn("- DNS 获取失败，无法评估"))
    elif any(not entry["country"] for entry in dns_entries):
        conclusions.append(warn("- 部分 DNS 解析器地域未知，无法完整判断"))
    else:
        conclusions.append(ok("✓ 未命中已知国内 DNS 解析器"))
    if not pub_ok:
        conclusions.append(warn("- IP 信息获取失败，无法评估风险"))
    elif pub.get("proxy") or pub.get("hosting"):
        if risk_score is not None:
            if risk_score < 30:
                conclusions.append(ok(f"✓ IP 风险低（{risk_score}/100）"))
            elif risk_score < 70:
                conclusions.append(warn(f"! IP 风险中等（{risk_score}/100），建议关注"))
            else:
                conclusions.append(bad(f"✗ IP 风险高（{risk_score}/100），建议更换节点"))
        else:
            conclusions.append(warn("! IP 为机房/代理，未查到风险分数"))
    elif pub.get("proxy") is False and pub.get("hosting") is False:
        conclusions.append(ok("✓ IP 无代理或机房标记"))
    else:
        conclusions.append(warn("- 公网代理或机房标记未知"))
    if tz_matched is True:
        conclusions.append(ok("✓ 时区一致"))
    elif tz_matched is False:
        conclusions.append(bad("✗ 时区不一致，建议调整"))
    else:
        conclusions.append(warn("- 时区无法比对"))
    verdict, coverage = _network_assessment(
        {"ipv6": ipv6_addr, "dns": dns_entries, "dns_has_cn": dns_cn},
        {"ok": pub_ok, "ip": pub.get("query"), "proxy": pub.get("proxy"), "hosting": pub.get("hosting")},
        {"score": risk_score}, {"matched": tz_matched},
    )
    tbl_row("结论分析", conclusions[0])
    for c in conclusions[1:]:
        tbl_row("", c)
    tbl_sep()
    if verdict == "high":
        tbl_row("综合结论", bad("⚠ 已观测信号命中启发式高风险"))
    elif verdict == "unknown":
        tbl_row("综合结论", warn("观测不足，网络状态未知"))
    elif verdict == "proxy-in-use":
        tbl_row("综合结论", warn("检出代理；服务可用性未验证"))
    else:
        tbl_row("综合结论", ok("已观测信号低风险；服务可用性未验证"))
    if coverage["missing"]:
        labels = {"public": "公网信息", "dns": "DNS 归属", "risk": "风险分", "timezone": "时区"}
        tbl_row("观测缺项", warn("、".join(labels[key] for key in coverage["missing"])))

    tbl_bot()

    if IS_WIN and _ZI is None:
        print(f"\n  {C.YELLOW}提示：pip install tzdata  （Windows 时区精确比对所需）{C.RESET}")
    if IS_WIN and not _COLOR:
        print(f"\n  提示：pip install colorama  （启用彩色输出）")
    print()
