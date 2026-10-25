"""Observe configuration/network changes; never read or publish proxy secrets."""
import ctypes
import os
from pathlib import Path
import platform
import select
import socket
import threading


class SystemChanges:
    def __init__(self, callback, failed=lambda: None):
        self.callback = callback
        self.failed = failed
        self.stopping = threading.Event()
        self.ready = threading.Event()
        self.error = False
        self.loop = None
        self.cf = None
        self.thread = threading.Thread(target=self._run, name="system-proxy-changes", daemon=True)
        self.thread.start()
        if not self.ready.wait(3) or self.error:
            self.close()
            raise RuntimeError("system proxy change observer unavailable")

    def _run(self):
        try:
            if platform.system() == "Darwin":
                self._mac()
            else:
                self._linux()
        except Exception:
            self.error = True
            self.ready.set()
            self.failed()

    def _mac(self):
        ptr = ctypes.c_void_p
        cf = self.cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        sc = ctypes.CDLL("/System/Library/Frameworks/SystemConfiguration.framework/SystemConfiguration")
        callback_type = ctypes.CFUNCTYPE(None, ptr, ptr, ptr)
        changed = callback_type(lambda *_: self.callback())
        cf.CFStringCreateWithCString.argtypes = [ptr, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFStringCreateWithCString.restype = ptr
        cf.CFArrayCreate.argtypes = [ptr, ctypes.POINTER(ptr), ctypes.c_long, ptr]
        cf.CFArrayCreate.restype = ptr
        cf.CFRelease.argtypes = [ptr]
        cf.CFRunLoopGetCurrent.restype = ptr
        cf.CFRunLoopAddSource.argtypes = [ptr, ptr, ptr]
        cf.CFRunLoopStop.argtypes = [ptr]
        sc.SCDynamicStoreCreate.argtypes = [ptr, ptr, callback_type, ptr]
        sc.SCDynamicStoreCreate.restype = ptr
        sc.SCDynamicStoreSetNotificationKeys.argtypes = [ptr, ptr, ptr]
        sc.SCDynamicStoreSetNotificationKeys.restype = ctypes.c_bool
        sc.SCDynamicStoreCreateRunLoopSource.argtypes = [ptr, ptr, ctypes.c_long]
        sc.SCDynamicStoreCreateRunLoopSource.restype = ptr
        values = []
        def string(value):
            result = cf.CFStringCreateWithCString(None, value, 0x08000100)
            values.append(result)
            return result
        store = source = patterns = None
        try:
            store = sc.SCDynamicStoreCreate(None, string(b"agent-monitor-system-proxy"), changed, None)
            entries = (ptr * 2)(string(b"State:/Network/.*"), string(b"Setup:/Network/.*"))
            patterns = cf.CFArrayCreate(None, entries, 2, None)
            if not store or not sc.SCDynamicStoreSetNotificationKeys(store, None, patterns):
                raise RuntimeError("configuration observer registration failed")
            source = sc.SCDynamicStoreCreateRunLoopSource(None, store, 0)
            if not source:
                raise RuntimeError("configuration observer source failed")
            self.loop = cf.CFRunLoopGetCurrent()
            mode = ptr.in_dll(cf, "kCFRunLoopDefaultMode")
            cf.CFRunLoopAddSource(self.loop, source, mode)
            self.ready.set()
            if not self.stopping.is_set():
                cf.CFRunLoopRun()
        finally:
            self.loop = None
            for value in [source, store, patterns, *values]:
                if value:
                    cf.CFRelease(value)

    def _linux(self):
        # Chromium's Linux system source depends on desktop/session. Watch the
        # persistent GNOME/KDE sources and kernel network events independently
        # of the URL's current candidate list. Environment is a process snapshot.
        home = Path.home()
        config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
        kde = Path(os.environ.get("KDEHOME", home / ".kde"))
        directories = [config, kde / "share/config", home / ".kde4/share/config"]
        dconf = [config / "dconf", Path("/etc/dconf/db"), Path("/etc/dconf/profile")]
        settings = _GnomeChanges(self.callback)
        def fingerprint():
            output = []
            paths = [directory / name for directory in directories for name in ("kioslaverc", "kdeglobals")]
            for directory in dconf:
                if directory.is_dir():
                    paths.extend(directory.rglob("*"))
            profile = os.environ.get("DCONF_PROFILE")
            if profile and profile.startswith("/"):
                paths.append(Path(profile))
            for path in paths:
                try:
                    value = path.stat()
                    output.append((str(path), value.st_ino, value.st_mtime_ns, value.st_size))
                except FileNotFoundError:
                    pass
            return tuple(sorted(output))
        channel = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, socket.NETLINK_ROUTE)
        channel.bind((0, 1 | 0x10 | 0x100 | 0x40 | 0x400))
        channel.setblocking(False)
        previous = fingerprint()
        self.ready.set()
        try:
            while not self.stopping.is_set():
                changed = False
                if select.select([channel], [], [], .5)[0]:
                    channel.recv(65536)
                    changed = True
                current = fingerprint()
                if current != previous:
                    previous = current
                    changed = True
                if changed:
                    self.callback()
                settings.dispatch()
        finally:
            channel.close()
            settings.close()

    def close(self):
        self.stopping.set()
        if self.cf and self.loop:
            self.cf.CFRunLoopStop(self.loop)
        if self.thread is not threading.current_thread():
            self.thread.join(timeout=1)


class _GnomeChanges:
    """Use GSettings notifications, including changes before dconf is flushed."""
    def __init__(self, callback):
        ptr = ctypes.c_void_p
        self.settings = []
        self.gio = ctypes.CDLL("libgio-2.0.so.0")
        self.glib = ctypes.CDLL("libglib-2.0.so.0")
        self.objects = ctypes.CDLL("libgobject-2.0.so.0")
        self.gio.g_settings_schema_source_get_default.restype = ptr
        self.gio.g_settings_schema_source_lookup.argtypes = [ptr, ctypes.c_char_p, ctypes.c_bool]
        self.gio.g_settings_schema_source_lookup.restype = ptr
        self.gio.g_settings_new_full.argtypes = [ptr, ptr, ctypes.c_char_p]
        self.gio.g_settings_new_full.restype = ptr
        self.gio.g_settings_schema_unref.argtypes = [ptr]
        self.objects.g_signal_connect_data.argtypes = [ptr, ctypes.c_char_p, ptr, ptr, ptr, ctypes.c_int]
        self.objects.g_object_unref.argtypes = [ptr]
        self.glib.g_main_context_iteration.argtypes = [ptr, ctypes.c_bool]
        self.glib.g_main_context_iteration.restype = ctypes.c_bool
        # change-event precedes per-key changed signals and does not require
        # reading a setting value (including any stored proxy password).
        def changed(*_):
            callback()
            return False
        self.callback = ctypes.CFUNCTYPE(ctypes.c_bool, ptr, ptr, ctypes.c_int, ptr)(changed)
        source = self.gio.g_settings_schema_source_get_default()
        if source:
            for suffix in ("", ".http", ".https", ".ftp", ".socks"):
                schema = self.gio.g_settings_schema_source_lookup(source, ("org.gnome.system.proxy" + suffix).encode(), True)
                if schema:
                    value = self.gio.g_settings_new_full(schema, None, None)
                    self.gio.g_settings_schema_unref(schema)
                    self.settings.append(value)
                    self.objects.g_signal_connect_data(value, b"change-event", self.callback, None, None, 0)

    def dispatch(self):
        for _ in range(100):
            if not self.glib.g_main_context_iteration(None, False):
                break

    def close(self):
        for value in self.settings:
            self.objects.g_object_unref(value)
        self.settings.clear()
