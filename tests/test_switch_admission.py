"""Coordinator journal and portable-output integration controls."""

import argparse
import base64
import errno
import json
import os
import select
import signal
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_command_admission

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import qs
import skill_generation_switch as switch
import switch_admission as admission
import switch_output as output


class SwitchAdmissionTest(unittest.TestCase):
    def test_live_release_is_required_for_ordinary_applicable_claim(self):
        helper = test_command_admission.CommandAdmissionTest()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        fixture = helper.fixture
        affected = "OCC-A.V2"
        disjoint = "OCC-DISJOINT"
        assignment, _ = helper.pending(occurrence=affected)
        other, _ = helper.pending(label="OTHER", occurrence=disjoint)
        adapter = qs._JournalAdmission(fixture.run)
        occurrence_lock = qs.sha(affected.encode())
        witness = {"boot_id": "fixture", "volume_guid": "fixture-volume",
                   "volume_serial": 1, "session_id": "test-session"}
        fence = {"scope": "test", "effects": [affected], "logical_occurrence_id": affected,
                 "operation_id": "switch-1", "fingerprint": "a" * 64, "pointer_sha256": "b" * 64,
                 "provider": switch.WINDOWS, "recovery_scope": switch.WINDOWS_SCOPE,
                 "continuity": witness, "adoption": admission.ADOPTION,
                 "effect_scope": {"assignment_ids": [], "resource_keys": ["SWITCH"],
                                  "sole_writer_paths": ["fixture/loader"]}}

        def journal_fence(status):
            with qs.command_cut(adapter.lock(), adapter.occurrence_lock(occurrence_lock)):
                view = adapter.read_committed_locked()
                raw = qs.canon_yaml({**fence, "status": status,
                                     "continuity": switch.encoded(witness).decode()})
                command = qs.Request(admission.FENCE, status + str(len(view[3])), "/root",
                                     "c" * 64, fixture.run.name,
                                     view[1]["input_snapshot"], qs.command_revision(fixture.run, view[3]),
                                     raw, occurrence_lock)
                adapter.commit_locked(view, command, qs.Committed(command.fingerprint(), raw), ())

        def claim(occurrence, assignment_id, source, key, *, ok):
            request, _ = helper.request(key, "/root/cut", qs.sha(source.read_bytes()),
                                        assignment_id=assignment_id, attempt_id="TRY-1",
                                        logical_occurrence_id=occurrence)
            result = fixture.call(*helper.command("claim-effect", "--request", str(request)), ok=ok)
            return request, result

        journal_fence("SWITCH_CLOSED")
        _, refused = claim(affected, "A-CUT", assignment, "closed", ok=False)
        self.assertEqual(refused["error"], "switch_effect_scope_fenced")
        _, accepted = claim(disjoint, "A-OTHER", other, "disjoint", ok=True)
        self.assertEqual(accepted["effect_state"], "UNKNOWN")
        late, _, late_binding = helper.resource_assignment("LATE", resource_keys=["SWITCH"])
        journal_fence("SWITCH_RELEASED")
        late_request, _ = helper.request("late", "/root/cut", qs.sha(late.read_bytes()), **late_binding)
        late_args = argparse.Namespace(command="claim-effect", root=str(fixture.workspace),
                                       run_id=fixture.run.name, request=str(late_request))
        before = helper.resource_journal_bytes()
        with self.assertRaisesRegex(switch.Refused, "switch_effect_scope_fenced"):
            qs.journal_command(late_args, switch_guard=lambda _: True)
        self.assertEqual(helper.resource_journal_bytes(), before)
        request, refused = claim(affected, "A-CUT", assignment, "released", ok=False)
        self.assertEqual(refused["error"], "switch_effect_scope_fenced")
        args = argparse.Namespace(command="claim-effect", root=str(fixture.workspace),
                                  run_id=fixture.run.name, request=str(request))
        sent = []
        with self.assertRaisesRegex(switch.Refused, "switch_effect_scope_fenced"):
            qs.journal_command(args, sent.append, switch_guard=lambda _: False)
        self.assertFalse(sent)
        receipt = qs.journal_command(args, sent.append, switch_guard=lambda row: row["continuity"] == witness)
        self.assertEqual(sent, [receipt])
        journal_fence("SWITCH_CLOSED")
        before = helper.resource_journal_bytes()
        self.assertEqual(qs.journal_command(args, sent.append), receipt)
        replay = fixture.call(*helper.command("claim-effect", "--request", str(request), "--external-dispatch"))
        self.assertEqual(replay["dispatch_action"], "HOLD_RECONCILE")
        self.assertEqual(helper.resource_journal_bytes(), before)
        self.assertEqual(sent, [receipt])

    def test_portable_output_rejects_rehashed_profile_mix_and_member_drift(self):
        subject = {"run_id": "R", "input_snapshot": "a" * 64, "plan": {},
                   "task_id": "T", "check_id": "C"}
        files = {output.ENTRY: b"entry", output.SUPERVISOR: b"supervisor"}
        descriptor = {"id": "OUTPUT-QSSELF-04", "version": 3, "provider": switch.WINDOWS,
                      "recovery_scope": switch.WINDOWS_SCOPE, "design_sha256": output.DESIGN,
                      "review_sha256": output.REVIEW, "adoption_sha256": output.ADOPTION,
                      "selected_subject": subject}

        def bind(suffix, label, provider, scope, members):
            runtime = {"provider": {"id": provider, "recovery_scope": scope},
                       "members": {name: switch.digest(files[name]) for name in members},
                       "interpreter": {"path": "/opt/python", "version": "3.12.0", "sha256": "c" * 64}}
            if provider == switch.WINDOWS:
                runtime["supervisor_interpreter"] = dict(runtime["interpreter"])
                runtime["provider"].update(path="/mnt/c/Windows/provider.exe", sha256="c" * 64,
                                           volume="disposable-volume")
            runtime_name = "switch" + suffix + "-runtime.json"
            files[runtime_name] = switch.encoded(runtime)
            runtime_sha = switch.digest(files[runtime_name])
            request = {"request_version": switch.REQUEST, "runtime": runtime_sha,
                       "provider": provider, "recovery_scope": scope, "action": "activate",
                       "operation_id": "first", "logical_occurrence_id": "occurrence",
                       "scope": "test", "profile": "test", "expected_generation": 0,
                       "expected_pointer_sha256": switch.ABSENT, "target_generation": 1,
                       "continuity": {"boot_id": "boot", "volume_guid": "volume",
                                      "volume_serial": 1, "session_id": "session"},
                       "prior": None, "target": {"tag": "TARGET_TREE",
                                                "staged": {"path": "/fixture/staged", "identity": [1, 2],
                                                           "manifest_sha256": "c" * 64}}}
            request.update({name: "/fixture/" + name for name in
                            ("active", "holding", "journal", "pointer", "fence", "occurrence_lock",
                             "run_lock")})
            request["operation_fingerprint"] = switch.fingerprint(request)
            valid = {**request, "logical_occurrence_id": "OCC-A.V2"}
            valid["operation_fingerprint"] = switch.fingerprint(valid)
            if (provider, scope) in ((switch.WINDOWS, switch.WINDOWS_SCOPE),
                                     (switch.FIXTURE, switch.FIXTURE_SCOPE)):
                self.assertEqual(switch.validate(valid, check_paths=False)["logical_occurrence_id"], "OCC-A.V2")
                for key, bad_id in (("logical_occurrence_id", "bad/id"), ("operation_id", "switch.v2")):
                    invalid = {**request, key: bad_id}
                    invalid["operation_fingerprint"] = switch.fingerprint(invalid)
                    with self.assertRaises(switch.Refused):
                        switch.validate(invalid, check_paths=False)
            request_name = "switch" + suffix + "-request.json"
            files[request_name] = switch.encoded(request)
            descriptor[label + "runtime_sha256"] = runtime_sha
            descriptor[label + "request_sha256"] = switch.digest(files[request_name])
            files[output.OUTPUT] = switch.encoded(descriptor)

        bind("", "", switch.WINDOWS, switch.WINDOWS_SCOPE, {output.ENTRY, output.SUPERVISOR})
        bind("-fixture", "fixture_", switch.FIXTURE, switch.FIXTURE_SCOPE, {output.ENTRY})
        self.assertEqual(output.verify(files.__getitem__, subject), switch.digest(files[output.OUTPUT]))
        with self.assertRaisesRegex(switch.Refused, "subject binding differs"):
            output.verify(files.__getitem__, {**subject, "task_id": "T-OTHER"})
        files[output.SUPERVISOR] += b"drift"
        with self.assertRaisesRegex(switch.Refused, "member hash differs"):
            output.verify(files.__getitem__, subject)
        bind("", "", switch.WINDOWS, switch.FIXTURE_SCOPE, {output.ENTRY, output.SUPERVISOR})
        with self.assertRaisesRegex(switch.Refused, "runtime/request binding differs"):
            output.verify(files.__getitem__, subject)


class SwitchAuthorityScopeTest(unittest.TestCase):
    def scope_fixture(self, *, local=False):
        helper = test_command_admission.CommandAdmissionTest()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        fixture = helper.fixture
        run = fixture.run
        base = fixture.base
        snapshot = fixture.init["input_snapshot"]
        case = base / "qs-switch-qualification-fixture"
        physical_root = case if local else Path("/mnt/c/Users/Owner")
        active = physical_root / "Loader" / "Question-Space"
        holding, staged, backup = (physical_root/"Protected"/name for name in ("Holding", "Staging", "Backup"))
        if local:
            active.parent.mkdir(parents=True)
            holding.parent.mkdir()
        authority_agent, observer = "/root/switch", "/root/independent"
        original_sha, graph_sha = "2" * 64, "3" * 64
        anchor, related, disjoint = "OCC-A.V2", "OCC-B", "OCC-C"
        scope = {"profile": "fixture", "active": str(active), "assignment_ids": ["A-ANCHOR"],
                 "resource_keys": [], "sole_writer_paths": []}
        helper.pending(label="ANCHOR", occurrence=anchor)
        aliases = {
            "B": r"c:\users\OWNER\loader\QUESTION-SPACE\child",
            "HOLD": "/mnt/c/users/owner/PROTECTED/HOLDING/child",
            "STAGED": r"C:\USERS\owner\protected\staging",
            "BACKUP": r"c:\users\owner\PROTECTED\BACKUP",
        }
        if local:
            aliases = dict(zip(aliases, map(str, (active, holding, staged, backup))))
        b_source, _, b_binding = helper.resource_assignment("B", sole_writer_paths=[aliases["B"]])
        for label in ("HOLD", "STAGED", "BACKUP"):
            helper.resource_assignment(label, sole_writer_paths=[aliases[label]])
        helper.pending(label="OUTSIDE", occurrence=disjoint)

        plan = fixture.common("PLAN-ROOT", "PLAN-AUTHOR", authority_agent)
        plan.update(plan_version="fixture", identity_result={"stage_id": "S-000"},
                    local_execution_graph={"entry": "START", "exit": "END", "edges": []},
                    tasks=[], switch_effect_scopes={"fixture": scope})
        fixture.register_and_accept("ROOT", "PLAN-AUTHOR", "PLAN", "plan/PLAN-ROOT.yaml",
                                    plan, authority_agent, root_assignment=True)
        state, current, _ = qs.accepted(run)
        _, projection = qs.plan_projection_for_run(run, state, current)
        plan_sha = qs.sha(qs.canon_yaml(projection))
        # This fixture binds the exact real plan document and supplies only the
        # selected projection metadata and graph observation of a delivery run.
        qs.save_yaml(run / "state/current.yaml",
                     {**current, "current_plan_projection": projection,
                      "current_plan_projection_sha256": plan_sha})
        root_assignment_sha = state["registered"]["A-ROOT\0TRY-1"]["assignment_sha256"]

        old_manifest = base / "old-manifest.json"
        new_manifest = base / "new-manifest.json"
        for manifest, source in ((old_manifest, b"old"), (new_manifest, b"new")):
            manifest.write_bytes(switch.encoded({"entries": {"scripts/qs.py": qs.sha(source)}}))
        old_sha = qs.sha(old_manifest.read_bytes())
        candidate_sha = qs.sha(new_manifest.read_bytes())

        def evidence(identifier, kind, payload, agent=observer):
            document = fixture.common(identifier, "P-05", agent)
            document.update(knowledge_status="OBSERVED", changed="UNCHANGED",
                            **{kind: switch.encoded(payload).decode("utf-8")})
            receipt = fixture.register_and_accept(identifier, "P-05", "EVIDENCE",
                                                  "evidence/"+identifier+".yaml", document, agent)
            return {"id": identifier, "sha256": receipt["content_hash"]}

        predecessors = {
            check: evidence("E-"+check, "switch_predecessor",
                            {"check": check, "verdict": "PASS", "candidate_sha256": candidate_sha})
            for check in ("C01", "C02", "C03")
        }
        old_c03 = evidence("E-OLD-C03", "switch_predecessor",
                           {"check": "C03", "verdict": "PASS", "candidate_sha256": old_sha})
        paths = {"active": str(active), "journal": str(base/"journal"),
                 "pointer": str(base/"pointer"), "fence": str(base/"fence")}
        launch = {**paths, "mode": "qualification", "root_agent_id": authority_agent,
                  "candidate_sha256": candidate_sha, "runtime_manifest": str(base/"runtime/manifest.json"),
                  "qualification_root": str(case)}
        witness = {"boot_id": "fixture", "volume_guid": "volume", "volume_serial": 1,
                   "session_id": "fixture-session"}
        tree = {"path": str(staged), "identity": [1, 2], "manifest_sha256": "c" * 64}
        request = {**paths, "request_version": switch.REQUEST, "runtime": "a" * 64,
                   "operation_id": "operation",
                   "logical_occurrence_id": anchor, "scope": "fixture", "profile": "fixture",
                   "provider": switch.FIXTURE if local else switch.WINDOWS,
                   "recovery_scope": switch.FIXTURE_SCOPE if local else switch.WINDOWS_SCOPE,
                   "continuity": witness, "action": "activate", "expected_generation": 1,
                   "expected_pointer_sha256": "b" * 64, "target_generation": 2,
                   "prior": {**tree, "path": str(active), "identity": [3, 4]},
                   "run_lock": str(run/"state/coordinator.lock"),
                   "occurrence_lock": str(run/"state/occurrences"/qs.sha(anchor.encode())/"state/coordinator.lock"),
                   "holding": str(holding), "target": {"tag": "TARGET_TREE", "staged": tree},
                   "backup": {**tree, "path": str(backup), "identity": [5, 6]}}
        request["operation_fingerprint"] = switch.fingerprint(request)
        predecessor = switch.encoded({**switch.pointer_value(request),
                                      "installed": request["prior"], "generation": 1,
                                      "operation_id": "predecessor", "operation_fingerprint": "a" * 64})
        request["expected_pointer_sha256"] = switch.digest(predecessor)
        request["operation_fingerprint"] = switch.fingerprint(request)
        switch.validate(request, check_paths=False)
        selector = {"assignment_ids": ["A-ANCHOR"], "resource_keys": [], "sole_writer_paths": [
            "mnt/c/users/owner/loader/question-space", "mnt/c/users/owner/protected/backup",
            "mnt/c/users/owner/protected/holding", "mnt/c/users/owner/protected/staging",
        ]}
        if local:
            selector["sole_writer_paths"] = sorted(str(path).lstrip("/") for path in (active, holding, staged, backup))
        initial_effects = [anchor, related, "OCC-BACKUP", "OCC-HOLD", "OCC-STAGED"]
        Path(paths["journal"]).mkdir()
        quiet_payload = {
            "profile": "fixture", "active": str(active), "request_sha256": original_sha,
            "cached_writers": "QUIESCENT", "client_writers": "QUIESCENT", "session_id": "fixture-session",
            "effects": initial_effects, "effect_scope_sha256": qs.sha(qs.canon_yaml(selector))}
        quiescence = evidence("E-QUIET", "switch_quiescence", quiet_payload)
        wrong_quiescence = evidence("E-QUIET-WRONG", "switch_quiescence",
                                    {**quiet_payload, "effects": [anchor]})
        grant_payload = {**paths, "adoption": admission.ADOPTION, "mode": "qualification",
                         "input_snapshot": snapshot, "plan_sha256": plan_sha, "graph_sha256": graph_sha,
                         "authority_result_id": "PLAN-ROOT", "authority_sha256": root_assignment_sha,
                         "request_sha256": original_sha, "quiescence": quiescence, "restore_source": None,
                         "recovery": None, "discovery": None, "predecessors": predecessors,
                         "effects": initial_effects, "autoload_roots": [str(active.parent)]}
        full = evidence("E-FULL", "switch_grant", grant_payload, authority_agent)
        missing = evidence("E-MISSING", "switch_grant", {**grant_payload, "effects": [anchor]}, authority_agent)
        wrong_quiet = evidence("E-WRONG-QUIET", "switch_grant",
                               {**grant_payload, "quiescence": wrong_quiescence}, authority_agent)
        stale = evidence("E-STALE", "switch_grant",
                         {**grant_payload, "predecessors": {**predecessors, "C03": old_c03}},
                         authority_agent)

        def authority_for(ref, req=request, envelope="activate", raw=original_sha, original=original_sha):
            authority = admission.Authority(
                qs, run, req, raw, original, qs.command_revision(run),
                envelope, ref, launch, None, None)
            return authority

        def graph():
            return patch.object(qs, "architecture_projection_for_run",
                                return_value={"architecture_ref": {
                                    "graph_sha256": graph_sha, "synthesis_id": "SYN-FIXTURE"}})

        return (helper, request, predecessor, selector, initial_effects, authority_for, graph, evidence,
                quiet_payload, grant_payload, full, missing, wrong_quiet, stale, b_source, b_binding)

    def test_windows_aliases_cover_registered_unknown_before_guard(self):
        (helper, request, _, _, initial_effects, authority_for, graph, _, _, _, full, missing,
         wrong_quiet, stale, b_source, b_binding) = self.scope_fixture()
        fixture = helper.fixture
        run = fixture.run
        anchor = request["logical_occurrence_id"]

        def admit(ref, error=None):
            before = helper.resource_journal_bytes()
            authority = authority_for(ref)
            with authority.command_cut(), graph(), patch.object(authority, "validate_paths"):
                if error:
                    with self.assertRaisesRegex(switch.Refused, error):
                        authority.admit(authority.request["operation_fingerprint"], authority.raw_sha)
                    self.assertIsNone(authority.grant)
                else:
                    self.assertTrue(authority.admit(authority.request["operation_fingerprint"], authority.raw_sha))
                    self.assertEqual(authority.effects, initial_effects)
            self.assertEqual(helper.resource_journal_bytes(), before)
            self.assertFalse(Path(request["pointer"]).exists())
            self.assertFalse(Path(request["fence"]).exists())

        def native_unknown(assignment):
            fixture.call(*helper.command("launch-intent", "--assignment-id", assignment,
                                         "--attempt-id", "TRY-1", "--state", "DISPATCH_UNKNOWN"))

        native_unknown("A-OUTSIDE")
        admit(full)
        admit(wrong_quiet, "switch quiescence evidence differs")
        admit(stale, "switch predecessor not applicable: C03")
        claim = fixture.call(*helper.resource_claim("claim-b", b_source, b_binding))
        self.assertEqual(claim["effect_state"], "UNKNOWN")
        admit(missing, "switch effect scope incomplete")
        admit(full, "switch applicable effect UNKNOWN")
        stopped = helper.resource_reconcile("PROOF-B-NOT-SENT", b_binding, effect_reconciliation={
            **b_binding, "assignment_sha256": claim["assignment_sha256"], "claim_key": "claim-b",
            "disposition": "NOT_SENT", "authoritative_receipt": "TEST_ONLY no send observed"})
        self.assertEqual(fixture.call(*stopped)["effect_state"], "NOT_SENT")
        native_unknown("A-ANCHOR")
        self.assertIsNone(qs.occurrence_effect(run, anchor))
        admit(full, "switch applicable native dispatch UNKNOWN")

    def test_released_fence_admits_late_windows_alias_after_interrupted_reconcile(self):
        (helper, request, predecessor, selector, effects, authority_for, graph, evidence, quiet, grant,
         full, _, _, _, _, _) = self.scope_fixture()
        run = helper.fixture.run
        pointer = Path(request["pointer"])
        journal = Path(request["journal"])
        witness = request["continuity"]
        post = switch.encoded(switch.pointer_value(request))
        post_sha = switch.digest(post)
        operation = request["operation_id"]
        pointer.write_bytes(predecessor)

        def latest():
            return admission.current_fences(qs.command_journal(run))[request["scope"]]

        def admit(authority):
            self.assertTrue(authority.admit(authority.request["operation_fingerprint"], authority.raw_sha))

        def confirm_release(authority):
            with patch.object(authority, "continuity", return_value=witness), \
                    patch.object(switch, "matches", return_value=True):
                self.assertTrue(authority.release_guard(witness))

        initial = authority_for(full)
        with initial.command_cut(), graph(), patch.object(initial, "validate_paths"):
            admit(initial)
            switch.archive(journal, operation, "intent").write_bytes(switch.encoded({
                "request": request, "pre_pointer": base64.b64encode(predecessor).decode(), "post_pointer": post_sha,
                "request_sha256": initial.original_sha,
            }))
            self.assertTrue(initial.close_guard())
            self.assertEqual(latest()["status"], "SWITCH_CLOSED")
            pointer.write_bytes(post)
            switch.archive(journal, operation, "commit").write_bytes(
                switch.encoded(switch.commit_value(request, post_sha)))
            switch.archive(journal, operation, "verified").write_bytes(switch.encoded({
                "profile": request["profile"], "operation_id": operation, "pointer_sha256": post_sha,
                "generation": request["target_generation"], "fresh": True, "success": True,
                "same_name_count": 1,
                "selection": {**request["target"]["staged"], "path": request["active"]},
                "client": "fixture", "surfaces": "fixture", "observed_at": "fixture",
            }))
            confirm_release(initial)
        self.assertEqual(latest()["status"], "SWITCH_RELEASED")

        late, _, binding = helper.resource_assignment(
            "LATE", sole_writer_paths=[r"c:\users\OWNER\LOADER\question-space\late"])
        sent = []

        def claim_for(key, source, subject):
            payload, _ = helper.request(key, "/root/cut", qs.sha(source.read_bytes()), **subject)
            return argparse.Namespace(command="claim-effect", root=str(helper.fixture.workspace),
                                      run_id=run.name, request=str(payload))

        def held(claim):
            before = helper.resource_journal_bytes()
            with self.assertRaisesRegex(switch.Refused, "switch_effect_scope_fenced"):
                qs.journal_command(claim, sent.append, switch_guard=lambda _: True)
            self.assertEqual(helper.resource_journal_bytes(), before)
            self.assertFalse(sent)

        held(claim_for("late", late, binding))
        expanded = sorted([*effects, binding["logical_occurrence_id"]])
        quiet_ref = evidence("E-QUIET-LATE", "switch_quiescence", {**quiet, "effects": expanded})
        full_ref = evidence("E-GRANT-LATE", "switch_grant", {
            **grant, "effects": expanded, "quiescence": quiet_ref,
        }, "/root/switch")
        stale = authority_for(full, envelope="reconcile")
        before = helper.resource_journal_bytes()
        with stale.command_cut(), graph(), patch.object(stale, "validate_paths"):
            with self.assertRaisesRegex(switch.Refused, "effect scope incomplete"):
                admit(stale)
        self.assertEqual(helper.resource_journal_bytes(), before)

        interrupted = authority_for(full_ref, envelope="reconcile")
        with interrupted.command_cut(), graph(), patch.object(interrupted, "validate_paths"):
            admit(interrupted)
            pointer.write_bytes(b"{}")
            with self.assertRaisesRegex(switch.Refused, "request lineage differs"):
                interrupted.close_guard()
            self.assertEqual(helper.resource_journal_bytes(), before)
            pointer.write_bytes(post)
            self.assertTrue(interrupted.close_guard())
        self.assertEqual(latest()["status"], "SWITCH_CLOSED")
        self.assertEqual(latest()["effects"], expanded)
        held(claim_for("late", late, binding))

        newer, _, newer_binding = helper.resource_assignment(
            "NEWER", sole_writer_paths=["/mnt/c/USERS/OWNER/Protected/Holding/newer"])
        held(claim_for("newer", newer, newer_binding))

        with interrupted.command_cut():
            with self.assertRaisesRegex(switch.Refused, "switch fence admission cut changed"):
                interrupted.close_guard()
        expanded = sorted([*expanded, newer_binding["logical_occurrence_id"]])
        resumed_quiet = evidence("E-QUIET-RESUME", "switch_quiescence", {**quiet, "effects": expanded})
        resumed_grant = evidence("E-GRANT-RESUME", "switch_grant", {
            **grant, "effects": expanded, "quiescence": resumed_quiet,
        }, "/root/switch")
        resumed = authority_for(resumed_grant, envelope="reconcile")
        with resumed.command_cut(), graph(), patch.object(resumed, "validate_paths"):
            admit(resumed)
            self.assertTrue(resumed.close_guard())
            self.assertEqual(latest()["effects"], expanded)
            confirm_release(resumed)
        self.assertEqual(latest()["status"], "SWITCH_RELEASED")
        self.assertEqual(latest()["effect_scope"], selector)

        successor = {**request, "operation_id": "successor",
                     "expected_pointer_sha256": post_sha, "expected_generation": 2, "target_generation": 3,
                     "prior": {**request["target"]["staged"], "path": request["active"]}}
        successor["operation_fingerprint"] = switch.fingerprint(successor)
        successor_sha = switch.digest(switch.encoded(successor))
        next_quiet = evidence("E-QUIET-NEXT", "switch_quiescence", {
            **quiet, "effects": expanded, "request_sha256": successor_sha,
        })
        next_grant = evidence("E-GRANT-NEXT", "switch_grant", {
            **grant, "effects": expanded, "request_sha256": successor_sha, "quiescence": next_quiet,
        }, "/root/switch")

        receipt = qs.journal_command(claim_for("late", late, binding), sent.append, switch_guard=lambda row:
                                     row["continuity"] == witness and row["pointer_sha256"] == post_sha)
        self.assertEqual(sent, [receipt])
        self.assertEqual(receipt["effect_state"], "UNKNOWN")
        stopped = helper.resource_reconcile("E-LATE-NOT-SENT", binding, effect_reconciliation={
            **binding, "assignment_sha256": receipt["assignment_sha256"], "claim_key": "late",
            "disposition": "NOT_SENT", "authoritative_receipt": "TEST_ONLY no send observed",
        })
        self.assertEqual(helper.fixture.call(*stopped)["effect_state"], "NOT_SENT")
        next_operation = authority_for(next_grant, req=successor, raw=successor_sha, original=successor_sha)
        with next_operation.command_cut(), graph(), patch.object(next_operation, "validate_paths"):
            admit(next_operation)
            switch.archive(journal, "successor", "intent").write_bytes(switch.encoded({
                "request": successor, "pre_pointer": base64.b64encode(post).decode(),
                "post_pointer": switch.digest(switch.encoded(switch.pointer_value(successor))),
                "request_sha256": successor_sha,
            }))
            self.assertTrue(next_operation.close_guard())
        self.assertEqual(latest()["operation_id"], "successor")
        self.assertEqual(latest()["status"], "SWITCH_CLOSED")
        self.assertEqual(latest()["effects"], expanded)

    def test_windows_autoload_and_disposable_checks_use_same_path_identity(self):
        (helper, seed, _, _, _, authority_for, _, _, _, grant, full, *_) = self.scope_fixture()
        root = Path("/mnt/c/Users/Owner")
        alias = root / "lOADER"
        authority = authority_for(full)
        authority.launch = {**authority.launch, "mode": "delivery"}

        # Path spellings simulate both projections. No C: path is read or written.
        with patch.object(switch, "path_at", side_effect=Path):
            authority.validate_paths(grant)
            for name in ("holding", "staged", "backup", "runtime", "journal", "pointer", "fence", "run"):
                with self.subTest(surface=name):
                    req = {**seed}
                    candidate = authority_for(full, req)
                    candidate.launch = dict(authority.launch)
                    proof = dict(grant)
                    protected = str(alias / ("Protected-" + name))
                    if name == "holding":
                        req["holding"] = protected
                    elif name == "staged":
                        req["target"] = {**seed["target"], "staged": {**seed["target"]["staged"], "path": protected}}
                    elif name == "backup":
                        req["backup"] = {**seed["backup"], "path": protected}
                    elif name == "runtime":
                        candidate.launch["runtime_manifest"] = str(Path(protected) / "manifest.json")
                    elif name == "run":
                        candidate.run = Path(protected)
                        req["run_lock"] = str(candidate.run / "state/coordinator.lock")
                        req["occurrence_lock"] = str(candidate.run / "state/occurrences" /
                                                     candidate.occurrence_key / "state/coordinator.lock")
                    else:
                        req[name] = candidate.launch[name] = proof[name] = protected
                    before = helper.resource_journal_bytes()
                    with self.assertRaisesRegex(switch.Refused, "switch surface overlaps autoload"):
                        candidate.validate_paths(proof)
                    self.assertEqual(helper.resource_journal_bytes(), before)

            nested = {**seed, "holding": str(root/"PROTECTED"/"sTAGING"/"inner")}
            candidate = authority_for(full, nested)
            candidate.launch = authority.launch
            with self.assertRaisesRegex(switch.Refused, "path alias or containment"):
                candidate.validate_paths(grant)

            disposable = root / "qs-switch-qualification-case"
            scope = disposable / "Loader"
            case_alias = root / "QS-SWITCH-QUALIFICATION-CASE"
            qualified = {**seed, "active": str(case_alias/"lOADER"/"question-space"),
                         "holding": str(disposable/"Holding"),
                         "target": {**seed["target"], "staged": {**seed["target"]["staged"],
                                                                "path": str(disposable/"Stage")}},
                         "backup": {**seed["backup"], "path": str(case_alias/"Backup")}}
            candidate = authority_for(full, qualified)
            candidate.launch = {**candidate.launch, "active": qualified["active"],
                                "qualification_root": str(disposable)}
            proof = {**grant, "active": qualified["active"], "autoload_roots": [str(scope)]}
            candidate.validate_paths(proof)
            qualified["holding"] = str(root/"qs-switch-qualification-case-other"/"Holding")
            with self.assertRaisesRegex(switch.Refused, "outside its reserved disposable namespace"):
                candidate.validate_paths(proof)

    def standalone_fixture(self, *, commit_first=True):
        (helper, seed, _, _, effects, authority_for, graph, evidence, quiet, grant,
         _, _, _, _, b_source, b_binding) = self.scope_fixture(local=True)
        run = helper.fixture.run
        active, journal, pointer, fence = map(Path, (seed[key] for key in ("active", "journal", "pointer", "fence")))
        protected = Path(seed["holding"]).parent
        spy = helper.fixture.base / "effect-spy"

        def log(event, operation):
            with spy.open("a", encoding="ascii") as handle:
                handle.write(f"{event} {operation}\n")
                handle.flush()
                os.fsync(handle.fileno())

        class Provider(switch.FixtureMove):
            def move(self, source, destination, operation):
                super().move(source, destination, operation)
                log("MOVE", operation)

        provider = Provider()
        witness = SimpleNamespace(current=seed["continuity"])
        witness.observe = lambda: witness.current

        class LocalTransport(switch.RemoteAuthority):
            def __init__(self, coordinator):
                self.coordinator = coordinator

            def command_cut(self):
                return self.coordinator.command_cut()

            def call(self, method, *args):
                # Exercise the coordinator's actual dispatch with JSON values.
                return self.coordinator.dispatch(switch.decode(switch.encoded({"method": method, "args": args})))

        class LocalProvider:
            name = switch.FIXTURE

            def __init__(self, transport):
                self.transport = transport

            def qualify(self, paths, operation):
                self.transport.call("qualify", list(map(str, paths)), operation)

            def move(self, source, destination, operation):
                self.transport.call("move", str(source), str(destination), operation)

        def staged(name, contents):
            path = protected / name
            path.mkdir()
            (path / "SKILL.md").write_bytes(contents)
            return {"path": str(path), **switch.tree(path)}

        def request(operation, generation, target):
            old = switch.record(pointer)
            prior = None if old is None else {"path": str(active), **switch.tree(active)}
            req = {key: value for key, value in seed.items() if key != "backup"}
            req.update(operation_id=operation, expected_generation=generation,
                       expected_pointer_sha256=switch.ABSENT if old is None else switch.digest(old),
                       target_generation=generation + 1, prior=prior,
                       holding=str(protected / ("held-" + operation)),
                       target={"tag": "TARGET_TREE", "staged": target})
            if prior is not None:
                req["backup"] = staged("backup-" + operation, (active / "SKILL.md").read_bytes())
            req["operation_fingerprint"] = switch.fingerprint(req)
            return req

        def grant_for(req, previous_scope=None, *, label=None):
            raw = switch.digest(switch.encoded(req))
            op = req["operation_id"]
            label = label or op
            paths = [req["active"], req["holding"], req["target"]["staged"]["path"]]
            if "backup" in req:
                paths.append(req["backup"]["path"])
            selector = {"assignment_ids": ["A-ANCHOR"], "resource_keys": [],
                        "sole_writer_paths": sorted({path.lstrip("/") for path in paths} |
                                                    set(previous_scope or []))}
            registered_paths = {
                effects[1]: seed["active"], "OCC-HOLD": seed["holding"],
                "OCC-STAGED": seed["target"]["staged"]["path"], "OCC-BACKUP": seed["backup"]["path"],
            }
            scoped = sorted([effects[0], *(occ for occ, path in registered_paths.items()
                                           if path.lstrip("/") in selector["sole_writer_paths"])])
            proof = evidence("E-Q-"+label, "switch_quiescence", {
                **quiet, "request_sha256": raw, "effects": scoped,
                "effect_scope_sha256": qs.sha(qs.canon_yaml(selector)),
            })
            discovery = evidence("E-D-"+label, "switch_discovery", {
                "profile": req["profile"], "operation_id": op,
                "pointer_sha256": switch.digest(switch.encoded(switch.pointer_value(req))),
                "generation": req["target_generation"], "fresh": True, "success": True,
                "same_name_count": 1, "selection": {**req["target"]["staged"], "path": str(active)},
                "client": "disposable fixture", "surfaces": "disposable fixture", "observed_at": "fixture",
            })
            return evidence("E-G-"+label, "switch_grant", {
                **grant, "effects": scoped, "request_sha256": raw, "quiescence": proof, "discovery": discovery,
            }, "/root/switch"), selector["sole_writer_paths"]

        def execute(req, ref, checkpoint=lambda _: None, *, reconcile=False):
            operation = req["operation_id"]
            envelope = ({"request_version": switch.REQUEST, "action": "reconcile", "original": req,
                         "operation_id": operation, "operation_fingerprint": req["operation_fingerprint"]}
                        if reconcile else req)
            raw = switch.digest(switch.encoded(envelope))
            coordinator = authority_for(ref, req, envelope["action"], raw, switch.digest(switch.encoded(req)))
            coordinator.provider, coordinator.witness = provider, witness

            def observed(name):
                if name == "after_CAS":
                    log("CAS", operation)
                checkpoint(name)

            transport = LocalTransport(coordinator)
            with graph():
                return switch.execute(envelope, raw, transport, LocalProvider(transport), observed)

        def latest():
            return admission.current_fences(qs.command_journal(run))[seed["scope"]]

        # Name the original registered physical paths so the first observed selector covers all.
        target_a = staged("Staging", b"A")
        first = request("first", 0, target_a)
        first["holding"] = seed["holding"]
        first["operation_fingerprint"] = switch.fingerprint(first)
        first_ref, paths = grant_for(first)
        if commit_first:
            self.assertEqual(execute(first, first_ref)["discovery"], "VERIFIED")
            self.assertEqual(latest()["status"], "SWITCH_RELEASED")
        return SimpleNamespace(helper=helper, seed=seed, first=first, first_ref=first_ref, paths=paths,
                               request=request, staged=staged, grant_for=grant_for, execute=execute,
                               latest=latest, active=active, journal=journal, pointer=pointer, fence=fence,
                               spy=spy, witness=witness, b_source=b_source, b_binding=b_binding)

    @unittest.skipUnless(hasattr(os, "fork"), "Linux disposable kill fixture")
    def test_standalone_authority_preserves_release_and_reconciles_shared_close_cuts(self):
        fixture = self.standalone_fixture()
        helper, journal, pointer, fence, spy = (fixture.helper, fixture.journal, fixture.pointer,
                                               fixture.fence, fixture.spy)
        request, staged, grant_for = fixture.request, fixture.staged, fixture.grant_for
        execute, latest, paths = fixture.execute, fixture.latest, fixture.paths
        req = request("second", 1, staged("staged-second", b"B"))
        ref, paths = grant_for(req, paths)
        stage = Path(req["target"]["staged"]["path"]) / "SKILL.md"
        held = Path(req["holding"])
        original_journal = helper.resource_journal_bytes()
        original_pointer = pointer.read_bytes()
        original_spy = spy.read_bytes()
        for invalid, expected in (("stage", "staged tree differs"), ("holding", "invalid initial cut")):
            with self.subTest(invalid=invalid):
                if invalid == "stage":
                    stage.write_bytes(b"mismatch")
                else:
                    held.mkdir()
                with self.assertRaisesRegex(switch.Refused, expected):
                    execute(req, ref)
                self.assertEqual(helper.resource_journal_bytes(), original_journal)
                self.assertEqual(pointer.read_bytes(), original_pointer)
                self.assertEqual(spy.read_bytes(), original_spy)
                self.assertFalse(switch.archive(journal, "second", "intent").exists())
                self.assertFalse(fence.exists())
                if invalid == "stage":
                    stage.write_bytes(b"B")
                else:
                    held.rmdir()

        for name, operation, expected_status in (("before_shared_close", "second", "SWITCH_RELEASED"),
                                                 ("after_shared_close", "third", "SWITCH_CLOSED")):
            with self.subTest(cut=name):
                if operation == "third":
                    req = request(operation, 2, staged("staged-third", b"C"))
                    ref, paths = grant_for(req, paths)
                old_pointer = pointer.read_bytes()
                old_spy = spy.read_bytes()
                old_journal = helper.resource_journal_bytes()
                reader, writer = os.pipe()
                child = os.fork()
                if child == 0:
                    os.close(reader)
                    try:
                        def checkpoint(observed):
                            if observed == name:
                                os.write(writer, b"R")
                                signal.pause()
                        execute(req, ref, checkpoint)
                    except BaseException as error:
                        os.write(writer, repr(error).encode()[:4096])
                    finally:
                        os._exit(91)
                os.close(writer)
                try:
                    self.assertTrue(select.select([reader], [], [], 10)[0], "child did not reach barrier")
                    self.assertEqual(os.read(reader, 4096), b"R")
                finally:
                    os.close(reader)
                    os.kill(child, signal.SIGKILL)
                    _, status = os.waitpid(child, 0)
                self.assertTrue(os.WIFSIGNALED(status))
                self.assertEqual(os.WTERMSIG(status), signal.SIGKILL)
                self.assertTrue(switch.archive(journal, operation, "intent").exists())
                self.assertEqual(latest()["status"], expected_status)
                journal_after_kill = helper.resource_journal_bytes()
                if expected_status == "SWITCH_RELEASED":
                    self.assertEqual(journal_after_kill, old_journal)
                else:
                    self.assertEqual(journal_after_kill[:-1], old_journal)
                    self.assertEqual(latest()["operation_id"], operation)
                self.assertFalse(fence.exists())
                self.assertEqual(pointer.read_bytes(), old_pointer)
                self.assertEqual(spy.read_bytes(), old_spy)
                self.assertEqual(execute(req, ref, reconcile=True)["discovery"], "VERIFIED")
                self.assertEqual(execute(req, ref, reconcile=True)["discovery"], "VERIFIED")
                self.assertEqual(latest()["status"], "SWITCH_RELEASED")
                self.assertEqual(latest()["operation_id"], operation)
                self.assertEqual(spy.read_bytes()[len(old_spy):].decode().splitlines(),
                                 [f"MOVE {operation}", f"MOVE {operation}", f"CAS {operation}"])

    def test_pending_stage_drift_holds_same_session_without_replaying_effects(self):
        class Interrupted(RuntimeError):
            pass

        for cut in ("before_shared_close", "after_shared_close", "before_ABSENT_TO_N"):
            with self.subTest(cut=cut):
                fixture = self.standalone_fixture()
                staged = fixture.staged("staged-pending", b"B")
                request = fixture.request("pending", 1, staged)
                grant, _ = fixture.grant_for(request, fixture.paths)
                stage_file = Path(staged["path"]) / "SKILL.md"
                holding = Path(request["holding"])

                def checkpoint(name):
                    if name == cut:
                        raise Interrupted(name)

                pointer = fixture.pointer.read_bytes()
                with self.assertRaisesRegex(Interrupted, cut):
                    fixture.execute(request, grant, checkpoint)
                old_tree_moved = cut == "before_ABSENT_TO_N"
                self.assertEqual(fixture.fence.exists(), old_tree_moved)
                self.assertTrue(switch.matches(fixture.active, None if old_tree_moved else request["prior"]))
                self.assertTrue(switch.matches(holding, request["prior"] if old_tree_moved else None))
                first_held = cut == "before_shared_close"
                self.assertEqual(fixture.latest()["status"], "SWITCH_RELEASED" if first_held else "SWITCH_CLOSED")
                self.assertEqual(fixture.latest()["operation_id"], "first" if first_held else "pending")
                self.assertEqual(fixture.witness.observe(), request["continuity"])
                before = {path.name: path.read_bytes() for path in fixture.journal.iterdir()}
                self.assertIn("pending.intent.json", before)
                effects = fixture.spy.read_bytes()
                local = switch.record(fixture.fence)
                shared = fixture.helper.resource_journal_bytes()
                stage_file.write_bytes(b"same-session drift")
                checkpoints = []
                result = fixture.execute(request, grant, checkpoints.append, reconcile=True)
                self.assertEqual(result["status"], "UNKNOWN")
                self.assertEqual(fixture.witness.observe(), request["continuity"])
                self.assertEqual(fixture.pointer.read_bytes(), pointer)
                self.assertEqual(fixture.spy.read_bytes(), effects)
                self.assertEqual(switch.record(fixture.fence), local)
                after_shared = fixture.helper.resource_journal_bytes()
                self.assertEqual(after_shared[:-1] if first_held else after_shared, shared)
                self.assertEqual(fixture.latest()["status"], "SWITCH_CLOSED")
                self.assertEqual(fixture.latest()["operation_id"], "pending")
                self.assertEqual({path.name: path.read_bytes() for path in fixture.journal.iterdir()}, before)
                self.assertFalse(checkpoints)
                self.assertTrue(switch.matches(fixture.active, None if old_tree_moved else request["prior"]))
                self.assertTrue(switch.matches(holding, request["prior"] if old_tree_moved else None))
                self.assertEqual(stage_file.read_bytes(), b"same-session drift")

                stage_file.write_bytes(b"B")
                self.assertTrue(switch.matches(stage_file.parent, staged))
                self.assertEqual(fixture.execute(request, grant, reconcile=True)["discovery"], "VERIFIED")
                self.assertEqual(fixture.latest()["status"], "SWITCH_RELEASED")
                remaining_moves = 1 if old_tree_moved else 2
                self.assertEqual(fixture.spy.read_bytes()[len(effects):].decode().splitlines(),
                                 ["MOVE pending"] * remaining_moves + ["CAS pending"])

    def test_pending_witness_gap_before_shared_close_holds_first_install_claim(self):
        class Interrupted(RuntimeError):
            pass

        def checkpoint(name):
            if name == "before_shared_close":
                raise Interrupted(name)

        for gap in ("missing", "changed"):
            with self.subTest(gap=gap):
                fixture = self.standalone_fixture(commit_first=False)
                request, grant = fixture.first, fixture.first_ref
                helper, live = fixture.helper, request["continuity"]
                run = helper.fixture.run
                shared = helper.resource_journal_bytes()

                with self.assertRaisesRegex(Interrupted, "before_shared_close"):
                    fixture.execute(request, grant, checkpoint)
                self.assertEqual(helper.resource_journal_bytes(), shared)
                self.assertNotIn(request["scope"], admission.current_fences(qs.command_journal(run)))
                durable = {path.name: path.read_bytes() for path in fixture.journal.iterdir()}
                self.assertEqual(set(durable), {"first.absence.json", "first.intent.json"})
                self.assertFalse(fixture.pointer.exists())
                self.assertFalse(fixture.fence.exists())
                self.assertFalse(fixture.spy.exists())
                self.assertTrue(switch.matches(Path(request["target"]["staged"]["path"]),
                                               request["target"]["staged"]))

                fixture.witness.current = None if gap == "missing" else {**live, "session_id": "changed-session"}
                checkpoints = []
                result = fixture.execute(request, grant, checkpoints.append, reconcile=True)
                self.assertEqual(result["status"], "UNKNOWN")
                self.assertRegex(result["revalidation_sha256"], r"^[0-9a-f]{64}$")
                self.assertFalse(checkpoints)
                self.assertEqual(fixture.latest()["status"], "SWITCH_CLOSED")
                self.assertEqual(fixture.latest()["operation_id"], request["operation_id"])
                self.assertEqual(helper.resource_journal_bytes()[:-1], shared)
                self.assertEqual({path.name: path.read_bytes() for path in fixture.journal.iterdir()}, durable)
                self.assertFalse(fixture.pointer.exists())
                self.assertFalse(fixture.fence.exists())
                self.assertFalse(fixture.spy.exists())
                self.assertFalse(fixture.active.exists())
                self.assertFalse(Path(request["holding"]).exists())

                fixture.witness.current = live
                payload, _ = helper.request("first-witness-"+gap, "/root/cut",
                                            qs.sha(fixture.b_source.read_bytes()), **fixture.b_binding)
                args = argparse.Namespace(command="claim-effect", root=str(helper.fixture.workspace),
                                          run_id=run.name, request=str(payload))
                held, sent, observed = helper.resource_journal_bytes(), [], []
                with self.assertRaisesRegex(switch.Refused, "switch_effect_scope_fenced"):
                    qs.journal_command(args, sent.append, switch_guard=observed.append)
                self.assertEqual(helper.resource_journal_bytes(), held)
                self.assertFalse(sent)
                self.assertFalse(observed)

                self.assertEqual(fixture.execute(request, grant, reconcile=True)["discovery"], "VERIFIED")
                self.assertEqual(fixture.latest()["status"], "SWITCH_RELEASED")
                self.assertEqual(fixture.spy.read_text().splitlines(), ["MOVE first", "CAS first"])

    def test_committed_drift_holds_fresh_claim_after_exact_restoration(self):
        fixture = self.standalone_fixture()
        helper = fixture.helper
        run = helper.fixture.run
        live = fixture.first["continuity"]
        launch = {**fixture.seed, "runtime_sha256": fixture.first["runtime"]}
        installed = fixture.active / "SKILL.md"
        loader = fixture.active.parent
        moved_loader = loader.with_name(loader.name + "-moved")
        fence_link = fixture.fence.with_name("fence-hardlink-source")
        original = installed.read_bytes()
        inode = installed.stat().st_ino
        loader_inode = loader.stat().st_ino
        pointer = fixture.pointer.read_bytes()
        spy = fixture.spy.read_bytes()
        sent = []
        live_checks = []

        def check_released(row):
            live_checks.append(row)
            return admission.check_released(launch, fixture.witness, row)

        def claim(key):
            payload, _ = helper.request(key, "/root/cut", qs.sha(fixture.b_source.read_bytes()),
                                        **fixture.b_binding)
            args = argparse.Namespace(command="claim-effect", root=str(helper.fixture.workspace),
                                      run_id=run.name, request=str(payload))
            return qs.journal_command(args, sent.append, switch_guard=check_released)

        self.assertTrue(admission.check_released(launch, fixture.witness, fixture.latest()))
        grant = fixture.first_ref
        for gap in ("tree", "loader_symlink", "fence_directory", "fence_hardlink",
                    "missing_witness", "changed_witness"):
            with self.subTest(gap=gap):
                old = helper.resource_journal_bytes()
                if gap == "tree":
                    installed.write_bytes(b"changed in place")
                elif gap == "loader_symlink":
                    loader.rename(moved_loader)
                    loader.symlink_to(moved_loader, target_is_directory=True)
                    self.assertEqual(installed.stat().st_ino, inode)
                elif gap == "fence_directory":
                    fixture.fence.mkdir()
                elif gap == "fence_hardlink":
                    fence_link.write_bytes(b"ambiguous local fence")
                    fixture.fence.hardlink_to(fence_link)
                elif gap == "missing_witness":
                    fixture.witness.current = None
                else:
                    fixture.witness.current = {**live, "session_id": "changed-session"}
                result = fixture.execute(fixture.first, grant, reconcile=True)
                self.assertEqual(result["status"], "UNKNOWN")
                self.assertEqual(fixture.latest()["status"], "SWITCH_CLOSED")
                self.assertEqual(fixture.latest()["operation_id"], fixture.first["operation_id"])
                self.assertEqual(helper.resource_journal_bytes()[:-1], old)
                self.assertEqual(fixture.pointer.read_bytes(), pointer)
                self.assertEqual(fixture.spy.read_bytes(), spy)

                if gap == "tree":
                    installed.write_bytes(original)
                elif gap == "loader_symlink":
                    loader.unlink()
                    moved_loader.rename(loader)
                elif gap == "fence_directory":
                    self.assertTrue(fixture.fence.is_dir())
                    fixture.fence.rmdir()
                elif gap == "fence_hardlink":
                    self.assertEqual(fixture.fence.stat().st_nlink, 2)
                    self.assertEqual(fixture.fence.read_bytes(), b"ambiguous local fence")
                    fixture.fence.unlink()
                    fence_link.unlink()
                fixture.witness.current = live
                self.assertEqual(loader.stat().st_ino, loader_inode)
                self.assertEqual(installed.stat().st_ino, inode)
                self.assertEqual(installed.read_bytes(), original)
                old = helper.resource_journal_bytes()
                with self.assertRaisesRegex(switch.Refused, "switch_effect_scope_fenced"):
                    claim("fresh-"+gap)
                self.assertEqual(helper.resource_journal_bytes(), old)
                self.assertFalse(live_checks)
                self.assertFalse(sent)

                grant, _ = fixture.grant_for(fixture.first, fixture.paths, label="observed-"+gap)
                self.assertEqual(fixture.execute(fixture.first, grant, reconcile=True)["discovery"], "VERIFIED")
                self.assertEqual(fixture.latest()["status"], "SWITCH_RELEASED")
                self.assertEqual(fixture.pointer.read_bytes(), pointer)
                self.assertEqual(fixture.spy.read_bytes(), spy)
        receipt = claim("fresh-after-release")
        self.assertEqual(receipt["effect_state"], "UNKNOWN")
        self.assertEqual(sent, [receipt])
        self.assertEqual(len(live_checks), 1)

    def test_session_keeps_reconcile_available_without_enrolled_witness(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime = Path("/mnt/c/fixture-runtime")
            active = "/mnt/c/fixture-loader/active"
            manifest_path = str(runtime / "manifest.json")
            provider = {"path": "/mnt/c/powershell.exe", "sha256": "a"*64, "volume": "fixture-volume"}
            manifest = {"members": {admission.SUPERVISOR: "b"*64, admission.ENTRY: "c"*64},
                        "supervisor_interpreter": {"path": "/mnt/c/python.exe", "sha256": "d"*64,
                                                   "version": "fixture"},
                        "provider": provider}
            request_path = str(root/"reconcile.json")
            launch = {"runtime_manifest": manifest_path, "runtime_sha256": "e"*64,
                      "candidate_manifest": str(root/"candidate.json"), "candidate_sha256": "f"*64,
                      "root_agent_id": "/root", "active": active, "journal": str(root/"journal"),
                      "pointer": str(root/"pointer"), "fence": str(root/"fence"), "mode": "delivery"}
            witness = {"boot_id": "boot", "volume_guid": "fixture-volume",
                       "volume_serial": 1, "session_id": "held"}
            req = {"request_version": switch.REQUEST, "action": "activate",
                   "provider": switch.WINDOWS, "recovery_scope": switch.WINDOWS_SCOPE,
                   "runtime": launch["runtime_sha256"], "journal": launch["journal"],
                   "active": active, "pointer": launch["pointer"], "fence": launch["fence"],
                   "holding": "/mnt/c/fixture-holding", "run_lock": str(root/"run.lock"),
                   "occurrence_lock": str(root/"occurrence.lock"),
                   "operation_id": "operation", "logical_occurrence_id": "OCC-A",
                   "scope": "test", "profile": "test", "continuity": witness, "prior": None,
                   "expected_pointer_sha256": switch.ABSENT, "expected_generation": 0,
                   "target_generation": 1, "target": {"tag": "TARGET_TREE", "staged": {
                       "path": "/mnt/c/fixture-staged", "identity": [1, 2], "manifest_sha256": "a"*64}}}
            req["operation_fingerprint"] = switch.fingerprint(req)
            envelope = {"request_version": switch.REQUEST, "action": "reconcile", "original": req,
                        "operation_id": req["operation_id"], "operation_fingerprint": req["operation_fingerprint"]}
            command = {"request": request_path, "request_sha256": "a"*64, "original_sha256": "b"*64,
                       "grant": {"id": "G", "sha256": "c"*64}, "expected_revision": "d"*64}
            def dispatch(authority, *_):
                self.assertIsNone(authority.continuity())
                return {"status": "UNKNOWN"}

            def protected_path_only(path):
                self.assertNotIn(str(path), (active, str(Path(active).parent),
                                             req["holding"], req["target"]["staged"]["path"]))
                return Path(path)

            send_message = switch.send_message
            for startup_cut in ("spawn_failure", "dead_child"):
                with self.subTest(startup_cut=startup_cut):
                    seen = []
                    child = None
                    startup_error = "enrollment unavailable"
                    if startup_cut == "dead_child":
                        child = subprocess.Popen([sys.executable, "-I", "-B", "-c", "pass"],
                                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE)
                        self.assertEqual(child.wait(timeout=5), 0)
                        startup_error = os.strerror(errno.EPIPE)

                    def start_child(*args, **kwargs):
                        if child is None:
                            raise OSError(startup_error)
                        return child

                    def send(stream, value):
                        if child is not None and stream is child.stdin:
                            send_message(stream, value)
                            return
                        seen.append(value)

                    with patch.object(switch, "path_at", side_effect=protected_path_only), \
                            patch.object(switch, "capture_runtime", return_value=manifest), \
                            patch.object(switch, "WindowsMove", return_value=object()), \
                            patch.object(qs, "run_dir", return_value=root/"run"), \
                            patch.object(qs, "root", return_value=root), \
                            patch.object(admission, "pinned", return_value=switch.encoded(envelope)), \
                            patch.object(admission.subprocess, "Popen", side_effect=start_child), \
                            patch.object(switch, "send_message", side_effect=send), \
                            patch.object(switch, "read_message", side_effect=[command, None]), \
                            patch.object(admission, "execute_child", side_effect=dispatch):
                        args = argparse.Namespace(root=str(root), run_id="RUN")
                        result = admission.session(args, qs, launch, {"entries": manifest["members"]})
                    self.assertEqual(result, {"status": "SWITCH_SESSION_CLOSED"})
                    self.assertEqual(len(seen), 2)
                    self.assertIsNone(seen[0]["continuity"])
                    self.assertIn(startup_error, seen[0]["continuity_error"])
                    self.assertEqual(seen[1], {"result": {"status": "UNKNOWN"}})
                    if child is not None:
                        self.assertTrue(child.stdin.closed)
                        self.assertTrue(child.stdout.closed)

    def test_delivery_projection_matrix_keeps_unknown_and_ignores_terminal_only(self):
        def row(operation, occurrence, status, **fields):
            return {"operation": operation, "receipt": {
                "logical_occurrence_id": occurrence, "status": status, **fields}}
        claimed, reconciled = "EFFECT_CLAIMED", "EFFECT_RECONCILED"
        journal = [
            row("claim-effect", "OCC-ACK", claimed, effect_state="UNKNOWN"),
            row("reconcile-effect", "OCC-ACK", reconciled, effect_state="RECIPIENT_CONFIRMED"),
            row("reconcile-effect", "OCC-ACK", "EFFECT_RESOURCE_RELEASED"),
            row("claim-effect", "OCC-COMBINED", claimed, effect_state="UNKNOWN"),
            row("reconcile-effect", "OCC-COMBINED", reconciled, effect_state="RECIPIENT_CONFIRMED",
                resource_terminal_state="COMPLETED"),
            row("claim-effect", "OCC-RETRY", claimed, effect_state="UNKNOWN"),
            row("reconcile-effect", "OCC-RETRY", reconciled, effect_state="NOT_SENT"),
            row("claim-effect", "OCC-UNKNOWN", claimed, effect_state="UNKNOWN"),
            row(admission.FENCE, "OCC-UNKNOWN", "SWITCH_CLOSED"),
        ]
        self.assertEqual(admission.delivery_states(journal), {
            "OCC-ACK": "RECIPIENT_CONFIRMED", "OCC-COMBINED": "RECIPIENT_CONFIRMED",
            "OCC-RETRY": "NOT_SENT", "OCC-UNKNOWN": "UNKNOWN"})


class SwitchBootstrapTest(unittest.TestCase):
    def test_tampered_controller_dependencies_refuse_before_import(self):
        originals = Path(qs.__file__).resolve().parent
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            scripts = base / "scripts"
            scripts.mkdir()
            entries = {}
            for source in originals.glob("*.py"):
                shutil.copyfile(source, scripts/source.name)
                entries["scripts/"+source.name] = qs.sha(source.read_bytes())
            candidate = base / "manifest.json"
            candidate.write_bytes(switch.encoded({"entries": entries}))
            launch = base / "launch.json"
            launch.write_bytes(switch.encoded({"candidate_manifest": str(candidate),
                                               "candidate_sha256": qs.sha(candidate.read_bytes())}))
            invocation = [sys.executable, "-I", "-B", str(scripts/"qs.py"), "switch-session",
                          "--root", str(base), "--run-id", "R1", "--launch", str(launch),
                          "--launch-sha256", qs.sha(launch.read_bytes())]
            for name in ("orchestration", "question_admission", "question_ledger"):
                with self.subTest(name=name):
                    module = scripts / (name+".py")
                    original = module.read_bytes()
                    marker = base / name
                    module.write_bytes(original + (
                        "\nfrom pathlib import Path\nPath(" + repr(str(marker)) + ").touch()\n"
                    ).encode())
                    result = subprocess.run(invocation, capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                    self.assertEqual(json.loads(result.stderr)["error"],
                                     "switch source hash differs:scripts/"+name+".py")
                    self.assertFalse(marker.exists())
                    module.write_bytes(original)
            result = subprocess.run(invocation, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stderr)["error"], "unknown switch scope mode")
            for name in ("socket", "ctypes", "argparse", "__future__"):
                (scripts/(name+".py")).write_text(
                    "from pathlib import Path\nPath(" + repr(str(base/name)) + ").touch()\n"
                )
            for options, expected in (
                (("-I", "-B"), "unknown switch scope mode"),
                (("-I",), "switch session requires python -I -B"),
                (("-B",), "switch session requires python -I -B"),
                ((), "switch session requires python -I -B"),
            ):
                with self.subTest(options=options):
                    result = subprocess.run([sys.executable, *options, *invocation[3:]],
                                            capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                    self.assertEqual(json.loads(result.stderr)["error"], expected)
                    self.assertFalse(any((base/name).exists()
                                         for name in ("socket", "ctypes", "argparse", "__future__")))
