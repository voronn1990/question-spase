"""Disposable specification fixtures. No native Windows or client qualification."""

import base64
import copy
import importlib.util
import os
import select
import signal
from pathlib import Path
from subprocess import CompletedProcess
import tempfile
import unittest
from unittest.mock import patch


entry = Path(__file__).resolve().parents[1] / "scripts/skill_generation_switch.py"
spec = importlib.util.spec_from_file_location("switch", entry)
switch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(switch)


class Interrupted(RuntimeError):
    pass


WITNESS = dict(boot_id="fixture-boot", volume_guid="fixture-volume",
               volume_serial=1, session_id="fixture-session")


class Authority:
    unknown = False
    discoverable = False
    wrong_profile = False
    restore_manifest = None

    def __init__(self):
        self.witness = dict(WITNESS)
        self.guard_closed = False
        self.allow_recovery = False
        self.revalidations = []

    def continuity(self, request):
        return self.witness

    def close_guard(self, request):
        self.guard_closed = True
        return True

    def recovery_admit(self, request, witness, cut, cut_sha):
        self.revalidations.append((cut, cut_sha))
        return self.allow_recovery

    def release_guard(self, request, witness):
        self.guard_closed = False
        return True

    def admit(self, request, raw_sha):
        return not self.unknown

    def restore_source(self, request):
        return request["target"]["staged"]["manifest_sha256"] == self.restore_manifest

    def discover(self, request, pointer_sha):
        if not self.discoverable:
            return None
        selected = request["target"].get("staged")
        return dict(profile="wrong" if self.wrong_profile else request["profile"],
                    operation_id=request["operation_id"], pointer_sha256=pointer_sha,
                    generation=request["target_generation"], fresh=True, success=True,
                    same_name_count=int(selected is not None),
                    selection={**selected, "path": request["active"]} if selected else None,
                    client="fixture/1", surfaces="fixture", observed_at="fixture-time")


class Provider(switch.FixtureMove):
    def __init__(self):
        self.moves = []
        self.unqualified = False
        self.lose_ack = False

    def qualify(self, paths, operation_id):
        if self.unqualified:
            raise switch.Refused("fixture barrier unavailable")
        super().qualify(paths, operation_id)

    def move(self, source, destination, operation_id):
        super().move(source, destination, operation_id)
        self.moves.append((source.name, destination.name))
        if self.lose_ack:
            self.lose_ack = False
            raise Interrupted("move occurred without acknowledgement")


class WindowsProviderPinTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="qs-windows-provider-pin-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.executable = self.root / "System32/WindowsPowerShell/v1.0/powershell.exe"
        self.executable.parent.mkdir(parents=True)
        self.executable.write_bytes(b"pinned PowerShell fixture")
        self.alias = self.root / "provider-alias"
        self.alias.hardlink_to(self.executable)
        self.pin = self.file_pin(self.executable)
        self.volume = r"\\?\Volume{12345678-1234-1234-1234-123456789abc}"
        self.manifest_path = self.root / "runtime.json"
        interpreter = self.root / "python"
        interpreter.write_bytes(b"pinned interpreter fixture")
        members = {}
        (self.root / "scripts").mkdir()
        for member in ("scripts/skill_generation_switch.py", "scripts/continuity_supervisor.py"):
            path = self.root / member
            path.write_bytes(member.encode())
            members[member] = self.file_pin(path)["sha256"]
        self.manifest = {
            "interpreter": {**self.file_pin(interpreter), "version": "fixture"},
            "supervisor_interpreter": {**self.file_pin(interpreter), "version": "fixture"},
            "members": members,
            "provider": {**self.pin, "id": switch.WINDOWS,
                         "recovery_scope": switch.WINDOWS_SCOPE, "volume": self.volume},
        }
        system_path = patch.object(switch, "_SYSTEM_POWERSHELL", self.executable)
        system_path.start()
        self.addCleanup(system_path.stop)

    def file_pin(self, path):
        return {"path": str(path), "sha256": switch.digest(path.read_bytes())}

    def capture(self):
        raw = switch.encoded(self.manifest)
        self.manifest_path.write_bytes(raw)
        runtime_sha = switch.digest(raw)
        return switch.capture_runtime(self.manifest_path, runtime_sha, {
            "runtime": runtime_sha, "provider": switch.WINDOWS,
            "recovery_scope": switch.WINDOWS_SCOPE,
        })

    def provider(self, path):
        return switch.WindowsMove(str(path), self.pin["sha256"], None, self.volume)

    def test_provider_launch_disables_progress_before_cmdlets(self):
        acknowledgement = switch.encoded({"command": "barrier", "operation_id": "fixture", "ok": True})
        with patch.object(switch.subprocess, "run",
                          return_value=CompletedProcess([], 0, acknowledgement, b"")) as run:
            self.provider(self.executable).invoke("barrier", "fixture", [])
        argv = run.call_args.args[0]
        body = base64.b64decode(argv[argv.index("-EncodedCommand") + 1], validate=True).decode("utf-16-le")
        self.assertEqual(body.strip().splitlines()[0], "$ProgressPreference = 'SilentlyContinue'")
        self.assertEqual(switch.decode(run.call_args.kwargs["input"])["operation_id"], "fixture")

    def test_provider_rejects_failed_or_unbound_acknowledgements(self):
        reply = {"command": "barrier", "operation_id": "fixture", "ok": True}
        ack = switch.encoded(reply)
        cases = (
            ("stderr", 0, ack, b"provider error"),
            ("clixml", 0, ack, b'#< CLIXML\r\n<Objs><Obj S="progress" /></Objs>'),
            ("nonzero", 1, ack, b""),
            ("malformed", 0, b"not JSON", b""),
            ("other operation", 0, switch.encoded({**reply, "operation_id": "other"}), b""),
            ("other command", 0, switch.encoded({**reply, "command": "move"}), b""),
        )
        provider = self.provider(self.executable)
        for name, code, stdout, stderr in cases:
            with self.subTest(name=name):
                def completed(argv, *, check, **kwargs):
                    result = CompletedProcess(argv, code, stdout, stderr)
                    if check:
                        result.check_returncode()
                    return result

                with patch.object(switch.subprocess, "run", side_effect=completed):
                    with self.assertRaisesRegex(switch.ProviderInterrupted, "barrier acknowledgement unavailable"):
                        provider.invoke("barrier", "fixture", [])

    def test_hardlinked_system_provider_is_pinned_at_capture_and_each_launch(self):
        self.assertEqual(self.executable.stat().st_nlink, 2)
        self.assertEqual(self.capture(), self.manifest)
        provider = self.provider(self.executable)
        acknowledgement = switch.encoded({"command": "barrier", "operation_id": "fixture", "ok": True})
        with patch.object(switch.subprocess, "run",
                          return_value=CompletedProcess([], 0, acknowledgement, b"")) as run:
            provider.invoke("barrier", "fixture", [])
            run.assert_called_once()
            self.assertEqual(run.call_args.args[0][0], str(self.executable))
            self.alias.write_bytes(b"provider bytes changed through alias")
            with self.assertRaisesRegex(switch.Refused, "PowerShell pin mismatch"):
                provider.invoke("barrier", "fixture", [])
            run.assert_called_once()
        with self.assertRaisesRegex(switch.Refused, "Windows provider pin differs"):
            self.capture()

    def test_provider_rejects_alias_symlink_and_changed_open_identity(self):
        provider = self.provider(self.executable)
        with patch.object(switch.subprocess, "run") as run:
            self.manifest["provider"]["path"] = str(self.alias)
            with self.assertRaisesRegex(switch.Refused, "expected unaliased regular file"):
                self.capture()
            with self.assertRaisesRegex(switch.Refused, "expected unaliased regular file"):
                self.provider(self.alias).invoke("barrier", "fixture", [])
            self.manifest["provider"]["path"] = str(self.executable)
            self.executable.unlink()
            self.executable.symlink_to(self.alias)
            with self.assertRaisesRegex(switch.Refused, "link or reparse point"):
                self.capture()
            with self.assertRaisesRegex(switch.Refused, "link or reparse point"):
                provider.invoke("barrier", "fixture", [])

            self.executable.unlink()
            self.executable.hardlink_to(self.alias)
            replacement = self.root / "replacement"
            replacement.write_bytes(self.alias.read_bytes())
            actual_open = os.open

            def open_replacement(path, flags):
                if path == self.executable:
                    path = replacement
                return actual_open(path, flags)

            with patch.object(switch.os, "open", side_effect=open_replacement):
                with self.assertRaisesRegex(switch.Refused, "file identity changed"):
                    self.capture()
                with self.assertRaisesRegex(switch.Refused, "file identity changed"):
                    provider.invoke("barrier", "fixture", [])
            run.assert_not_called()

    def test_hardlinked_runtime_members_interpreter_and_control_files_are_refused(self):
        self.capture()
        for path in (self.root / "scripts/skill_generation_switch.py",
                     self.root / "python", self.manifest_path):
            with self.subTest(path=path.name):
                alias = self.root / "runtime-alias"
                alias.hardlink_to(path)
                with self.assertRaisesRegex(switch.Refused, "expected unaliased regular file"):
                    self.capture()
                alias.unlink()
        self.manifest["supervisor_interpreter"].update(self.pin)
        with self.assertRaisesRegex(switch.Refused, "expected unaliased regular file"):
            self.capture()


class SwitchTests(unittest.TestCase):
    def provision(self):
        directory = tempfile.TemporaryDirectory(prefix="qs-switch-fixture-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        (self.root / "journal").mkdir()
        for lock in ("occurrence_lock", "run_lock"):
            (self.root / lock).touch()
        self.active = self.root / "active"
        self.pointer = self.root / "pointer"
        self.fence = self.root / "fence"
        self.provider = Provider()
        self.authority = Authority()
        (self.root / "unrelated").write_bytes(b"untouched")

    def stage(self, name, content):
        path = self.root / name
        path.mkdir()
        (path / "SKILL.md").write_bytes(content)
        return {"path": str(path), **switch.tree(path)}

    def request(self, op, action, target, prior=None, generation=0, backup=None):
        result = dict(request_version=switch.REQUEST, operation_id=op,
                      logical_occurrence_id="stable", action=action, expected_generation=generation,
                      expected_pointer_sha256=switch.digest(self.pointer.read_bytes())
                      if self.pointer.exists() else switch.ABSENT,
                      target_generation=generation + 1, scope="disposable-fixture", profile="test",
                      provider=switch.FIXTURE, recovery_scope=switch.FIXTURE_SCOPE,
                      continuity=dict(WITNESS), runtime="a" * 64, prior=prior, target=target)
        for name in ("active", "pointer", "fence", "journal", "occurrence_lock", "run_lock"):
            result[name] = str(self.root / name)
        result["holding"] = str(self.root / ("held-" + op))
        if backup is not None:
            result["backup"] = backup
        return self.sign(result)

    def sign(self, request):
        request["operation_fingerprint"] = switch.fingerprint(request)
        return request

    def call(self, request, fail=None, *, reconcile=False):
        envelope = request
        if reconcile:
            envelope = dict(request_version=switch.REQUEST, action="reconcile", original=request,
                            operation_id=request["operation_id"],
                            operation_fingerprint=request["operation_fingerprint"])

        def fault(cut):
            if cut == fail:
                raise Interrupted(cut)

        return switch.execute(envelope, switch.digest(switch.encoded(envelope)),
                              self.authority, self.provider, fault)

    def first(self):
        staged = self.stage("staged-N", b"N")
        return self.request("first", "activate", {"tag": "TARGET_TREE", "staged": staged})

    def inverse(self):
        first = self.first()
        self.assertEqual(self.call(first)["discovery"], "PENDING_FENCED")
        journal = self.root / "journal"
        absence = switch.decode((journal / "first.absence.json").read_bytes())["proof"]
        receipt = switch.digest((journal / "first.commit.json").read_bytes())
        target = dict(tag="VERIFIED_ABSENT", first_operation_id="first",
                      first_receipt_sha256=receipt, prior_absence=absence)
        return self.request("inverse", "restore", target,
                            {"path": str(self.active), **switch.tree(self.active)}, 1)

    def test_first_install_inverse_recovers_all_named_cuts(self):
        for cut in ("before_N_TO_HOLDING", "after_N_TO_HOLDING", "before_CAS",
                    "after_CAS", "after_receipt", "journaled_N_TO_HOLDING", "before_fence_release", "lost_ack"):
            with self.subTest(cut=cut):
                self.provision()
                request = self.inverse()
                self.provider.lose_ack = cut == "lost_ack"
                self.authority.discoverable = cut == "before_fence_release"
                with self.assertRaises(Interrupted):
                    self.call(request, cut)
                self.assertTrue(self.fence.exists())
                result = self.call(request, reconcile=True)
                self.assertEqual(result["discovery"],
                                 "VERIFIED" if cut == "before_fence_release" else "PENDING_FENCED")
                self.assertFalse(self.active.exists())
                self.assertEqual((Path(request["holding"]) / "SKILL.md").read_bytes(), b"N")
                self.assertEqual(switch.tree(Path(request["holding"]))["identity"],
                                 request["prior"]["identity"])
                pointer_raw = self.pointer.read_bytes()
                pointer = switch.decode(pointer_raw)
                self.assertEqual((pointer["state"], pointer["generation"]), ("UNINSTALLED", 2))
                self.assertNotIn("installed", pointer)
                self.authority.discoverable = True
                self.assertEqual(self.call(request, reconcile=True)["discovery"], "VERIFIED")
                self.assertEqual(self.call(request, reconcile=True)["discovery"], "VERIFIED")
                self.assertEqual(self.pointer.read_bytes(), pointer_raw)
                self.assertEqual(self.provider.moves, [("staged-N", "active"), ("active", "held-inverse")])
                self.assertFalse(self.fence.exists())
                self.assertTrue((self.root / "journal/first.commit.json").exists())
                self.assertEqual((self.root / "unrelated").read_bytes(), b"untouched")

    def test_initial_activation_reconciles_move_and_pointer_interruptions(self):
        for cut in ("before_ABSENT_TO_N", "after_ABSENT_TO_N", "journaled_ABSENT_TO_N",
                    "before_CAS", "after_CAS", "after_receipt"):
            with self.subTest(cut=cut):
                self.provision()
                request = self.first()
                with self.assertRaises(Interrupted):
                    self.call(request, cut)
                self.assertEqual(self.call(request, reconcile=True)["discovery"], "PENDING_FENCED")
                self.assertEqual((self.active / "SKILL.md").read_bytes(), b"N")
                self.assertEqual(len(self.provider.moves), 1)

    def test_stale_unknown_tampered_and_occupied_cuts(self):
        for reason in ("stale", "unknown", "tampered", "empty", "occupied", "bad_receipt",
                       "wrong_profile", "branch_mix", "unqualified"):
            with self.subTest(reason=reason):
                self.provision()
                request = self.inverse()
                if reason == "stale":
                    request["expected_pointer_sha256"] = "0" * 64
                if reason == "unknown":
                    self.authority.unknown = True
                if reason == "tampered":
                    (self.active / "SKILL.md").write_bytes(b"changed")
                if reason in ("empty", "occupied"):
                    held = Path(request["holding"])
                    held.mkdir()
                    if reason == "occupied":
                        (held / "keep").touch()
                if reason == "bad_receipt":
                    request["target"]["first_receipt_sha256"] = "0" * 64
                if reason == "wrong_profile":
                    request["profile"] = "different"
                if reason == "branch_mix":
                    request["target"]["staged"] = None
                if reason == "unqualified":
                    self.provider.unqualified = True
                before = self.pointer.read_bytes()
                with self.assertRaises(switch.Refused):
                    self.call(self.sign(request))
                self.assertTrue(self.fence.exists())
                self.assertEqual(self.pointer.read_bytes(), before)
                self.assertEqual(len(self.provider.moves), 1)

    def test_changed_fingerprint_unknown_operation_and_discovery_failure(self):
        self.provision()
        request = self.inverse()
        with self.assertRaises(switch.Refused):
            self.call(request, reconcile=True)
        with self.assertRaises(Interrupted):
            self.call(request, "before_N_TO_HOLDING")
        changed = copy.deepcopy(request)
        changed["target_generation"] += 1
        with self.assertRaises(switch.Refused):
            self.call(self.sign(changed), reconcile=True)
        self.authority.discoverable = True
        self.authority.wrong_profile = True
        with self.assertRaises(switch.Refused):
            self.call(request, reconcile=True)
        self.assertTrue(self.fence.exists())
        self.assertFalse(self.active.exists())

    def test_changed_namespace_after_checkpoint_blocks_cas(self):
        self.provision()
        request = self.first()

        def change(cut):
            if cut == "before_CAS":
                (self.active / "SKILL.md").write_bytes(b"tampered after observation")

        with self.assertRaisesRegex(switch.Refused, "exact cut changed"):
            switch.execute(request, switch.digest(switch.encoded(request)),
                           self.authority, self.provider, change)
        self.assertFalse(self.pointer.exists())
        self.assertTrue(self.authority.guard_closed)
        self.assertEqual(len(self.provider.moves), 1)

    def test_new_session_during_pending_provider_cut_blocks_move(self):
        self.provision()
        request = self.first()

        def replace_session(cut):
            if cut == "before_ABSENT_TO_N":
                self.authority.witness["session_id"] = "new-session"

        with self.assertRaisesRegex(switch.Refused, "continuity lost"):
            switch.execute(request, switch.digest(switch.encoded(request)),
                           self.authority, self.provider, replace_session)
        self.assertFalse(self.pointer.exists())
        self.assertTrue(self.authority.guard_closed)
        self.assertEqual(self.provider.moves, [])

    def test_discontinuous_witness_closes_guard_even_after_release(self):
        for released in (False, True):
            for gap in ("boot_id", "volume_guid", "session_id", "missing"):
                with self.subTest(released=released, gap=gap):
                    self.provision()
                    request = self.first()
                    self.authority.discoverable = released
                    self.call(request)
                    before = self.pointer.read_bytes()
                    if gap == "missing":
                        self.authority.witness = None
                    else:
                        self.authority.witness[gap] = "changed"
                    result = self.call(request, reconcile=True)
                    self.assertEqual(result["status"], "UNKNOWN")
                    self.assertTrue(self.authority.guard_closed)
                    self.assertEqual(self.pointer.read_bytes(), before)
                    self.assertEqual(len(self.provider.moves), 1)

    def test_fresh_released_admission_reverifies_without_move(self):
        self.provision()
        self.authority.discoverable = True
        request = self.first()
        self.call(request)
        before = self.pointer.read_bytes()
        self.authority.witness["session_id"] = "fresh-session"
        self.assertEqual(self.call(request, reconcile=True)["status"], "UNKNOWN")
        self.authority.allow_recovery = True
        self.assertEqual(self.call(request, reconcile=True)["discovery"], "VERIFIED")
        self.assertTrue(self.authority.revalidations)
        self.assertFalse(self.authority.guard_closed)
        self.assertEqual(self.pointer.read_bytes(), before)
        self.assertEqual(len(self.provider.moves), 1)

    def fixture_operation(self, kind):
        if kind == "first":
            return self.first()
        if kind == "absent":
            return self.inverse()
        self.authority.discoverable = True
        self.call(self.first())
        upgrade = self.request(
            "upgrade", "activate", {"tag": "TARGET_TREE", "staged": self.stage("staged-B", b"B")},
            {"path": str(self.active), **switch.tree(self.active)}, 1,
            self.stage("backup-N", b"N"))
        if kind == "upgrade":
            return upgrade
        self.call(upgrade)
        restored = self.stage("restore-N", b"N")
        self.authority.restore_manifest = restored["manifest_sha256"]
        return self.request(
            "restore", "restore", {"tag": "TARGET_TREE", "staged": restored},
            {"path": str(self.active), **switch.tree(self.active)}, 2,
            self.stage("backup-B", b"B"))

    def kill_at(self, request, cut, spy):
        receive, send = os.pipe()
        child = os.fork()
        if child == 0:
            os.close(receive)
            try:
                def checkpoint(cut_name):
                    if cut_name == "after_CAS":
                        spy("CAS")
                    if cut_name == cut:
                        os.write(send, b"R")
                        signal.pause()

                switch.execute(request, switch.digest(switch.encoded(request)),
                               self.authority, self.provider, checkpoint)
            finally:
                os._exit(91)
        os.close(send)
        try:
            self.assertTrue(select.select([receive], [], [], 10)[0], "child did not reach barrier")
            self.assertEqual(os.read(receive, 1), b"R")
        finally:
            os.close(receive)
            os.kill(child, signal.SIGKILL)
            _, status = os.waitpid(child, 0)
        self.assertTrue(os.WIFSIGNALED(status))
        self.assertEqual(os.WTERMSIG(status), signal.SIGKILL)

    @unittest.skipUnless(hasattr(os, "fork"), "Linux disposable kill fixture")
    def test_real_process_kill_fixture_for_every_applicable_cut(self):
        operations = {"first": ("ABSENT_TO_N",), "upgrade": ("O_TO_ABSENT", "ABSENT_TO_N"),
                      "tree": ("O_TO_ABSENT", "ABSENT_TO_N"), "absent": ("N_TO_HOLDING",)}
        for kind, phases in operations.items():
            cuts = ["before_CAS", "after_CAS", "after_receipt", "before_fence_release"]
            cuts += [prefix + phase for phase in phases for prefix in ("before_", "after_", "journaled_")]
            for cut in cuts:
                with self.subTest(kind=kind, cut=cut):
                    self.provision()
                    request = self.fixture_operation(kind)
                    self.authority.discoverable = True
                    log = self.root / "independent-spy"
                    move = self.provider.move

                    def spy(action):
                        with log.open("a", encoding="ascii") as handle:
                            handle.write(action + "\n")
                            handle.flush()
                            os.fsync(handle.fileno())

                    def observe_move(source, destination, operation):
                        move(source, destination, operation)
                        spy("MOVE")

                    self.provider.move = observe_move
                    self.kill_at(request, cut, spy)

                    def recovery_cut(name):
                        if name == "after_CAS":
                            spy("CAS")

                    envelope = dict(request_version=switch.REQUEST, action="reconcile",
                                    original=request, operation_id=request["operation_id"],
                                    operation_fingerprint=request["operation_fingerprint"])
                    raw_sha = switch.digest(switch.encoded(envelope))
                    result = switch.execute(envelope, raw_sha, self.authority, self.provider, recovery_cut)
                    self.assertEqual(result["discovery"], "VERIFIED")
                    committed = self.pointer.read_bytes()
                    switch.execute(envelope, raw_sha, self.authority, self.provider, recovery_cut)
                    self.assertEqual(self.pointer.read_bytes(), committed)
                    actions = log.read_text().splitlines()
                    self.assertEqual(actions.count("MOVE"), len(phases))
                    self.assertEqual(actions.count("CAS"), 1)
                    self.assertEqual((self.root / "unrelated").read_bytes(), b"untouched")
                    self.assertFalse(self.fence.exists())
                    if kind == "absent":
                        self.assertFalse(self.active.exists())
                        self.assertEqual((Path(request["holding"]) / "SKILL.md").read_bytes(), b"N")
                    elif "backup" in request:
                        self.assertEqual(switch.tree(Path(request["backup"]["path"]))["identity"],
                                         request["backup"]["identity"])

    @unittest.skipUnless(hasattr(os, "fork"), "Linux disposable kill fixture")
    def test_kill_after_first_failed_discovery_does_not_admit_inverse(self):
        self.provision()
        request = self.first()
        self.kill_at(request, "after_failed_discovery", lambda _: None)
        before = self.pointer.read_bytes()
        result = self.call(request, reconcile=True)
        self.assertEqual(result["discovery"], "PENDING_FENCED")
        self.assertEqual(self.pointer.read_bytes(), before)
        self.assertTrue(self.active.is_dir())
        self.assertTrue(self.fence.exists())
        self.assertEqual(len(list((self.root / "journal").glob("*.intent.json"))), 1)

    def test_restore_tree_retains_replaced_tree_and_separate_backup(self):
        self.provision()
        self.authority.discoverable = True
        self.call(self.first())
        new = self.stage("staged-B", b"B")
        prior = {"path": str(self.active), **switch.tree(self.active)}
        upgrade = self.request("upgrade", "activate", {"tag": "TARGET_TREE", "staged": new},
                               prior, 1, self.stage("backup-N", b"N"))
        self.authority.discoverable = False
        self.call(upgrade)
        self.assertTrue(self.fence.exists())
        previous = self.stage("restore-N", b"N")
        self.authority.restore_manifest = previous["manifest_sha256"]
        backup = self.stage("backup-B", b"B")
        restore = self.request("restore", "restore", {"tag": "TARGET_TREE", "staged": previous},
                               {"path": str(self.active), **switch.tree(self.active)}, 2, backup)
        self.authority.discoverable = True
        self.assertEqual(self.call(restore)["discovery"], "VERIFIED")
        self.assertEqual((self.active / "SKILL.md").read_bytes(), b"N")
        self.assertEqual((Path(restore["holding"]) / "SKILL.md").read_bytes(), b"B")
        self.assertEqual((Path(backup["path"]) / "SKILL.md").read_bytes(), b"B")


if __name__ == "__main__":
    unittest.main()
