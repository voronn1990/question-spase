"""Disposable controls for the protected delivery coordinator."""

import argparse
import base64
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_qs

qs = test_qs.qs
import skill_generation_switch as switch
import switch_admission as admission


class Interrupted(RuntimeError):
    pass


class DeliveryBootstrapTest(unittest.TestCase):
    def setUp(self):
        fixture = test_qs.QsTest()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        self.fixture = fixture
        self.root = fixture.base
        # Two genuine Linux filesystems model the metadata/physical separation.
        physical = tempfile.TemporaryDirectory(prefix="qs-delivery-test-", dir="/dev/shm")
        self.addCleanup(physical.cleanup)
        self.physical = Path(physical.name)
        loader = self.physical / "loader"
        loader.mkdir()
        active = loader / "question-space"
        active.mkdir()
        (active / "SKILL.md").write_bytes(b"Legacy 4.2.2")
        (active / "release.yaml").write_bytes(b"4.2.2")
        legacy_sha = self.package(active, "4.2.2")
        (active / "README.md").write_bytes(b"Old README outside legacy manifest")
        (active / "extra.pyc").write_bytes(b"opaque installed bytes")
        backup = self.physical / "backup"
        shutil.copytree(active, backup)
        metadata = self.root / "metadata"
        metadata.mkdir()
        (metadata / "delivery-anchor.lock").touch()
        journal = metadata / "journal"
        journal.mkdir()
        paths = {"active": str(active), "journal": str(journal), "pointer": str(metadata/"pointer"),
                 "fence": str(metadata/"fence")}
        self.pointer = Path(paths["pointer"])
        self.journal = journal
        (self.root/"owner.txt").write_bytes(b"TEST_ONLY covering authority")
        self.owner = self.ref(self.root/"owner.txt")
        self.witness = {"boot_id": "boot", "volume_guid": "volume", "volume_serial": 7, "session_id": "first"}
        self.visible_witness = self.witness
        self.events = []
        self.provider = SimpleNamespace(name=switch.WINDOWS, volume="volume",
                                        qualify=lambda paths, op: self.events.append(("qualify", op)),
                                        move=lambda *_: self.fail("legacy adoption attempted a move"))

        self.retrieved = self.root/"retrieved"
        self.retrieved.mkdir()
        for name, content in (("README.md", b"Reviewed README"), ("SKILL.md", b"candidate"),
                              ("release.yaml", b"candidate version")):
            (self.retrieved/name).write_bytes(content)
        (self.retrieved/"history/4.2.2").mkdir(parents=True)
        shutil.copyfile(active/"manifest.json", self.retrieved/"history/4.2.2/manifest.json")
        candidate_sha = self.package(self.retrieved, "4.3.0")
        (self.retrieved/"SKILL.md").chmod(0o755)

        self.root_agent = "/root/delivery"
        self.stock_source = {"root": str(self.root/"sealed"), "run_id": "SEALED", "release_sha256": "d"*64}
        self.stock_subject = {"input_snapshot": "a"*64, "plan_sha256": "b"*64, "graph_sha256": "c"*64,
                              "authority_result_id": "S-000-R1", "authority_sha256": "e"*64,
                              "source_revision": "f"*64, "stages": {}}
        self.launch = {**paths, "mode": "delivery", "root_agent_id": self.root_agent,
                       "runtime_manifest": str(self.root/"runtime/manifest.json"), "runtime_sha256": "1"*64,
                       "candidate_manifest": str(self.retrieved/"manifest.json"), "candidate_sha256": candidate_sha}
        self.effect_scope = {"profile": "owner", "active": str(active), "assignment_ids": ["A-Occurrence"],
                             "resource_keys": [], "sole_writer_paths": []}
        self.evidence("Occurrence", "occurrence_fixture", {"test_only": True}, occurrence="install")
        predecessors = {}
        for check in sorted(admission.CHECKS):
            payload = {"check": check, "verdict": "PASS", "candidate_sha256": candidate_sha, "receipt": self.owner}
            if check == "C04":
                payload["output"] = {"path": str(self.root), "sha256": "3"*64}
            if check == "C05":
                payload["output"] = {"id": "OUTPUT-QSSELF-05", "review": self.owner,
                                     "readme_sha256": switch.digest((self.retrieved/"README.md").read_bytes())}
            predecessors[check] = self.evidence(check, "switch_predecessor", payload)
        delivery = {"owner": self.owner, "repository": admission.REPOSITORY, "ref": "refs/heads/main",
                    "metadata_root": str(metadata), "source": self.stock_source}
        self.handoff = {**delivery, "version": admission.DELIVERY, "sealed": self.stock_subject,
                        "candidate_sha256": candidate_sha, "scope": "skill", "logical_occurrence_id": "install",
                        "effect_scope": self.effect_scope, "paths": paths, "autoload_roots": [str(loader)],
                        "predecessors": predecessors}
        self.launch["delivery"] = {**delivery, "handoff": self.evidence(
            "Handoff", "switch_delivery_handoff", self.handoff, self.root_agent)}
        (self.root/"runtime").mkdir()
        runtime_raw = switch.encoded({"members": {}, "provider": {
            "id": switch.WINDOWS, "recovery_scope": switch.WINDOWS_SCOPE}})
        Path(self.launch["runtime_manifest"]).write_bytes(runtime_raw)
        (self.root/"switch-runtime.json").write_bytes(runtime_raw)
        self.launch["runtime_sha256"] = switch.digest(runtime_raw)
        self.request = {**paths, "request_version": admission.BASELINE,
                        "operation_id": "adopt", "logical_occurrence_id": "install",
                        "action": "adopt_legacy", "expected_generation": 0, "target_generation": 1,
                        "expected_pointer_sha256": switch.ABSENT, "scope": "skill", "profile": "owner",
                        "provider": switch.WINDOWS, "recovery_scope": switch.WINDOWS_SCOPE,
                        "continuity": self.witness, "runtime": self.launch["runtime_sha256"],
                        "prior": self.bound(active), "holding": str(self.physical/"holding"),
                        "backup": self.bound(backup), "legacy_manifest_sha256": legacy_sha,
                        "run_lock": str(fixture.run/"state/coordinator.lock"),
                        "occurrence_lock": str(fixture.run/"state/occurrences"/
                                               qs.sha(b"install")/"state/coordinator.lock")}
        self.request["operation_fingerprint"] = switch.fingerprint(self.request)
        self.raw = switch.digest(switch.encoded(self.request))
        self.grant = {**paths, "adoption": admission.ADOPTION, "mode": "delivery",
                      "input_snapshot": fixture.init["input_snapshot"],
                      **{key: self.stock_subject[key] for key in
                         ("authority_result_id", "authority_sha256", "graph_sha256", "plan_sha256")},
                      "request_sha256": self.raw, "effects": ["install"], "autoload_roots": [str(loader)],
                      "publication": None, "restore_source": None, "recovery": None, "discovery": None,
                      "predecessors": predecessors, "bootstrap_recovery": None,
                      "legacy": self.evidence("Old", "switch_legacy", {
                          "request_sha256": self.raw, "prior": self.request["prior"], "backup": self.request["backup"],
                          "legacy_manifest_sha256": legacy_sha, "authoritative_pointer": switch.ABSENT})}
        self.make_grant("Initial")

        def sealed(_qs, source, *_):
            switch.require(source == self.stock_source, "wrong test sealed source")
            return Path(source["root"]), self.stock_subject, {"stages": []}, []

        holder = patch.object(admission, "sealed_subject", side_effect=sealed)
        holder.start()
        self.addCleanup(holder.stop)
        import switch_output
        output = patch.object(switch_output, "verify", return_value="3"*64)
        output.start()
        self.addCleanup(output.stop)

    @staticmethod
    def package(root, version):
        entries = {p.relative_to(root).as_posix(): switch.digest(p.read_bytes())
                   for p in root.rglob("*") if p.is_file()}
        raw = switch.encoded({"entries": entries, "kind": "skill", "format": "question-space-portable-package-v1",
                              "metadata": {"name": "question-space", "version": version}})
        (root/"manifest.json").write_bytes(raw)
        return switch.digest(raw)

    @staticmethod
    def ref(path):
        return {"path": str(path), "sha256": switch.digest(path.read_bytes())}

    @staticmethod
    def bound(path):
        return {"path": str(path), **switch.tree(path)}

    def evidence(self, name, kind, payload, agent="/root/observer", *, occurrence=None):
        doc = self.fixture.common("E-"+name, "P-05", agent)
        doc.update(knowledge_status="OBSERVED", changed="UNCHANGED", artifact_type="EVIDENCE",
                   assignment_id="A-"+name, attempt_id="TRY-1", **{kind: switch.encoded(payload).decode()})
        assignment = {key: doc[key] for key in ("input_snapshot", "assignment_id", "attempt_id", "artifact_type", "agent_id")}
        assignment.update(result_id=doc["id"], output_path="evidence/"+doc["id"]+".yaml",
                          dependency_hashes=[], role_id="P-05")
        if occurrence is not None:
            assignment.update(logical_occurrence_id=occurrence, launch_state="READY_TO_DISPATCH")
        a, r = (self.root/f"{name}-{suffix}.yaml" for suffix in ("assignment", "result"))
        a.write_bytes(qs.canon_yaml(assignment))
        r.write_bytes(qs.canon_yaml(doc))
        argv = ("--root", str(self.fixture.workspace), "--run-id", self.fixture.run.name, "--assignment", str(a))
        self.fixture.call("assign", *argv)
        receipt = self.fixture.call("commit", *argv, "--result", str(r))
        return {"id": doc["id"], "sha256": receipt["content_hash"]}

    def make_grant(self, name, *, request=None, **extra):
        request = request or self.request
        raw = switch.digest(switch.encoded(request))
        scope_paths = [request["active"], request["holding"], request["backup"]["path"]]
        if "target" in request:
            scope_paths.append(request["target"]["staged"]["path"])
        selector = admission.writer_scope(qs, {"sole_writer_paths": scope_paths})
        selector["assignment_ids"] = self.effect_scope["assignment_ids"]
        previous = admission.current_fences(qs.command_journal(self.fixture.run)).get(request["scope"])
        if previous:
            selector = {key: sorted(set(value) | set(previous["effect_scope"][key]))
                        for key, value in selector.items()}
        quiet = self.evidence("Quiet-"+name, "switch_quiescence", {
            "profile": request["profile"], "active": request["active"], "request_sha256": raw,
            "cached_writers": "QUIESCENT", "client_writers": "QUIESCENT",
            "session_id": self.visible_witness["session_id"], "effects": ["install"],
            "effect_scope_sha256": qs.sha(qs.canon_yaml(selector)),
            "source_revision": self.stock_subject["source_revision"], "source_effects": [],
            "external": {"receipt": self.owner, "effects": []}})
        self.grant_ref = self.evidence("Grant-"+name, "switch_grant", {
            **self.grant, "request_sha256": raw, "quiescence": quiet, **extra}, self.root_agent)

    def authority(self, request=None):
        request = request or self.request
        raw = switch.digest(switch.encoded(request))
        return admission.Authority(
            qs, self.fixture.run, request, raw, raw, qs.command_revision(self.fixture.run),
            request["action"], self.grant_ref, self.launch, SimpleNamespace(observe=lambda: self.visible_witness),
            self.provider)

    def test_adopt_crash_cuts_and_replay_keep_exact_tree_and_pointer(self):
        def interrupt(name):
            def checkpoint(observed):
                if observed == name:
                    raise Interrupted(name)
            return checkpoint

        before = switch.tree(Path(self.request["active"]))
        with self.assertRaisesRegex(Interrupted, "before_baseline_CAS"):
            self.authority().adopt_legacy(interrupt("before_baseline_CAS"))
        self.assertFalse(self.pointer.exists())
        history = qs.command_journal(self.fixture.run)
        self.assertEqual(admission.current_fences(history)["skill"]["status"], "SWITCH_CLOSED")
        self.assertFalse(Path(self.request["fence"]).exists())
        with self.assertRaisesRegex(Interrupted, "after_baseline_CAS"):
            self.authority().adopt_legacy(interrupt("after_baseline_CAS"))
        self.assertFalse((self.journal/"adopt.baseline-commit.json").exists())
        original = self.pointer.read_bytes()

        self.visible_witness = {**self.witness, "session_id": "renewed"}
        self.make_grant("Hold")
        with self.assertRaisesRegex(switch.Refused, "exact independently accepted recovery"):
            self.authority().adopt_legacy()
        self.assertEqual(self.pointer.read_bytes(), original)
        cut = switch.readonly_cut(self.request, self.authority().physical(), self.journal,
                                  self.pointer, Path(self.request["fence"]))
        recovery = self.evidence("Exact", "switch_bootstrap_recovery", {
            "continuity": self.visible_witness, "request_sha256": self.raw,
            "intent_sha256": switch.digest((self.journal/"adopt.baseline.json").read_bytes()),
            "proposed_pointer_sha256": switch.digest(original), "cut_sha256": switch.digest(switch.encoded(cut))})
        self.make_grant("Recovery", bootstrap_recovery=recovery)
        path = self.root/"adopt.json"
        path.write_bytes(switch.encoded(self.request))
        command = {"request": str(path), "request_sha256": self.raw, "original_sha256": self.raw,
                   "expected_revision": qs.command_revision(self.fixture.run), "grant": self.grant_ref}
        args = argparse.Namespace(root=str(self.fixture.workspace), run_id=self.fixture.run.name)
        runtime = {"members": {}, "provider": {
            "path": "/fixture/provider.exe", "sha256": "a"*64, "volume": "volume"}}
        witness = SimpleNamespace(initial=self.visible_witness, initial_error=None,
                                  observe=lambda: self.visible_witness, close=lambda: None)
        replies = []
        with patch.object(switch, "capture_runtime", return_value=runtime), \
             patch.object(switch, "WindowsMove", return_value=self.provider), \
             patch.object(admission, "NativeWitness", return_value=witness), \
             patch.object(switch, "read_message", side_effect=[command, None]), \
             patch.object(switch, "send_message", side_effect=lambda stream, value: replies.append(value)):
            self.assertEqual(admission.session(args, qs, self.launch, {"entries": {}}),
                             {"status": "SWITCH_SESSION_CLOSED"})
        self.assertEqual(replies[0], {"continuity": self.visible_witness})
        result = replies[1]["result"]
        self.assertEqual(result["status"], "LEGACY_ADOPTED_FENCED")
        self.assertEqual(result["commit"]["generation"], 1)
        self.assertEqual(self.pointer.read_bytes(), original)
        self.assertEqual(switch.decode(original)["continuity"], self.witness)
        self.assertEqual(switch.tree(Path(self.request["active"])), before)
        self.assertEqual(qs.command_journal(self.fixture.run), history)
        self.assertEqual(self.authority().adopt_legacy(), result)
        self.assertEqual(self.pointer.read_bytes(), original)
        (self.journal/"other-observation.json").write_bytes(switch.encoded({"changed": True}))
        with self.assertRaisesRegex(switch.Refused, "exact independently accepted recovery"):
            self.authority().adopt_legacy()
        self.assertEqual(self.pointer.read_bytes(), original)
        self.assertTrue(all(event == ("qualify", "adopt") for event in self.events))
        self.assertFalse(Path(self.request["holding"]).exists())

    def test_admission_rejects_wrong_profile_pointer_backup_and_alias(self):
        before = qs.command_journal(self.fixture.run)
        metadata = Path(self.launch["delivery"]["metadata_root"])
        self.assertFalse(list(metadata.glob("active-*.json")))
        with patch.object(admission, "sealed_subject",
                          side_effect=switch.Refused("delivery source has applicable UNKNOWN")):
            with self.assertRaisesRegex(switch.Refused, "delivery source has applicable UNKNOWN"):
                self.authority().adopt_legacy()
        self.assertEqual(qs.command_journal(self.fixture.run), before)
        self.assertFalse(list(metadata.glob("active-*.json")))
        self.assertEqual(list(self.journal.iterdir()), [])
        self.assertFalse(self.pointer.exists())
        self.assertFalse(Path(self.request["fence"]).exists())
        self.assertEqual(self.events, [])
        bad = {**self.request, "profile": "different"}
        bad["operation_fingerprint"] = switch.fingerprint(bad)
        self.make_grant("WrongProfile", request=bad)
        with self.assertRaisesRegex(switch.Refused, "selected switch effect scope subject differs"):
            self.authority(bad).adopt_legacy()
        self.make_grant("OriginalProfile")
        relaunch = self.authority()
        relaunch.launch = {**self.launch, "delivery": {**self.launch["delivery"],
                            "source": {**self.stock_source, "release_sha256": "7"*64}}}
        with self.assertRaisesRegex(switch.Refused, "delivery source pin differs"):
            relaunch.adopt_legacy()
        self.pointer.write_bytes(b"existing authority")
        with self.assertRaisesRegex(switch.Refused, "pointer, intent or fence conflicts"):
            self.authority().adopt_legacy()
        self.assertEqual(self.pointer.read_bytes(), b"existing authority")
        self.pointer.unlink()
        backup_extra = Path(self.request["backup"]["path"])/"extra.pyc"
        backup_extra.write_bytes(b"changed backup")
        with self.assertRaisesRegex(switch.Refused, "live cut changed"):
            self.authority().adopt_legacy()
        self.assertFalse(self.pointer.exists())
        backup_extra.write_bytes(b"opaque installed bytes")
        self.authority().adopt_legacy()
        with self.assertRaisesRegex(switch.Refused, "anchor rekeyed"):
            admission.delivery_anchor(self.launch, self.fixture.run.parent/"other-run", self.request, create=True)
        windows = {**self.request, "active": "C:/Users/Owner/Loader/question-space",
                   "prior": {**self.request["prior"], "identity": [600, 601]}}
        admission.delivery_anchor(self.launch, self.fixture.run, windows, create=True)
        alias = {**windows, "active": "/mnt/c/users/owner/loader/QUESTION-SPACE"}
        with self.assertRaisesRegex(switch.Refused, "anchor rekeyed"):
            admission.delivery_anchor(self.launch, self.fixture.run, alias, create=True)

    def publication(self):
        entries, tree = admission.product_inventory(self.retrieved, self.launch["candidate_sha256"])
        return self.evidence("Published", "switch_publication", {
            "repository": admission.REPOSITORY, "ref": "refs/heads/main", "commit": "f"*40,
            "receipt": self.owner, "retrieved_tree": str(self.retrieved),
            "candidate_sha256": self.launch["candidate_sha256"], "tree_sha256": tree["manifest_sha256"],
            "git_modes": {name: "100755" if name == "SKILL.md" else "100644" for name in entries}})

    def next_request(self, gen, source, backup, action, *, operation=None):
        operation = operation or f"switch-{gen}"
        staged = self.physical / ("staged-" + operation)
        shutil.copytree(source, staged)
        req = {key: v for key, v in self.request.items() if key != "legacy_manifest_sha256"}
        req.update(request_version=switch.REQUEST, action=action, operation_id=operation,
                   expected_generation=gen-1, target_generation=gen, continuity=self.visible_witness,
                   expected_pointer_sha256=switch.digest(self.pointer.read_bytes()),
                   prior=self.bound(Path(req["active"])), backup=self.bound(backup),
                   holding=str(self.physical / ("holding-" + operation)),
                   target={"tag": "TARGET_TREE", "staged": self.bound(staged)})
        req["operation_fingerprint"] = switch.fingerprint(req)
        return req

    def execute_switch(self, request, coordinator=None):
        coordinator = coordinator or self.authority(request)

        class FixtureTransport(switch.RemoteAuthority):
            def command_cut(self):
                return coordinator.command_cut()

            def call(self, method, *args):
                return coordinator.dispatch(switch.decode(switch.encoded({"method": method, "args": args})))

        transport = FixtureTransport(None)
        provider = switch.RemoteWindowsMove(transport, {
            "path": "/fixture/provider.exe", "sha256": "a"*64, "volume": self.provider.volume})
        with patch.object(switch, "verify_runtime"):
            return switch.execute(request, coordinator.raw_sha, transport, provider,
                                  runtime_manifest=("TEST_ONLY", request["runtime"]))

    def discovery(self, request):
        return self.evidence("Observed-"+request["operation_id"], "switch_discovery", {
            "profile": request["profile"], "operation_id": request["operation_id"],
            "pointer_sha256": switch.digest(switch.encoded(switch.pointer_value(request))),
            "generation": request["target_generation"], "fresh": True, "success": True,
            "same_name_count": 1, "selection": {**request["target"]["staged"], "path": request["active"]},
            "client": "disposable fixture", "surfaces": "disposable fixture", "observed_at": "fixture",
        })

    def restore_proof(self, request):
        return self.evidence("Restore-"+request["operation_id"], "switch_restore_source", {
            "staged": request["target"]["staged"], "prior_pointer_sha256": request["expected_pointer_sha256"],
            "request_sha256": switch.digest(switch.encoded(request))})

    def test_verified_publication_and_baseline_link_to_first_normal_fence(self):
        baseline = self.authority().adopt_legacy()
        pre = self.pointer.read_bytes()
        legacy = Path(self.request["backup"]["path"])
        early = self.next_request(2, legacy, legacy, "restore", operation="early-restore")
        self.make_grant("EarlyRestore", request=early, restore_source=self.restore_proof(early))
        journal_before = {p.name: p.read_bytes() for p in self.journal.iterdir()}
        shared_before = qs.command_journal(self.fixture.run)
        native_before = list(self.events)
        active_before = switch.tree(Path(early["active"]))
        authority = self.authority(early)
        with self.assertRaisesRegex(switch.Refused, "first delivery switch must activate"):
            self.execute_switch(early, authority)
        self.assertEqual(self.pointer.read_bytes(), pre)
        self.assertEqual({p.name: p.read_bytes() for p in self.journal.iterdir()}, journal_before)
        self.assertEqual(qs.command_journal(self.fixture.run), shared_before)
        self.assertEqual(self.events, native_before)
        self.assertEqual(switch.tree(Path(early["active"])), active_before)
        self.assertFalse(Path(early["holding"]).exists())
        self.assertFalse(Path(early["fence"]).exists())

        # Reproduces a legacy attempt that already wrote its intent before
        # calling the shared fence. Its intent cannot authorize the move either.
        intent = switch.archive(self.journal, early["operation_id"], "intent")
        switch.immutable(intent, {
            "request": early, "request_sha256": authority.raw_sha,
            "pre_pointer": base64.b64encode(pre).decode(),
            "post_pointer": switch.digest(switch.encoded(switch.pointer_value(early))),
        })
        with authority.command_cut(), self.assertRaisesRegex(switch.Refused, "first delivery switch must activate"):
            authority.check_fence_close(admission.current_fences(shared_before)["skill"])
        intent.unlink()
        self.visible_witness = {**self.witness, "session_id": "new-live"}
        request = self.next_request(2, self.retrieved, Path(self.request["backup"]["path"]), "activate")
        raw = switch.digest(switch.encoded(request))
        publication = self.publication()
        self.make_grant("FirstActivation", request=request, publication=publication)
        authority = self.authority(request)
        with authority.command_cut():
            authority.admit(request["operation_fingerprint"], raw)
            cut = switch.readonly_cut(request, authority.physical(), self.journal,
                                      self.pointer, Path(request["fence"]))
            digest = switch.digest(switch.encoded(cut))
            self.assertFalse(authority.recovery_admit(self.visible_witness, cut, digest))

        recovery = self.evidence("FreshExact", "switch_recovery", {
            "continuity": self.visible_witness, "cut_sha256": digest, "request_sha256": raw})
        self.make_grant("FirstWithRecovery", request=request, publication=publication, recovery=recovery)
        authority = self.authority(request)
        with authority.command_cut():
            authority.admit(request["operation_fingerprint"], raw)
            self.assertTrue(authority.recovery_admit(self.visible_witness, cut, digest))
            self.assertFalse(authority.recovery_admit(self.visible_witness, cut, "0"*64))
            switch.immutable(switch.archive(self.journal, request["operation_id"], "intent"), {
                "request": request, "request_sha256": raw, "pre_pointer": base64.b64encode(pre).decode(),
                "post_pointer": switch.digest(switch.encoded(switch.pointer_value(request)))})
            self.assertTrue(authority.close_guard())
        latest = admission.current_fences(qs.command_journal(self.fixture.run))["skill"]
        self.assertEqual(latest["operation_id"], request["operation_id"])
        self.assertEqual(latest["pointer_sha256"], baseline["commit"]["pointer_sha256"])
        self.assertEqual(self.pointer.read_bytes(), pre)
        self.assertFalse(Path(request["fence"]).exists())
        staged = Path(request["target"]["staged"]["path"])
        (staged/"README.md").write_bytes(b"wrong staged product")
        mismatched = {**request, "target": {"tag": "TARGET_TREE", "staged": self.bound(staged)}}
        mismatched["operation_fingerprint"] = switch.fingerprint(mismatched)
        self.make_grant("StageDrift", request=mismatched, publication=publication)
        authority = self.authority(mismatched)
        with authority.command_cut(), self.assertRaisesRegex(switch.Refused, "staged delivery differs"):
            authority.admit(mismatched["operation_fingerprint"], authority.raw_sha)
        self.assertEqual(self.pointer.read_bytes(), pre)
        self.make_grant("ModeDrift", request=request, publication=publication)

        # Independently reviewed Git mode drift also holds a replay at admission.
        (self.retrieved/"SKILL.md").chmod(0o644)
        authority = self.authority(request)
        with authority.command_cut(), self.assertRaisesRegex(switch.Refused, "Git member mode differs"):
            authority.admit(request["operation_fingerprint"], raw)
        self.assertEqual(self.pointer.read_bytes(), pre)

        # Complete the ordinary activation, then restore from the real gen2 cut.
        shutil.copyfile(self.retrieved/"README.md", staged/"README.md")
        (self.retrieved/"SKILL.md").chmod(0o755)
        self.provider.move = lambda source, destination, op: (
            source.rename(destination), self.events.append(("move", op)))
        self.make_grant("FinishActivation", request=request, publication=publication,
                        discovery=self.discovery(request))
        self.assertEqual(self.execute_switch(request)["discovery"], "VERIFIED")
        self.assertEqual(switch.decode(self.pointer.read_bytes())["generation"], 2)
        candidate_backup = self.physical/"candidate-backup"
        shutil.copytree(self.retrieved, candidate_backup)
        restore = self.next_request(3, legacy, candidate_backup, "restore")
        self.make_grant("RestoreActivated", request=restore, restore_source=self.restore_proof(restore),
                        discovery=self.discovery(restore))
        self.assertEqual(self.execute_switch(restore)["discovery"], "VERIFIED")
        self.assertEqual(switch.decode(self.pointer.read_bytes())["generation"], 3)
        self.assertEqual(switch.tree(Path(restore["active"]))["manifest_sha256"],
                         self.request["prior"]["manifest_sha256"])
        self.assertEqual([event for event in self.events if event[0] == "move"],
                         [("move", request["operation_id"])]*2 + [("move", restore["operation_id"])]*2)


class SealedEffectScopeTest(unittest.TestCase):
    def test_selected_source_scope_contributes_effects_before_delivery(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            run = base/"runs/SEALED"
            (run/"intents").mkdir(parents=True)
            (run/"state").mkdir()
            release = b"TEST_ONLY sealed release"
            (run/"release.yaml").write_bytes(release)
            source = {"root": str(base), "run_id": run.name, "release_sha256": switch.digest(release)}
            current = {"status": "SEALED", "release_sha256": source["release_sha256"], "input_snapshot": "1"*64}
            qs.save_yaml(run/"state/current.yaml", current)
            active = str(base/"target")
            source_path = str(base/"source-writer")
            handoff_path = str(base/"handoff-writer")
            request = {"scope": "install", "profile": "owner", "active": active,
                       "logical_occurrence_id": "local-install"}

            def assignment(aid, occurrence, **resources):
                pair = aid+"\0TRY-1"
                entry = {"assignment": {"assignment_id": aid, "attempt_id": "TRY-1",
                                        "logical_occurrence_id": occurrence, **resources}, "assignment_sha256": qs.sha(pair.encode())}
                qs.save_yaml(run/"intents"/(qs.sha(pair.encode()).upper()+".yaml"), entry)
                return pair, entry

            registered = dict([
                assignment("A-ROOT", "other", selection_authority="ROOT"),
                assignment("A-NAMED", "named"),
                assignment("A-RESOURCE", "source-resource", resource_keys=["SEALED-RESOURCE"]),
                assignment("A-WRITER", "source-writer", sole_writer_paths=[source_path]),
                assignment("A-HANDOFF", "handoff-writer", sole_writer_paths=[handoff_path]),
                assignment("A-PHYSICAL", "physical", sole_writer_paths=[active + "/child"]),
            ])
            state = {"registered": registered, "current_attempts": {"A-ROOT": "TRY-1"}}
            qs.save_yaml(run/"state/assignments.yaml", {"registered": sorted(registered)})
            root_doc = {}
            stages = {"S-000-R1": "S-000", **{"PLAN-"+n: "S-QSSELF-"+n for n in ("04", "05", "06")}}
            records = {rid: ({"content_hash": qs.sha(rid.encode())},
                             {"tasks": [{"id": "T-QSSELF-"+rid[-2:], "checks": ["C-QSSELF-"+rid[-2:]]}]})
                       for rid in stages if rid != "S-000-R1"}
            records["S-000-R1"] = ({"assignment_id": "A-ROOT", "attempt_id": "TRY-1"}, root_doc)
            handoff = {"profile": "owner", "active": active, "assignment_ids": ["A-LOCAL"],
                       "resource_keys": [], "sole_writer_paths": [handoff_path]}
            declared = {**handoff, "assignment_ids": ["A-NAMED"], "resource_keys": ["SEALED-RESOURCE"],
                        "sole_writer_paths": [source_path]}
            # Leave the production sealed subject, assignment/launch-intent
            # audit, selector and delivery-state check intact. Substitute only
            # the upstream structural release/accepted-result projections.
            journal = []

            def effects():
                return admission.sealed_subject(qs, source, request, [Path(active)], handoff)[3]

            with patch.object(qs, "accepted", return_value=(state, current, {})), \
                 patch.object(qs, "verify", return_value={"status": "PASS"}), \
                 patch.object(qs, "plan_projection_for_run", return_value=(stages, {"stages": []})), \
                 patch.object(qs, "_admitted_document", side_effect=lambda _run, _state, rid, _kind: records[rid]), \
                 patch.object(qs, "_immutable_accepted_attempt_result", return_value="S-000-R1"), \
                 patch.object(qs, "architecture_projection_for_run", return_value={
                     "architecture_ref": {"graph_sha256": "2"*64}}), \
                 patch.object(qs, "command_journal", side_effect=lambda _run: journal):
                self.assertEqual(effects(), ["handoff-writer", "physical"])
                root_doc["switch_effect_scopes"] = {"different-scope": declared}
                self.assertEqual(effects(), ["handoff-writer", "physical"])

                root_doc["switch_effect_scopes"] = {"install": {**declared, "profile": "wrong"}}
                with self.assertRaisesRegex(switch.Refused, "effect scope subject differs"):
                    effects()
                root_doc["switch_effect_scopes"] = {"install": declared}
                pending = registered["A-NAMED\0TRY-1"]
                pending["launch_state"] = "DISPATCH_UNKNOWN"
                with self.assertRaisesRegex(switch.Refused, "delivery source has applicable UNKNOWN"):
                    effects()
                pending.pop("launch_state")

                receipt = {"logical_occurrence_id": "named", "effect_state": "UNKNOWN"}
                journal.append({"operation": "claim-effect", "receipt": receipt, "raw_sha256": "3"*64})
                with self.assertRaisesRegex(switch.Refused, "delivery source has applicable UNKNOWN"):
                    effects()
                journal.append({"operation": "reconcile-effect", "raw_sha256": "4"*64,
                                "receipt": {**receipt, "effect_state": "RECIPIENT_CONFIRMED"}})
                self.assertEqual(effects(), ["handoff-writer", "named", "physical",
                                             "source-resource", "source-writer"])
