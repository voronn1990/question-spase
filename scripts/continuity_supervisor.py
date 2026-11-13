"""Native Windows continuity witness for a trusted, independently hosted supervisor.

The host owns this object across installer process termination and serves fresh
observations over its authenticated local authority channel. Restarting the host
creates a new witness session. Native notification qualification is pending.
"""

import ctypes as c
import hashlib
import json
from ctypes import wintypes as w
import os
import re
import subprocess
import secrets
import sys
import threading
import platform
from pathlib import Path


class ContinuityUnknown(RuntimeError):
    pass


class FilterData(c.Union):
    _fields_ = [("target", w.HANDLE), ("instance", w.WCHAR * 200)]


class NotificationFilter(c.Structure):
    _fields_ = [("size", w.DWORD), ("flags", w.DWORD), ("filter_type", w.DWORD),
                ("reserved", w.DWORD), ("data", FilterData)]


class VolumeSupervisor:
    def __init__(self, target, expected_guid, powershell, powershell_sha256):
        """Observe the fixed C: system volume for a supervised interruption session.

        Native events and identities veto observed changes. They do not establish
        that no externally forced rebinding or remount occurred.
        """
        if os.name != "nt":
            raise ContinuityUnknown("native Windows supervisor required")
        self._kernel = c.WinDLL("kernel32", use_last_error=True)
        self._configuration = c.WinDLL("cfgmgr32")
        self._powershell = Path(powershell)
        self._powershell_sha256 = powershell_sha256
        if not self._powershell.is_absolute() or not re.fullmatch(r"[a-f0-9]{64}", powershell_sha256):
            raise ContinuityUnknown("invalid supervisor PowerShell pin")
        self._target = target
        self._expected_guid = expected_guid
        self._gap = threading.Event()
        self._lock = threading.RLock()
        self._session = secrets.token_hex(32)
        self._handle = None
        self._registration = w.HANDLE()
        self._bind()
        try:
            initial = self._observe()
            self._handle = self._open(expected_guid, 0, 7, None, 3, 0, None)
            if self._handle in (None, c.c_void_p(-1).value):
                self._handle = None
                raise ContinuityUnknown("zero-access volume handle unavailable")
            event_filter = NotificationFilter()
            event_filter.size = c.sizeof(event_filter)
            event_filter.filter_type = 1  # CM_NOTIFY_FILTER_TYPE_DEVICEHANDLE
            event_filter.data.target = self._handle
            result = self._register(c.byref(event_filter), None, self._callback,
                                    c.byref(self._registration))
            if result or not self._registration:
                raise ContinuityUnknown(f"volume-handle notification unavailable: {result}")
            if self._gap.is_set() or self._observe() != initial:
                raise ContinuityUnknown("volume changed during witness enrollment")
            self._initial = initial
            self._initial_device = self._device_identity()
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _api(dll, name, result, *parameters):
        api = getattr(dll, name)
        api.restype, api.argtypes = result, parameters
        return api

    def _bind(self):
        bind, kernel, configuration = self._api, self._kernel, self._configuration
        callback = c.WINFUNCTYPE(w.DWORD, w.HANDLE, c.c_void_p, c.c_int, c.c_void_p, w.DWORD)
        self._callback = callback(self._event)
        self._register = bind(configuration, "CM_Register_Notification", w.DWORD,
                              c.POINTER(NotificationFilter), c.c_void_p, callback,
                              c.POINTER(w.HANDLE))
        self._unregister = bind(configuration, "CM_Unregister_Notification", w.DWORD, w.HANDLE)
        self._open = bind(kernel, "CreateFileW", w.HANDLE, w.LPCWSTR, w.DWORD, w.DWORD,
                          c.c_void_p, w.DWORD, w.DWORD, w.HANDLE)
        self._close = bind(kernel, "CloseHandle", w.BOOL, w.HANDLE)
        self._volume_root = bind(kernel, "GetVolumePathNameW", w.BOOL,
                                 w.LPCWSTR, w.LPWSTR, w.DWORD)
        self._volume_name = bind(kernel, "GetVolumeNameForVolumeMountPointW", w.BOOL,
                                 w.LPCWSTR, w.LPWSTR, w.DWORD)
        self._system_directory = bind(kernel, "GetSystemWindowsDirectoryW", w.UINT, w.LPWSTR, w.UINT)
        self._drive_type = bind(kernel, "GetDriveTypeW", w.UINT, w.LPCWSTR)
        self._volume_info = bind(kernel, "GetVolumeInformationW", w.BOOL,
                                 w.LPCWSTR, w.LPWSTR, w.DWORD, c.POINTER(w.DWORD),
                                 c.POINTER(w.DWORD), c.POINTER(w.DWORD), w.LPWSTR, w.DWORD)
        self._device_io = bind(kernel, "DeviceIoControl", w.BOOL, w.HANDLE, w.DWORD,
                               c.c_void_p, w.DWORD, c.c_void_p, w.DWORD,
                               c.POINTER(w.DWORD), c.c_void_p)

    def _event(self, notification, context, action, data, size):
        # Any lifecycle notification, including a removal request, voids this session.
        self._gap.set()
        return 0

    def _boot_identity(self):
        raw = self._powershell.read_bytes()
        if hashlib.sha256(raw).hexdigest() != self._powershell_sha256:
            raise ContinuityUnknown("supervisor PowerShell pin differs")
        command = ("$ErrorActionPreference='Stop'; "
                   "$os=Get-CimInstance -ClassName Win32_OperatingSystem -Property LastBootUpTime; "
                   "[Console]::WriteLine($os.LastBootUpTime.ToUniversalTime().Ticks)")
        result = subprocess.run([str(self._powershell), "-NoProfile", "-NonInteractive",
                                 "-Command", command], capture_output=True, check=True, timeout=10)
        value = result.stdout.strip()
        if result.stderr or not re.fullmatch(rb"[0-9]{15,20}", value):
            raise ContinuityUnknown("independent Windows boot observation unavailable")
        return value.decode("ascii")

    def _observe(self):
        boot = self._boot_identity()
        windows, root, guid = (c.create_unicode_buffer(512) for _ in range(3))
        length = self._system_directory(windows, len(windows))
        system_root = "C:" + chr(92)
        if not 0 < length < len(windows) or not windows.value.upper().startswith(system_root) or (
                self._drive_type(system_root) != 3):  # DRIVE_FIXED
            raise ContinuityUnknown("fixed Windows C: system drive required")
        if not self._volume_root(self._target, root, len(root)) or root.value.upper() != system_root or (
                not self._volume_name(system_root, guid, len(guid))):
            raise ContinuityUnknown("target is outside the Windows system volume")
        native = guid.value.rstrip(chr(92))
        if native.casefold() != self._expected_guid.casefold():
            raise ContinuityUnknown("native system volume identity changed")
        serial = w.DWORD()
        if not self._volume_info(system_root, None, 0, c.byref(serial), None, None, None, 0):
            raise ContinuityUnknown("system volume serial unavailable")
        return boot, native.casefold(), serial.value

    def _device_identity(self):
        data = (w.DWORD * 3)()
        size = w.DWORD()
        # IOCTL_STORAGE_GET_DEVICE_NUMBER uses FILE_ANY_ACCESS.
        if not self._device_io(self._handle, 0x002D1080, None, 0, c.byref(data), c.sizeof(data),
                               c.byref(size), None) or size.value != c.sizeof(data):
            raise ContinuityUnknown("retained native volume handle unavailable")
        return tuple(data)

    def observe(self):
        with self._lock:
            try:
                if self._gap.is_set() or self._observe() != self._initial or self._handle is None:
                    raise ContinuityUnknown("witness lost")
                if self._device_identity() != self._initial_device:
                    raise ContinuityUnknown("retained device identity changed")
                if self._gap.is_set():
                    raise ContinuityUnknown("volume lifecycle event")
                return dict(boot_id=self._initial[0], volume_guid=self._initial[1],
                            volume_serial=self._initial[2], session_id=self._session)
            except Exception:
                self._gap.set()
                raise

    def close(self):
        with self._lock:
            self._gap.set()
            if self._registration:
                if self._unregister(self._registration):
                    # Keep callback and handle live if native unregister failed.
                    raise ContinuityUnknown("volume notification unregister failed")
                self._registration = w.HANDLE()
            if self._handle is not None:
                if not self._close(self._handle):
                    raise ContinuityUnknown("volume handle close failed")
                self._handle = None


def main():
    """Run on inherited private stdio held by the independent coordinator."""
    source, sink = sys.stdin.buffer, sys.stdout.buffer

    def receive():
        raw = source.readline(8193)
        if not raw or len(raw) > 8192 or not raw.endswith(b"\n"):
            raise EOFError("supervisor channel unavailable")
        return json.loads(raw)

    def send(record):
        sink.write(json.dumps(record, separators=(",", ":")).encode("utf-8") + b"\n")
        sink.flush()

    setup = receive()
    required = {"target", "volume", "powershell", "powershell_sha256", "challenge",
                "interpreter_sha256", "interpreter_version", "entry_sha256"}
    if (set(setup) != required or sys.flags.isolated != 1 or not sys.flags.dont_write_bytecode
            or platform.python_version() != setup["interpreter_version"]
            or hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest() != setup["interpreter_sha256"]
            or hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != setup["entry_sha256"]):
        raise ContinuityUnknown("native supervisor executable binding differs")
    supervisor = VolumeSupervisor(setup["target"], setup["volume"], setup["powershell"],
                                  setup["powershell_sha256"])
    try:
        request = {"observe": setup["challenge"]}
        while True:
            if request == {"close": setup["challenge"]}:
                break
            if request != {"observe": setup["challenge"]}:
                raise ContinuityUnknown("unexpected supervisor channel request")
            send({"challenge": setup["challenge"], "witness": supervisor.observe()})
            request = receive()
    finally:
        supervisor.close()


if __name__ == "__main__":
    main()
