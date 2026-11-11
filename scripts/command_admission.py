"""Admission and byte capture for one lock-held Question Space command."""

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from copy import deepcopy
from hashlib import sha256
import json
import os
import secrets
import stat
import sys


class AdmissionError(ValueError):
    pass


@dataclass(frozen=True)
class Request:
    operation: str
    key: str
    actor: str
    authority_sha256: str
    run_id: str
    snapshot_sha256: str
    expected_revision: str
    payload: bytes
    occurrence: str = ""

    def fingerprint(self):
        if type(self.payload) is not bytes or type(self.occurrence) is not str:
            raise AdmissionError("invalid command payload or occurrence")
        fields = (
            self.operation, self.key, self.actor, self.authority_sha256,
            self.run_id, self.snapshot_sha256, self.expected_revision,
            sha256(self.payload).hexdigest(), self.occurrence,
        )
        if any(type(field) is not str or not field for field in fields[:-1]):
            raise AdmissionError("incomplete command request")
        return sha256(json.dumps(fields, ensure_ascii=True).encode("ascii")).hexdigest()


@dataclass(frozen=True)
class Committed:
    fingerprint: str
    receipt: bytes


@dataclass(frozen=True)
class CapturedInput:
    """Path-shaped input to qs.load_yaml, backed only by captured bytes."""

    path: str
    raw: bytes

    def read_bytes(self):
        return self.raw

    def __str__(self):
        return self.path


@dataclass(frozen=True)
class StagedWrite:
    path: str
    raw: bytes
    before_sha256: str | None


def relative_parts(path):
    parts = path.split("/")
    if any(part in {"", ".", ".."} or "\0" in part for part in parts):
        raise AdmissionError("unsafe relative path")
    return parts


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def capture_file(root_fd, path, maximum):
    *parents, name = relative_parts(path)
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    with ExitStack() as stack:
        directory = root_fd
        for parent in parents:
            directory = os.open(parent, flags | os.O_DIRECTORY, dir_fd=directory)
            stack.callback(os.close, directory)
        fd = os.open(name, flags | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, "rb") as stream:
            initial = os.fstat(stream.fileno())
            if not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1 or initial.st_size > maximum:
                raise AdmissionError("unsupported captured file")
            raw = stream.read(maximum + 1)
            if len(raw) != initial.st_size or identity(os.fstat(stream.fileno())) != identity(initial):
                raise AdmissionError("source changed during capture")
    return raw


def publish_bytes(path, raw, *, replace=True):
    """Publish one file relative to its opened, symlink-free parent."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    absolute = path.absolute()
    *parents, name = relative_parts(absolute.relative_to(absolute.anchor).as_posix())
    with ExitStack() as stack:
        directory = os.open(absolute.anchor, flags)
        stack.callback(os.close, directory)
        for parent in parents:
            try:
                opened = os.open(parent, flags, dir_fd=directory)
            except FileNotFoundError:
                try:
                    os.mkdir(parent, dir_fd=directory)
                except FileExistsError:
                    pass
                else:
                    os.fsync(directory)
                opened = os.open(parent, flags, dir_fd=directory)
            directory = opened
            stack.callback(os.close, directory)
        temporary = ".tmp-" + secrets.token_hex(16)
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_CLOEXEC,
                     0o600, dir_fd=directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            if replace:
                os.replace(temporary,name,src_dir_fd=directory,dst_dir_fd=directory)
            else:
                os.link(temporary,name,src_dir_fd=directory,dst_dir_fd=directory,follow_symlinks=False)
                os.unlink(temporary,dir_fd=directory)
            os.fsync(directory)
        finally:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass


def publish_locked(marker, projections, write):
    """Publish a prevalidated journal marker before its exact projections.

    The caller supplies the operation's existing immutable journal recipe.
    Projection bytes are fully prepared before this function is called.
    """
    if marker is not None:
        path, raw = marker
        write(path, raw)
    for path, raw in projections:
        write(path, raw)


class Cut:
    """Private preparation for a single authoritative command."""

    def __init__(self, run, state, request):
        self._run = run
        self.state = state
        self._request = request
        self._sources = {}
        self._staged = {}

    def capture(self, path, expected_sha256, maximum):
        if type(maximum) is not int or maximum < 0:
            raise AdmissionError("invalid capture bound")
        self._run.authorize_read_locked(self.state, self._request, path)
        if path not in self._sources:
            self._sources[path] = capture_file(self._run.root_fd, path, maximum)
        raw = self._sources[path]
        if len(raw) > maximum or sha256(raw).hexdigest() != expected_sha256:
            raise AdmissionError("captured source differs from its exact reference")
        return raw

    def stage(self, path, raw, *, before_sha256=None):
        relative_parts(path)
        if type(raw) is not bytes or path in self._staged:
            raise AdmissionError("invalid or repeated staged destination")
        self._run.authorize_write_locked(self.state, self._request, path)
        if before_sha256 is not None and (
            type(before_sha256) is not str or len(before_sha256) != 64
            or any(ch not in "0123456789abcdef" for ch in before_sha256)
        ):
            raise AdmissionError("invalid staged destination anchor")
        self._staged[path] = StagedWrite(path, raw, before_sha256)


@contextmanager
def command_cut(run_lock, occurrence_lock=None):
    """Take the coordinator cut once, after any applicable occurrence lock."""
    with ExitStack() as stack:
        if occurrence_lock is not None:
            stack.enter_context(occurrence_lock)
        stack.enter_context(run_lock)
        yield


def admit(run, request, apply_locked):
    """Prepare once under existing locks and commit through the run's journal.

    The run adapter reads authoritative committed state without publishing it.
    Its commit_locked owns durable intent, fsync, marker, and projection.
    A StagedWrite anchors publication to absence or exact previous bytes. The
    apply_locked adapter may stage bytes only and returns exact receipt bytes.
    The adapter derives occurrence from the typed operation before admission,
    and validates its binding again under the locks before accepting it.
    """
    fingerprint = request.fingerprint()
    occurrence_lock = run.occurrence_lock(request.occurrence) if request.occurrence else None
    with command_cut(run.lock(), occurrence_lock):
        state = run.read_committed_locked()
        run.authenticate_locked(state, request)
        prior = run.lookup_committed_locked(state, request.key)
        if prior is not None:
            if prior.fingerprint != fingerprint:
                raise AdmissionError("committed request identity conflicts")
            return prior.receipt
        run.check_new_locked(state, request)
        cut = Cut(run, state, request)
        receipt = apply_locked(cut)
        if type(receipt) is not bytes or not receipt:
            raise AdmissionError("exact receipt bytes required")
        run.commit_locked(state, request, Committed(fingerprint, receipt), tuple(cut._staged.values()))
        return receipt


def parsed_size(value, limit):
    pending = [value]
    visited = set()
    size = 0
    while pending and size <= limit:
        node = pending.pop()
        identity = id(node)
        if identity in visited:
            continue
        visited.add(identity)
        size += sys.getsizeof(node)
        if isinstance(node, dict):
            pending.extend(node.keys())
            pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)
    return size


def cached_loader(original, *, max_entries=512, max_bytes=128 * 1024**2, max_input_bytes=8 * 1024**2):
    cache = {}
    resident = 0

    def load(path):
        nonlocal resident
        raw = path.raw
        if len(raw) > max_input_bytes:
            return original(path)
        key = (original, sha256(raw).digest())
        if key in cache:
            return deepcopy(cache[key])
        value = original(path)
        if len(cache) < max_entries and resident < max_bytes:
            size = parsed_size(value, max_bytes - resident)
            if resident + size <= max_bytes:
                try:
                    result = deepcopy(value)
                except RecursionError:
                    return value
                cache[key] = value
                resident += size
                return result
        return value

    return load
