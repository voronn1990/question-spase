"""Standalone generation switch for a protected, hash-pinned runtime."""

import argparse
import base64
import ctypes
import hashlib
import json
import os
import platform
import re
import socket
import stat
import subprocess
import sys
import tempfile
import unicodedata
from contextlib import contextmanager, nullcontext
from pathlib import Path

REQUEST = "question-space-generation-switch-v3"
POINTER = "question-space-generation-pointer-v3"
WINDOWS = "WINDOWS-DOTNET-DIRECTORY-MOVE-PROCESS-RECOVERY-V2"
WINDOWS_SCOPE = "WINDOWS_SYSTEM_VOLUME_SUPERVISED_PROCESS_INTERRUPTION_V2"
_SYSTEM_POWERSHELL = Path("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
FIXTURE_SCOPE = "DISPOSABLE_FIXTURE_PROCESS_INTERRUPTION_V1"
FIXTURE = "LINUX-DISPOSABLE-NOREPLACE-V1"
ABSENT = "ABSENT"
QUALIFICATION_CUTS = {
    "before_O_TO_ABSENT", "after_O_TO_ABSENT", "journaled_O_TO_ABSENT",
    "before_N_TO_HOLDING", "after_N_TO_HOLDING", "journaled_N_TO_HOLDING",
    "before_ABSENT_TO_N", "after_ABSENT_TO_N", "journaled_ABSENT_TO_N",
    "before_shared_close", "after_shared_close",
    "before_CAS", "after_CAS", "after_receipt", "after_failed_discovery",
    "before_fence_release", "after_provider_effect_before_ack_O_TO_ABSENT",
    "after_provider_effect_before_ack_N_TO_HOLDING", "after_provider_effect_before_ack_ABSENT_TO_N",
}
COMMON = set("""
request_version operation_id logical_occurrence_id operation_fingerprint action
expected_generation expected_pointer_sha256 target_generation scope profile
provider recovery_scope continuity runtime active prior holding pointer journal fence occurrence_lock run_lock
""".split())


class Refused(RuntimeError):
    pass


def require(condition, reason):
    if not condition:
        raise Refused(reason)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def decode(data):
    return json.loads(data.decode("utf-8"), object_pairs_hook=unique,
                      parse_constant=lambda _: require(False, "invalid JSON constant"))


def fields(record, expected):
    require(type(record) is dict and set(record) == set(expected), "unexpected fields")


def token(value):
    require(type(value) is str and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value),
            "invalid identifier")


def occurrence_id(value):
    # Match qs.ident for logical occurrence IDs. Other switch tokens name files.
    require(type(value) is str and re.fullmatch(r"[A-Za-z0-9._-]+", value),
            "invalid logical occurrence id")
    return value


def sha(value):
    require(type(value) is str and re.fullmatch(r"[a-f0-9]{64}", value), "invalid digest")


def continuity_binding(value):
    fields(value, {"boot_id", "volume_guid", "volume_serial", "session_id"})
    require(type(value["boot_id"]) is str and bool(value["boot_id"]), "invalid boot identity")
    require(type(value["volume_guid"]) is str and bool(value["volume_guid"]), "invalid volume identity")
    require(type(value["volume_serial"]) is int and 0 <= value["volume_serial"] <= 0xffffffff,
            "invalid volume serial")
    token(value["session_id"])


def fingerprint(request):
    return digest(encoded({k: v for k, v in request.items() if k != "operation_fingerprint"}))


def node(path):
    try:
        result = path.lstat()
    except FileNotFoundError:
        return None
    require(not stat.S_ISLNK(result.st_mode) and
            not getattr(result, "st_file_attributes", 0) & 0x400, "link or reparse point")
    return result


def canonical_path(value):
    require(type(value) is str, "invalid path")
    path = Path(value)
    require(path.is_absolute() and str(path) == value and ".." not in path.parts,
            "noncanonical path")
    return path


def path_identity(value):
    """Compare the supported C: and WSL projections in the Windows namespace."""
    spelling = str(value).replace("\\", "/")
    require(not spelling.startswith("//"), "unsupported switch writer namespace")
    parts = tuple(part for part in spelling.split("/") if part not in {"", "."})
    require(".." not in parts, "ambiguous switch path")
    if parts and parts[0].casefold() == "c:":
        require(re.match(r"^[Cc]:/", spelling), "drive-relative switch writer path")
        parts = ("mnt", "c", *parts[1:])
    elif re.match(r"^[a-z]:[^/]", spelling, re.IGNORECASE):
        raise Refused("drive-relative switch writer path")
    if tuple(part.casefold() for part in parts[:2]) == ("mnt", "c"):
        for part in parts[2:]:
            require(part.rstrip(" .") == part and not re.search(r'[\x00-\x1f<>:"|?*]', part),
                    "ambiguous Windows switch writer path")
        parts = tuple(part.upper().casefold() for part in parts)
    return parts


def contains_path(parent, child):
    return child[:len(parent)] == parent


def check_path_separation(physical, protected):
    physical_ids = [path_identity(path) for path in physical]
    protected_ids = [path_identity(path) for path in protected]
    require(len(set(physical_ids + protected_ids)) == len(physical_ids + protected_ids),
            "path alias or containment")
    for index, path in enumerate(physical_ids):
        require(not any(contains_path(path, other) or contains_path(other, path)
                        for other in [*physical_ids[index + 1:], *protected_ids]),
                "path alias or containment")


def path_at(value):
    path = canonical_path(value)
    for parent in reversed(path.parents):
        info = node(parent)
        require(info is not None and stat.S_ISDIR(info.st_mode), "invalid path ancestor")
    node(path)
    return path


def read(path):
    path = path_at(str(path))
    info = node(path)
    require(info is not None and stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
            "expected unaliased regular file")
    return _read_opened_file(path, info)


def _read_opened_file(path, info):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as handle:
        opened = os.fstat(handle.fileno())
        require(stat.S_ISREG(opened.st_mode) and os.path.samestat(info, opened),
                "file identity changed")
        return handle.read()


def _verify_windows_provider(executable, expected_sha, reason):
    sha(expected_sha)
    path = path_at(executable)
    if path == _SYSTEM_POWERSHELL:
        # Windows servicing may hard-link its System32 PowerShell executable.
        info = node(path)
        require(info is not None and stat.S_ISREG(info.st_mode) and info.st_nlink >= 1,
                "expected regular PowerShell provider")
        raw = _read_opened_file(path, info)
    else:
        raw = read(path)
    require(digest(raw) == expected_sha, reason)


def tree(path):
    path = path_at(str(path))
    root = node(path)
    if root is None:
        return None
    require(stat.S_ISDIR(root.st_mode), "expected directory")
    entries = []
    aliases = set()

    def visit(directory):
        for child in sorted(directory.iterdir()):
            rel = child.relative_to(path).as_posix()
            alias = unicodedata.normalize("NFC", rel).casefold()
            require(alias not in aliases and ":" not in child.name and
                    "\\" not in child.name and child.name.rstrip(". ") == child.name,
                    "unsafe manifest member")
            aliases.add(alias)
            info = node(child)
            require(info is not None and info.st_dev == root.st_dev, "tree mount or drift")
            if stat.S_ISDIR(info.st_mode):
                entries.append([rel, "directory"])
                visit(child)
            else:
                data = read(child)
                entries.append([rel, "file", len(data), digest(data)])

    visit(path)
    require(os.path.samestat(root, node(path)), "directory identity changed")
    return {"identity": [root.st_dev, root.st_ino], "manifest_sha256": digest(encoded(entries))}


def binding(value, *, check_paths=True):
    fields(value, {"path", "identity", "manifest_sha256"})
    (path_at if check_paths else canonical_path)(value["path"])
    sha(value["manifest_sha256"])
    require(type(value["identity"]) is list and len(value["identity"]) == 2 and
            all(type(i) is int and i > 0 for i in value["identity"]), "invalid tree identity")


def matches(path, bound):
    expected = {k: bound[k] for k in ("identity", "manifest_sha256")} if bound else None
    return tree(path) == expected


def validate(request, *, check_paths=True):
    require(type(request) is dict and request.get("request_version") == REQUEST,
            "unsupported request version")
    action = request.get("action")
    if action == "reconcile":
        fields(request, {"request_version", "action", "operation_id",
                         "operation_fingerprint", "original"})
        original = request["original"]
        require(type(original) is dict and original.get("action") in {"activate", "restore"},
                "invalid original action")
        validate(original, check_paths=check_paths)
        require(all(request[k] == original[k] for k in
                    ("operation_id", "operation_fingerprint")), "reconcile binding differs")
        return original
    require(action in {"activate", "restore"}, "invalid action")
    target = request.get("target")
    require(type(target) is dict, "invalid target")
    tag = target.get("tag")
    if tag == "TARGET_TREE":
        fields(target, {"tag", "staged"})
        binding(target["staged"], check_paths=check_paths)
    else:
        require(action == "restore" and tag == "VERIFIED_ABSENT", "invalid target tag")
        fields(target, {"tag", "first_operation_id", "first_receipt_sha256", "prior_absence"})
        token(target["first_operation_id"])
        sha(target["first_receipt_sha256"])
        sha(target["prior_absence"])
    needs_backup = tag == "TARGET_TREE" and request.get("prior") is not None
    fields(request, COMMON | {"target"} | ({"backup"} if needs_backup else set()))
    for key in ("operation_id", "scope", "profile"):
        token(request[key])
    occurrence_id(request["logical_occurrence_id"])
    profiles = {WINDOWS: WINDOWS_SCOPE, FIXTURE: FIXTURE_SCOPE}
    require(type(request["provider"]) is str and request["provider"] in profiles and
            request["recovery_scope"] == profiles[request["provider"]], "mixed provider/scope")
    continuity_binding(request["continuity"])
    sha(request["runtime"])
    sha(request["operation_fingerprint"])
    require(fingerprint(request) == request["operation_fingerprint"], "fingerprint mismatch")
    old, new = request["expected_generation"], request["target_generation"]
    require(type(old) is int and type(new) is int and old >= 0 and new > old,
            "nonmonotonic generation")
    if request["expected_pointer_sha256"] != ABSENT:
        sha(request["expected_pointer_sha256"])
    if request["prior"] is not None:
        binding(request["prior"], check_paths=check_paths)
        require(request["prior"]["path"] == request["active"], "prior is outside active")
    require(action != "restore" or request["prior"] is not None, "restore needs installed N")
    if needs_backup:
        binding(request["backup"], check_paths=check_paths)
        require(request["backup"]["manifest_sha256"] == request["prior"]["manifest_sha256"],
                "backup differs from active tree")
        require(request["backup"]["identity"] != request["prior"]["identity"],
                "backup aliases active tree")
    for key in ("active", "holding", "pointer", "journal", "fence",
                "occurrence_lock", "run_lock"):
        (path_at if check_paths else canonical_path)(request[key])
    return request


def flush_dir(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def durable(path, data, *, replace=False):
    descriptor, temp = tempfile.mkstemp(prefix=".switch-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if replace:
            os.replace(temp, path)
        else:
            no_replace(temp, path)
        flush_dir(path.parent)
    finally:
        if os.path.lexists(temp):
            os.unlink(temp)


def record(path):
    return read(path) if node(path) is not None else None


@contextmanager
def locked(path):
    import fcntl

    # Shared authority provisions stable lock files. Never create or replace them here.
    info = node(path)
    require(info is not None and stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
            "invalid lock")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        require(os.path.samestat(info, os.fstat(handle.fileno())) and
                os.path.samestat(info, node(path)), "lock identity changed")
        yield


@contextmanager
def fixture_cut(paths):
    with locked(paths["occurrence_lock"]), locked(paths["run_lock"]):
        yield


POWERSHELL_PROVIDER = r"""
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$r = ConvertFrom-Json ([Console]::In.ReadToEnd())
$step = 'initialize'
try {
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;

public static class SwitchWin32 {
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool GetVolumeNameForVolumeMountPoint(string root, StringBuilder name, uint size);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern uint GetFileAttributes(string path);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern SafeFileHandle CreateFile(string path, uint access, uint share, IntPtr security,
                                            uint disposition, uint flags, IntPtr template);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool FlushFileBuffers(SafeFileHandle handle);

    public static string Volume(string path) {
        var name = new StringBuilder(128);
        string windows = Environment.SystemDirectory;
        string system = Path.GetPathRoot(windows);
        if (!String.Equals(system, @"C:\", StringComparison.OrdinalIgnoreCase) ||
            new DriveInfo(system).DriveType != DriveType.Fixed ||
            !String.Equals(system, Path.GetPathRoot(path), StringComparison.OrdinalIgnoreCase))
            throw new IOException("fixed Windows C: system volume required");
        if (!GetVolumeNameForVolumeMountPoint(system, name, (uint)name.Capacity))
            throw new Win32Exception(Marshal.GetLastWin32Error());
        return name.ToString().TrimEnd('\\');
    }

    public static string Inspect(string path) {
        if (!String.Equals(path, Path.GetFullPath(path), StringComparison.Ordinal))
            throw new ArgumentException("noncanonical path");
        string leaf = path;
        string result = null;
        while (path != null) {
            uint attributes = GetFileAttributes(path);
            if (attributes == UInt32.MaxValue) {
                int error = Marshal.GetLastWin32Error();
                if (path == leaf && error == 2)
                    result = "absent";
                else
                    throw new Win32Exception(error);
            } else {
                if ((attributes & 0x400) != 0)
                    throw new IOException("reparse point");
                bool directory = (attributes & 0x10) != 0;
                if (path == leaf)
                    result = directory ? "directory" : "file";
                else if (!directory)
                    throw new IOException("non-directory ancestor");
            }
            path = Path.GetDirectoryName(path);
        }
        return result;
    }

    public static void FlushFile(string path) {
        // GENERIC_WRITE; SHARE_READ | SHARE_WRITE | SHARE_DELETE; OPEN_EXISTING.
        // OPEN_REPARSE_POINT guards the regular-file open.
        using (SafeFileHandle handle = CreateFile(path, 0x40000000u, 7u, IntPtr.Zero,
                                                   3u, 0x00200000u, IntPtr.Zero)) {
            if (handle.IsInvalid)
                throw new Win32Exception(Marshal.GetLastWin32Error());
            if (!FlushFileBuffers(handle))
                throw new Win32Exception(Marshal.GetLastWin32Error());
        }
    }
}
'@
    $step = 'inspect_paths'
    foreach ($path in $r.names) {
        $kind = [SwitchWin32]::Inspect([string]$path)
        if ($kind -eq 'file' -or [SwitchWin32]::Volume([string]$path) -ne $r.volume) {
            throw [System.IO.IOException]::new('directory kind or volume drift')
        }
    }
    if ($r.command -eq 'move') {
        $step = 'move_precondition'
        if ([SwitchWin32]::Inspect([string]$r.source) -ne 'directory' -or
            [SwitchWin32]::Inspect([string]$r.destination) -ne 'absent') {
            throw [System.IO.IOException]::new('source missing or destination occupied')
        }
        $step = 'directory_move'
        [System.IO.Directory]::Move([string]$r.source, [string]$r.destination)
    } elseif ($r.command -eq 'barrier') {
        $step = 'file_flush'
        foreach ($path in $r.files) {
            if ([SwitchWin32]::Inspect([string]$path) -ne 'file' -or
                [SwitchWin32]::Volume([string]$path) -ne $r.volume) {
                throw [System.IO.IOException]::new('file kind or volume drift')
            }
            [SwitchWin32]::FlushFile([string]$path)
        }
    } else {
        throw [System.ArgumentException]::new('unsupported adapter command')
    }
    $result = @{ operation_id = $r.operation_id; command = $r.command; ok = $true }
} catch {
    $e = $_.Exception.GetBaseException()
    $result = @{ operation_id = $r.operation_id; command = $r.command; ok = $false;
                 step = $step; exception = $e.GetType().FullName; hresult = $e.HResult;
                 win32_error = $null }
    if ($e -is [System.ComponentModel.Win32Exception]) { $result.win32_error = $e.NativeErrorCode }
}
[Console]::WriteLine((ConvertTo-Json -Compress -InputObject $result))
"""


class ProviderInterrupted(Refused):
    """Provider acknowledgement is unavailable. Reobserve under the durable intent."""


class WindowsMove:
    name = WINDOWS

    def __init__(self, executable, executable_sha256, translate, volume):
        self.executable = path_at(executable)
        sha(executable_sha256)
        self.executable_sha256 = executable_sha256
        self.translate = translate
        require(re.fullmatch(r"\\\\\?\\Volume\{[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\}",
                             volume), "invalid pinned volume GUID")
        self.volume = volume

    def native(self, path):
        value = self.translate(path_at(str(path)))
        require(type(value) is str and re.fullmatch(r"C:\\[^\\]+(?:\\[^\\]+)*", value),
                "noncanonical native path")
        for part in value[3:].split("\\"):
            require(part not in {".", ".."} and part.rstrip(" .") == part and
                    not re.search(r'[\x00-\x1f<>:"/|?*]', part) and
                    not re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[0-9¹²³]|LPT[0-9¹²³])",
                                     part.split(".", 1)[0]),
                    "unsafe native path component")
        return value

    def invoke(self, command, operation_id, names, **kwargs):
        require(len({path_identity(path) for path in names}) == len(names), "native paths alias")
        body = base64.b64encode(POWERSHELL_PROVIDER.encode("utf-16-le")).decode("ascii")
        payload = encoded({"command": command, "operation_id": operation_id,
                           "volume": self.volume, "names": names, **kwargs})
        _verify_windows_provider(str(self.executable), self.executable_sha256, "PowerShell pin mismatch")
        try:
            result = subprocess.run([str(self.executable), "-NoLogo", "-NoProfile", "-NonInteractive",
                                     "-EncodedCommand", body], input=payload, capture_output=True,
                                     check=True, timeout=None if command == "move" else 60)
            evidence = decode(result.stdout)
            require(not result.stderr and type(evidence) is dict and
                    evidence.get("operation_id") == operation_id and
                    evidence.get("command") == command, "unbound provider acknowledgement")
            if evidence.get("ok") is True:
                fields(evidence, {"operation_id", "command", "ok"})
                return
            fields(evidence, {"operation_id", "command", "ok", "step", "exception", "hresult", "win32_error"})
            require(evidence["ok"] is False and type(evidence["hresult"]) is int and
                    (evidence["win32_error"] is None or type(evidence["win32_error"]) is int) and
                    type(evidence["step"]) is str and type(evidence["exception"]) is str,
                    "invalid provider observation")
        except (subprocess.SubprocessError, OSError, ValueError, Refused) as error:
            raise ProviderInterrupted(f"{command} acknowledgement unavailable for {operation_id}") from error
        detail = encoded(evidence).decode("utf-8")
        if command == "move" and evidence["step"] == "directory_move":
            raise ProviderInterrupted(detail)
        raise Refused(detail)

    def qualify(self, paths, operation_id):
        files = []
        for path in paths:
            observed = tree(path)
            if observed is not None:
                for directory, _, names in os.walk(path):
                    for name in sorted(names):
                        child = Path(directory, name)
                        read(child)
                        native_child = self.native(child)
                        require(native_child == self.native(path) + "\\" +
                                str(child.relative_to(path)).replace("/", "\\"),
                                "native file mapping differs from directory mapping")
                        files.append(native_child)
        require(len({path_identity(path) for path in files}) == len(files), "native files alias")
        self.invoke("barrier", operation_id, [self.native(p) for p in paths], files=files)

    def move(self, source, destination, operation_id):
        source, destination = self.native(source), self.native(destination)
        self.invoke("move", operation_id, [source, destination], source=source, destination=destination)


def no_replace(source, destination):
    libc = ctypes.CDLL(None, use_errno=True)
    require(hasattr(libc, "renameat2"), "metadata no-replace unavailable")
    if libc.renameat2(-100, os.fsencode(source), -100, os.fsencode(destination), 1):
        errno = ctypes.get_errno()
        raise OSError(errno, os.strerror(errno))


def qualify_metadata(parent):
    flush_dir(parent)
    with tempfile.TemporaryDirectory(prefix=".switch-probe-", dir=parent) as probe:
        source, destination = Path(probe, "source"), Path(probe, "destination")
        source.touch()
        no_replace(source, destination)
    flush_dir(parent)


class FixtureMove:
    name = FIXTURE

    def qualify(self, paths, operation_id):
        require(len({p.parent.stat().st_dev for p in paths}) == 1, "volume drift")
        for path in paths:
            flush_dir(path.parent)
            if node(path) is not None:
                for directory, _, files in os.walk(path, topdown=False):
                    for name in files:
                        child = Path(directory, name)
                        read(child)
                        with os.fdopen(os.open(child, os.O_RDONLY | os.O_NOFOLLOW), "rb") as handle:
                            os.fsync(handle.fileno())
                    flush_dir(Path(directory))

    def move(self, source, destination, operation_id):
        no_replace(source, destination)


def pointer_value(request):
    target = request["target"]
    result = {
        "pointer_version": POINTER,
        "generation": request["target_generation"],
        "scope": request["scope"],
        "profile": request["profile"],
        "active_root": request["active"],
        "operation_id": request["operation_id"],
        "operation_fingerprint": request["operation_fingerprint"],
        "provider": request["provider"],
        "recovery_scope": request["recovery_scope"],
        "continuity": request["continuity"],
    }
    if target["tag"] == "TARGET_TREE":
        result.update(state="INSTALLED", installed={**target["staged"], "path": request["active"]})
    else:
        result.update(state="UNINSTALLED", absence=target)
    return result


def expected_cut(request, raw):
    expected = request["expected_pointer_sha256"]
    require((digest(raw) if raw is not None else ABSENT) == expected, "stale pointer digest")
    if raw is None:
        require(request["expected_generation"] == 0 and request["prior"] is None,
                "unexpected absent pointer")
        return
    value = decode(raw)
    common = {"pointer_version", "generation", "scope", "profile", "active_root",
              "operation_id", "operation_fingerprint", "state", "provider",
              "recovery_scope", "continuity"}
    branch = "installed" if request["prior"] is not None else "absence"
    fields(value, common | {branch})
    token(value["operation_id"])
    sha(value["operation_fingerprint"])
    continuity_binding(value["continuity"])
    require(all(value[key] == request[key] for key in ("provider", "recovery_scope")),
            "mixed pointer profile")
    require(value["pointer_version"] == POINTER and type(value["generation"]) is int and
            value["generation"] == request["expected_generation"] and
            all(value[k] == request[k] for k in ("scope", "profile")) and
            value["active_root"] == request["active"], "wrong pointer version, cut or scope")
    if branch == "installed":
        require(value["state"] == "INSTALLED" and value[branch] == request["prior"],
                "wrong prior tree")
    else:
        proof = value[branch]
        fields(proof, {"tag", "first_operation_id", "first_receipt_sha256", "prior_absence"})
        token(proof["first_operation_id"])
        sha(proof["first_receipt_sha256"])
        sha(proof["prior_absence"])
        require(value["state"] == "UNINSTALLED" and proof["tag"] == "VERIFIED_ABSENT",
                "malformed absence pointer")


def immutable(path, value):
    data = encoded(value)
    old = record(path)
    if old is None:
        durable(path, data)
    else:
        require(old == data, "immutable journal record differs")
        flush_dir(path.parent)
    return data


def archive(journal, operation, kind):
    return journal / f"{operation}.{kind}.json"


def verify_first_install(request, journal, current):
    target = request["target"]
    operation = target["first_operation_id"]
    require(operation != request["operation_id"], "inverse reuses first operation id")
    receipt_raw = read(archive(journal, operation, "commit"))
    require(digest(receipt_raw) == target["first_receipt_sha256"], "first receipt digest mismatch")
    intent = decode(read(archive(journal, operation, "intent")))
    fields(intent, {"request", "pre_pointer", "post_pointer", "request_sha256"})
    first = validate(intent["request"])
    post = encoded(pointer_value(first))
    require(first["operation_id"] == operation and first["prior"] is None and
            first["action"] == "activate" and first["target"]["tag"] == "TARGET_TREE" and
            all(first[k] == request[k] for k in ("scope", "profile", "active",
                                                "logical_occurrence_id")) and
            post == current and intent["post_pointer"] == digest(post) and
            decode(receipt_raw) == commit_value(first, digest(post)), "wrong first-install lineage")
    pre = base64.b64decode(intent["pre_pointer"], validate=True) if intent["pre_pointer"] is not None else None
    expected_cut(first, pre)
    proof = absence_proof(first, pre)
    require(proof == target["prior_absence"] and
            read(archive(journal, operation, "absence")) == encoded({"proof": proof}),
            "original absence proof differs")


def absence_proof(request, raw):
    return digest(encoded({"scope": request["scope"], "profile": request["profile"],
                           "active_root": request["active"], "active_absent": True,
                           "pointer_sha256": digest(raw) if raw is not None else ABSENT}))


def commit_value(request, pointer_sha):
    return {"operation_id": request["operation_id"],
            "operation_fingerprint": request["operation_fingerprint"],
            "generation": request["target_generation"], "pointer_sha256": pointer_sha,
            "provider": request["provider"], "recovery_scope": request["recovery_scope"]}


def capture_runtime(manifest_path, manifest_sha, request):
    """Check the exact immutable closure before starting any packaged program."""
    sha(manifest_sha)
    require(manifest_sha == request["runtime"], "request runtime pin differs")
    manifest_path = path_at(str(manifest_path))
    raw = read(manifest_path)
    require(digest(raw) == manifest_sha, "runtime manifest digest differs")
    manifest = decode(raw)
    windows = request["provider"] == WINDOWS
    fields(manifest, {"interpreter", "members", "provider"} |
           ({"supervisor_interpreter"} if windows else set()))
    interpreter = manifest["interpreter"]
    for pin in [interpreter] + ([manifest["supervisor_interpreter"]] if windows else []):
        fields(pin, {"path", "version", "sha256"})
        sha(pin["sha256"])
        require(type(pin["version"]) is str and
                digest(read(path_at(pin["path"]))) == pin["sha256"], "interpreter pin differs")
    members = manifest["members"]
    entry = "scripts/skill_generation_switch.py"
    supervisor = "scripts/continuity_supervisor.py"
    fields(members, {entry, supervisor} if windows else {entry})
    for member, raw_sha in members.items():
        sha(raw_sha)
        require(digest(read(path_at(str(manifest_path.parent / member)))) == raw_sha,
                "standalone runtime member pin differs")
    pin = manifest["provider"]
    if windows:
        fields(pin, {"id", "recovery_scope", "path", "sha256", "volume"})
        _verify_windows_provider(pin["path"], pin["sha256"], "Windows provider pin differs")
    else:
        fields(pin, {"id", "recovery_scope"})
    require(pin["id"] == request["provider"] and
            pin["recovery_scope"] == request["recovery_scope"], "runtime provider scope differs")
    return manifest


def verify_runtime(manifest_path, manifest_sha, request, provider):
    """Recheck the protected closure inside the isolated switching process."""
    manifest = capture_runtime(manifest_path, manifest_sha, request)
    interpreter = manifest["interpreter"]
    actual = Path(sys.executable).resolve(strict=True)
    require(sys.flags.isolated == 1 and sys.flags.dont_write_bytecode == 1 and
            interpreter["version"] == platform.python_version() and
            path_at(interpreter["path"]) == actual, "interpreter pin differs")
    entry = "scripts/skill_generation_switch.py"
    script = path_at(str(Path(manifest_path).parent / entry))
    require(path_at(os.path.abspath(__file__)) == script and
            digest(read(script)) == manifest["members"][entry], "standalone entry pin differs")
    if provider.name == WINDOWS:
        pin = manifest["provider"]
        require(pin["path"] == str(provider.executable) and
                pin["sha256"] == provider.executable_sha256 and pin["volume"] == provider.volume,
                "Windows provider binding differs")


def observe_live(authority, request):
    try:
        observed = authority.continuity(request)
        continuity_binding(observed)
        return dict(observed)
    except Exception:
        return None


def readonly_cut(request, physical, journal, pointer, fence):
    applicable = set(physical)
    records = {}
    for path in sorted(journal.glob("*.json")):
        raw = read(path)
        value = decode(raw)
        if path.name.endswith(".intent.json"):
            historical = validate(value["request"])
            if (historical["scope"], historical["profile"], historical["logical_occurrence_id"]) == (
                    request["scope"], request["profile"], request["logical_occurrence_id"]):
                applicable.update(Path(historical[key]) for key in ("active", "holding"))
                for bound in (historical.get("backup"), historical["target"].get("staged")):
                    if bound is not None:
                        applicable.add(Path(bound["path"]))
        records[path.name] = digest(raw)
    inventories = {str(path): tree(path) for path in sorted(applicable)}
    current = record(pointer)
    guard = record(fence)
    return {"physical": inventories, "journal": records,
            "pointer_sha256": digest(current) if current is not None else ABSENT,
            "fence_sha256": digest(guard) if guard is not None else ABSENT}


def guarded_cut(authority, request, physical, journal, pointer, fence, expected):
    observed = observe_live(authority, request)
    if observed is not None and observed == expected:
        return observed, None
    try:
        cut = readonly_cut(request, physical, journal, pointer, fence)
    except (Refused, ValueError, OSError):
        return None, None
    if observed is None:
        return None, digest(encoded(cut))
    # Separate admission resolves exact expected pointer and all applicable physical and
    # historical evidence. It must originate in the shared authority, not this request.
    cut_sha = digest(encoded(cut))
    if authority.recovery_admit(request, observed, cut, cut_sha) is not True:
        return None, cut_sha
    if observe_live(authority, request) != observed or readonly_cut(
            request, physical, journal, pointer, fence) != cut:
        return None, cut_sha
    return observed, cut_sha


def execute(envelope, raw_sha, authority, provider, checkpoint=lambda _: None, *,
            runtime_manifest=None):
    """Use the caller's shared guard boundary and independent discovery observer.

    authority.admit(request, raw_sha) authenticates exact path/lock/grant pins,
    physical custody, quiescence and scoped effect reconciliation under both locks.
    authority.discover(request, pointer_sha) obtains fresh read-only evidence.
    """
    request = validate(envelope, check_paths=False)
    sha(raw_sha)
    if provider.name == WINDOWS:
        require(runtime_manifest is not None, "Windows protected runtime manifest is required")
    if runtime_manifest is not None:
        verify_runtime(*runtime_manifest, request, provider)
    operation = request["operation_id"]
    paths = {key: path_at(request[key]) for key in
             ("journal", "pointer", "fence", "occurrence_lock", "run_lock")}
    active, holding = (Path(request[key]) for key in ("active", "holding"))
    target, prior = request["target"], request["prior"]
    staged = Path(target["staged"]["path"]) if target["tag"] == "TARGET_TREE" else None
    backup = Path(request["backup"]["path"]) if "backup" in request else None
    journal, pointer, fence = (paths[key] for key in ("journal", "pointer", "fence"))
    physical = [p for p in (active, holding, staged, backup) if p is not None]
    check_path_separation(physical, list(paths.values()))
    require(node(journal) is not None and stat.S_ISDIR(node(journal).st_mode), "invalid journal")
    require(provider.name == request["provider"], "provider mismatch")

    def phase(name):
        immutable(archive(journal, operation, name),
                  {"operation_fingerprint": request["operation_fingerprint"], "phase": name})

    cut_lock = getattr(authority, "command_cut", None)
    with (cut_lock() if cut_lock else fixture_cut(paths)):
        require(authority.admit(request, raw_sha) is True, "authority or effect guard refused")
        post = encoded(pointer_value(request))
        post_sha = digest(post)
        current = record(pointer)
        intent_path = archive(journal, operation, "intent")
        intent_raw = record(intent_path)
        already = current == post
        fence_bytes = encoded({"scope": request["scope"],
                               "occurrence": request["logical_occurrence_id"],
                               "operation_id": operation})
        inverse = target["tag"] == "VERIFIED_ABSENT"

        pre = current
        if intent_raw is None:
            require(envelope["action"] != "reconcile" and not already, "unknown operation")
            expected_cut(request, current)
        else:
            intent = decode(intent_raw)
            fields(intent, {"request", "pre_pointer", "post_pointer", "request_sha256"})
            require(intent["request"] == request and intent["post_pointer"] == post_sha,
                    "operation id collision")
            if envelope["action"] != "reconcile":
                require(intent["request_sha256"] == raw_sha, "original request bytes differ")
            pre = base64.b64decode(intent["pre_pointer"], validate=True) if intent["pre_pointer"] is not None else None
            expected_cut(request, pre)
            require(already or current == pre, "pointer diverged from intent")

        pending = not already and intent_raw is not None

        def unknown(revalidation=None):
            if pending:
                require(authority.close_guard(request) is True, "shared guard could not be confirmed closed")
            return {"status": "UNKNOWN", "revalidation_sha256": revalidation}

        if already:
            # This is the authenticated owner of the live post-pointer. Keep the
            # same operation fenced even if a later observation fails or changes back.
            checkpoint("before_shared_close")
            require(authority.close_guard(request) is True, "shared guard could not be confirmed closed")
            checkpoint("after_shared_close")

        try:
            previous_fence = record(fence)
        except (Refused, OSError, ValueError):
            if already:
                return unknown()
            raise
        old_fence = None
        if (not already and previous_fence not in (None, fence_bytes) and
                request["action"] == "restore"):
            previous = decode(pre)
            previous_operation = previous["operation_id"]
            if inverse:
                require(previous_operation == target["first_operation_id"], "inverse fence source differs")
            previous_intent = decode(read(archive(journal, previous_operation, "intent")))
            previous_request = validate(previous_intent["request"], check_paths=False)
            require(encoded(pointer_value(previous_request)) == pre and
                    previous_request["logical_occurrence_id"] == request["logical_occurrence_id"],
                    "restore fence lineage differs")
            old_fence = encoded({"scope": request["scope"], "occurrence": request["logical_occurrence_id"],
                                 "operation_id": previous_operation})
        fence_matches = previous_fence is None or previous_fence == fence_bytes or (
            old_fence is not None and previous_fence == old_fence)
        if not already:
            require(fence_matches, "competing fence")
        pristine = not already and previous_fence != fence_bytes
        if pending and pristine and staged is not None and request["action"] == "restore":
            require(authority.restore_source(request) is True, "unverified previous tree")
        try:
            observe_roots = getattr(authority, "observe_roots", None)
            if observe_roots is not None:
                observe_roots()
            for path in physical:
                path_at(str(path))
            if provider.name == WINDOWS:
                # Metadata requires independently qualified Linux no-replace publication.
                # Never attempt that primitive on the Windows physical projection.
                physical_devices = {path.parent.stat().st_dev for path in physical}
                metadata_devices = {path.stat().st_dev for path in (journal, pointer.parent, fence.parent)}
                require(physical_devices.isdisjoint(metadata_devices),
                        "Windows physical and Linux metadata surfaces overlap")
            require(fence_matches, "competing fence")
            if pristine:
                require(matches(active, prior) and matches(holding, None), "invalid initial cut")
                if staged is not None:
                    require(matches(staged, target["staged"]), "staged tree differs")
            if pending and not pristine:
                # Observe the entire pending move cut before invoking the provider or
                # publishing any new phase. The intent and local fence bind this operation.
                staged_ready = staged is None or matches(staged, target["staged"])
                before_old = (prior is not None and matches(active, prior) and
                              matches(holding, None) and staged_ready and
                              record(archive(journal, operation,
                                             "N_TO_HOLDING" if inverse else "O_TO_ABSENT")) is None)
                before_target = (matches(holding, prior) and matches(active, None) and
                                 staged_ready and (staged is None or
                                 record(archive(journal, operation, "ABSENT_TO_N")) is None))
                target_moved = (staged is not None and matches(active, target["staged"]) and
                                matches(staged, None) and matches(holding, prior))
                require(before_old or before_target or target_moved, "ambiguous pending cut")
            if backup is not None:
                require(matches(backup, request["backup"]), "backup differs")
            if inverse and not already:
                verify_first_install(request, journal, current)
            if already:
                require(matches(active, target.get("staged")) and matches(holding, prior) and
                        (staged is None or matches(staged, None)), "committed physical cut differs")
            commit_path = archive(journal, operation, "commit")
            verified_path = archive(journal, operation, "verified")
            verified = record(verified_path)
            require(record(commit_path) is None or already, "commit without matching pointer")
            if verified is not None:
                require(already, "verified cut diverged")
                require(read(commit_path) == encoded(commit_value(request, post_sha)),
                        "verified commit differs")
                verify_discovery(request, post_sha, decode(verified))
        except (Refused, OSError, ValueError):
            if already or pending:
                return unknown()
            raise
        if not pending and pristine and staged is not None and request["action"] == "restore":
            require(authority.restore_source(request) is True, "unverified previous tree")

        expected_witness = request["continuity"]
        if intent_raw is None and current is not None:
            expected_witness = decode(current)["continuity"]
        witness, revalidation = guarded_cut(authority, request, physical, journal, pointer,
                                            fence, expected_witness)
        if witness is None or (provider.name == WINDOWS and
                               witness["volume_guid"].casefold() != provider.volume.casefold()):
            return unknown(revalidation)
        if intent_raw is None and witness != request["continuity"]:
            return {"status": "UNKNOWN", "revalidation_sha256": revalidation}

        def guard():
            if observe_live(authority, request) != witness:
                raise Refused("UNKNOWN: continuity lost before mutation")

        def guarded_effect(expected_pointer, expected_trees):
            guard()
            require(record(pointer) == expected_pointer and
                    all(matches(path, expected) for path, expected in expected_trees),
                    "UNKNOWN: exact cut changed before effect")
            if backup is not None:
                require(matches(backup, request["backup"]), "UNKNOWN: verified backup changed")
            guard()

        def final_trees():
            result = [(active, target.get("staged")), (holding, prior)]
            if staged is not None:
                result.append((staged, None))
            return result

        guard()
        provider.qualify(physical, operation)
        for parent in {journal, pointer.parent, fence.parent}:
            if already:
                flush_dir(parent)
            else:
                qualify_metadata(parent)
        if revalidation is not None:
            guard()
            immutable(archive(journal, operation, "recovery-" + witness["session_id"] + "-" + revalidation),
                      {"operation_fingerprint": request["operation_fingerprint"],
                       "provider": request["provider"], "recovery_scope": request["recovery_scope"],
                       "continuity": witness, "revalidation_sha256": revalidation})
        if intent_raw is None:
            if prior is None:
                immutable(archive(journal, operation, "absence"),
                          {"proof": absence_proof(request, current)})
            immutable(intent_path, {"request": request, "post_pointer": post_sha,
                                    "request_sha256": raw_sha,
                                    "pre_pointer": base64.b64encode(current).decode("ascii")
                                    if current is not None else None})

        if not already:
            checkpoint("before_shared_close")
            if pristine:
                expected_trees = [(active, prior), (holding, None)]
                if staged is not None:
                    expected_trees.append((staged, target["staged"]))
                guarded_effect(current, expected_trees)
                require(record(fence) == previous_fence, "local fence changed before shared close")
            else:
                guard()
            require(authority.close_guard(request) is True, "shared guard could not be confirmed closed")
            checkpoint("after_shared_close")

        if verified is not None:
            if revalidation is not None:
                guard()
                evidence = authority.discover(request, post_sha)
                if evidence is None:
                    return {"commit": commit_value(request, post_sha), "discovery": "PENDING_FENCED"}
                verify_discovery(request, post_sha, evidence)
                immutable(archive(journal, operation, "reverified-" + witness["session_id"] + "-" + revalidation),
                          evidence)
            guarded_effect(post, final_trees())
            if previous_fence == fence_bytes:
                fence.unlink()
                flush_dir(fence.parent)
            guarded_effect(post, final_trees())
            require(authority.release_guard(request, witness) is True, "shared guard remains closed")
            return {"commit": commit_value(request, post_sha), "discovery": "VERIFIED"}

        if previous_fence != fence_bytes:
            durable(fence, fence_bytes, replace=previous_fence is not None)
        phase("PREPARE")

        if not already:
            held_prior = matches(holding, prior)
            if prior is not None and not held_prior:
                require(matches(active, prior) and matches(holding, None) and
                        (staged is None or matches(staged, target["staged"])), "ambiguous old cut")
                name = "N_TO_HOLDING" if inverse else "O_TO_ABSENT"
                require(record(archive(journal, operation, name)) is None, "old move regressed")
                checkpoint("before_" + name)
                before_old_move = [(active, prior), (holding, None)]
                if staged is not None:
                    before_old_move.append((staged, target["staged"]))
                guarded_effect(current, before_old_move)
                provider.move(active, holding, operation)
                checkpoint("after_" + name)
                guard()
                require(matches(active, None) and matches(holding, prior), "old move differs")
                held_prior = True
            require(held_prior, "holding differs")
            if prior is not None:
                provider.qualify(physical, operation)
                name = "N_TO_HOLDING" if inverse else "O_TO_ABSENT"
                phase(name)
                checkpoint("journaled_" + name)
            if staged is not None and not matches(active, target["staged"]):
                require(matches(active, None) and matches(staged, target["staged"]), "ambiguous staged cut")
                require(record(archive(journal, operation, "ABSENT_TO_N")) is None, "target move regressed")
                checkpoint("before_ABSENT_TO_N")
                guarded_effect(current, [(active, None), (holding, prior), (staged, target["staged"])])
                provider.move(staged, active, operation)
                checkpoint("after_ABSENT_TO_N")
                guard()
            require(matches(active, target.get("staged")) and matches(holding, prior) and
                    (staged is None or matches(staged, None)), "target cut differs")
            provider.qualify(physical, operation)
            if staged is not None:
                phase("ABSENT_TO_N")
                checkpoint("journaled_ABSENT_TO_N")
            checkpoint("before_CAS")
            guarded_effect(current, final_trees())
            durable(pointer, post, replace=current is not None)
            require(record(pointer) == post, "UNKNOWN: published pointer differs")
            checkpoint("after_CAS")
        result = commit_value(request, post_sha)
        immutable(commit_path, result)
        checkpoint("after_receipt")
        guard()
        evidence = authority.discover(request, post_sha)
        if evidence is None:
            checkpoint("after_failed_discovery")
            return {"commit": result, "discovery": "PENDING_FENCED"}
        verify_discovery(request, post_sha, evidence)
        immutable(verified_path, evidence)
        checkpoint("before_fence_release")
        guarded_effect(post, final_trees())
        fence.unlink()
        flush_dir(fence.parent)
        guarded_effect(post, final_trees())
        require(authority.release_guard(request, witness) is True, "shared guard remains closed")
        return {"commit": result, "discovery": "VERIFIED"}


def verify_discovery(request, pointer_sha, evidence):
    fields(evidence, {"profile", "operation_id", "pointer_sha256", "generation", "fresh",
                      "success", "same_name_count", "selection", "client", "surfaces", "observed_at"})
    staged = request["target"].get("staged")
    require(evidence["fresh"] is True and evidence["success"] is True and
            evidence["profile"] == request["profile"] and
            evidence["operation_id"] == request["operation_id"] and
            evidence["pointer_sha256"] == pointer_sha and
            evidence["generation"] == request["target_generation"] and
            type(evidence["same_name_count"]) is int and
            evidence["same_name_count"] == int(staged is not None) and
            evidence["selection"] == ({**staged, "path": request["active"]} if staged else None) and
            all(type(evidence[k]) is str and evidence[k] for k in
                ("client", "surfaces", "observed_at")), "discovery does not qualify")


class DisposableAuthority:
    """CLI-only authority for a disposable directory provisioned by the harness."""

    def __init__(self, root):
        self.root = root

    def admit(self, request, raw_sha):
        if request["scope"] != "disposable-fixture" or request["provider"] != FIXTURE:
            return False
        local_names = {"active": "active", "pointer": "pointer.json", "journal": "journal",
                       "fence": "fence.json", "occurrence_lock": "occurrence.lock",
                       "run_lock": "run.lock"}
        if any(Path(request[key]) != self.root / name for key, name in local_names.items()):
            return False
        paths = [Path(request["holding"])]
        paths += [Path(item["path"]) for item in
                  (request["target"].get("staged"), request.get("backup")) if item]
        return all(path.parent == self.root and path.name.startswith("scratch-") for path in paths)

    def continuity(self, request):
        return None

    def close_guard(self, request):
        return True

    def recovery_admit(self, request, witness, cut, cut_sha):
        return False

    def release_guard(self, request, witness):
        return False

    def restore_source(self, request):
        journal = Path(request["journal"])
        pointer = decode(read(Path(request["pointer"])))
        previous = decode(read(archive(journal, pointer["operation_id"], "intent")))
        prior = previous["request"]["prior"]
        return prior is not None and (prior["manifest_sha256"] ==
                                      request["target"]["staged"]["manifest_sha256"])

    def discover(self, request, pointer_sha):
        return None


def execute_file(action, request_path, request_sha, authority, provider, *, runtime_manifest,
                 checkpoint=lambda _: None):
    """Authenticate raw request bytes before dispatch to a separately supplied authority."""
    sha(request_sha)
    raw = read(path_at(str(request_path)))
    require(digest(raw) == request_sha, "request pin mismatch")
    envelope = decode(raw)
    require(type(envelope) is dict and envelope.get("action") == action,
            "command/request action mismatch")
    return execute(envelope, request_sha, authority, provider, checkpoint,
                   runtime_manifest=runtime_manifest)


CHANNEL_LIMIT = 16 * 1024**2


def read_message(stream):
    line = stream.readline(CHANNEL_LIMIT + 1)
    if not line:
        return None
    require(len(line) <= CHANNEL_LIMIT and line.endswith(b"\n"), "unbounded authority message")
    return decode(line)


def send_message(stream, value):
    raw = encoded(value) + b"\n"
    require(len(raw) <= CHANNEL_LIMIT, "unbounded authority response")
    stream.write(raw)
    stream.flush()


class RemoteAuthority:
    """Use the inherited private channel to the lock-holding coordinator."""

    def __init__(self, stream):
        self.stream = stream

    def call(self, method, *args):
        send_message(self.stream, {"method": method, "args": list(args)})
        response = read_message(self.stream)
        require(type(response) is dict, "authority disconnected")
        if set(response) == {"error"}:
            raise Refused(response["error"])
        fields(response, {"result"})
        return response["result"]

    def command_cut(self):
        require(self.call("cut") is True, "shared coordinator cut unavailable")
        return nullcontext()

    def admit(self, request, raw_sha):
        return self.call("admit", request["operation_fingerprint"], raw_sha)

    def close_guard(self, request):
        return self.call("close_guard")

    def continuity(self, request):
        return self.call("continuity")

    def recovery_admit(self, request, witness, cut, cut_sha):
        return self.call("recovery_admit", witness, cut, cut_sha)

    def release_guard(self, request, witness):
        return self.call("release_guard", witness)

    def restore_source(self, request):
        return self.call("restore_source")

    def discover(self, request, pointer_sha):
        return self.call("discover", pointer_sha)

    def observe_roots(self):
        self.call("observe_roots")


class RemoteWindowsMove:
    """Keep provider execution in the coordinator if the switching process dies."""

    name = WINDOWS

    def __init__(self, authority, pin):
        self.authority = authority
        self.executable = Path(pin["path"])
        self.executable_sha256 = pin["sha256"]
        self.volume = pin["volume"]

    def qualify(self, paths, operation_id):
        self.authority.call("qualify", [str(path) for path in paths], operation_id)

    def move(self, source, destination, operation_id):
        self.authority.call("move", str(source), str(destination), operation_id)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["activate", "reconcile", "restore"])
    parser.add_argument("request")
    parser.add_argument("request_sha256")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--disposable-fixture")
    source.add_argument("--authority-fd", type=int)
    parser.add_argument("--runtime-manifest", nargs=2, metavar=("PATH", "SHA256"), required=True)
    args = parser.parse_args()
    try:
        if args.disposable_fixture is not None:
            root = path_at(args.disposable_fixture)
            require(root.name.startswith("qs-switch-fixture-"), "unrecognized disposable root")
            result = execute_file(args.action, args.request, args.request_sha256,
                                  DisposableAuthority(root), FixtureMove(),
                                  runtime_manifest=args.runtime_manifest)
        else:
            raw = read(path_at(args.request))
            require(digest(raw) == args.request_sha256, "request pin mismatch")
            request = validate(decode(raw), check_paths=False)
            require(request["provider"] == WINDOWS, "coordinator requires Windows profile")
            manifest = capture_runtime(*args.runtime_manifest, request)
            with socket.socket(fileno=args.authority_fd) as channel, channel.makefile("rwb") as stream:
                authority = RemoteAuthority(stream)
                provider = RemoteWindowsMove(authority, manifest["provider"])
                result = execute_file(args.action, args.request, args.request_sha256, authority,
                                      provider, runtime_manifest=args.runtime_manifest,
                                      checkpoint=lambda name: authority.call("checkpoint", name))
        print(encoded(result).decode("utf-8"))
    except (Refused, OSError, ValueError, TypeError, subprocess.SubprocessError) as error:
        print(f"switch refused or interrupted: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
