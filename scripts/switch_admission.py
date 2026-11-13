"""Coordinator binding for the standalone ICR-002-R3 generation switch."""

import argparse
import base64
from contextlib import suppress
from pathlib import Path
import secrets
import socket
import subprocess
import sys

import skill_generation_switch as switch

FENCE = "skill-switch-fence"
ADOPTION = "4056a1bbec6c116ba7b291aa4619faf9e0d8734b637eabc588a369346961998d"
SUPERVISOR = "scripts/continuity_supervisor.py"
ENTRY = "scripts/skill_generation_switch.py"


def native(path, *, check_path=True):
    validate = switch.path_at if check_path else switch.canonical_path
    relative = validate(str(path)).relative_to("/mnt/c")
    switch.require(relative.parts and all("\\" not in part for part in relative.parts),
                   "invalid Windows path mapping")
    return "C:\\" + "\\".join(relative.parts)


def pinned(path, raw_sha):
    switch.sha(raw_sha)
    raw = switch.read(switch.path_at(path))
    switch.require(switch.digest(raw) == raw_sha, "protected raw digest differs")
    return raw


def current_fences(journal):
    latest = {}
    for row in journal:
        if row["operation"] == FENCE:
            receipt = row["receipt"]
            switch.fields(receipt, {"status", "scope", "logical_occurrence_id", "effects",
                                    "fingerprint", "operation_id", "continuity", "pointer_sha256",
                                    "provider", "recovery_scope", "adoption", "effect_scope"})
            switch.require(receipt["status"] in {"SWITCH_CLOSED", "SWITCH_RELEASED"} and
                           receipt["provider"] == switch.WINDOWS and
                           receipt["recovery_scope"] == switch.WINDOWS_SCOPE and
                           receipt["adoption"] == ADOPTION and
                           receipt["logical_occurrence_id"] in effect_ids(receipt["effects"]),
                           "invalid switch fence history")
            for name in (receipt["scope"], receipt["operation_id"]):
                switch.token(name)
            switch.occurrence_id(receipt["logical_occurrence_id"])
            selector = receipt["effect_scope"]
            switch.fields(selector, {"assignment_ids", "resource_keys", "sole_writer_paths"})
            switch.require(all(type(values) is list and
                               all(type(value) is str and value for value in values) and
                               values == sorted(set(values)) for values in selector.values()) and
                           bool(selector["sole_writer_paths"]), "invalid switch fence scope")
            switch.sha(receipt["fingerprint"])
            if receipt["pointer_sha256"] != switch.ABSENT or receipt["status"] == "SWITCH_RELEASED":
                switch.sha(receipt["pointer_sha256"])
            continuity = receipt["continuity"]
            switch.require(type(continuity) is str, "invalid switch fence continuity")
            witness = switch.decode(continuity.encode("utf-8"))
            switch.continuity_binding(witness)
            switch.require(switch.encoded(witness).decode("utf-8") == continuity,
                           "noncanonical switch fence continuity")
            latest[receipt["scope"]] = {**receipt, "continuity": witness}
    return latest


def effect_ids(values):
    switch.require(type(values) is list, "invalid switch effect IDs")
    ids = {switch.occurrence_id(value) for value in values}
    switch.require(len(ids) == len(values), "duplicate switch effect IDs")
    return ids


def writer_scope(qs, declaration):
    """Use one case-insensitive identity for the native C: and WSL mount projections."""
    scope = qs._effect_resource_scope(declaration)
    scope["sole_writer_paths"] = sorted({"/".join(switch.path_identity(path))
                                        for path in declaration.get("sole_writer_paths", [])})
    return scope


def in_effect_scope(qs, assignment, selector):
    return (assignment["assignment_id"] in selector["assignment_ids"] or
            qs.dispatch_resource_conflict(writer_scope(qs, assignment), writer_scope(qs, selector)))


def claim_guard(qs, journal, assignment, live_guard=None):
    occurrence = assignment["logical_occurrence_id"]
    for receipt in current_fences(journal).values():
        named = occurrence in receipt["effects"]
        if named or in_effect_scope(qs, assignment, receipt["effect_scope"]):
            switch.require(named and receipt["status"] == "SWITCH_RELEASED" and
                           live_guard is not None and live_guard(receipt) is True,
                           "switch_effect_scope_fenced")


def delivery_states(journal):
    latest = {}
    for row in journal:
        receipt = row["receipt"]
        operation = row["operation"]
        # A terminal-only resource release leaves the prior delivery state in force.
        if operation == "claim-effect" or (
            operation == "reconcile-effect" and receipt.get("status") != "EFFECT_RESOURCE_RELEASED"
        ):
            latest[receipt["logical_occurrence_id"]] = receipt["effect_state"]
    return latest


def selected_effects(qs, run, state, current, root_doc, request, physical, previous):
    scopes = root_doc.get("switch_effect_scopes")
    declared = scopes.get(request["scope"]) if isinstance(scopes, dict) else None
    switch.require(declared is not None, "selected switch effect scope missing")
    switch.fields(declared, {"profile", "active", "assignment_ids", "resource_keys", "sole_writer_paths"})
    switch.require(all(declared[name] == request[name] for name in ("profile", "active")),
                   "selected switch effect scope subject differs")
    assignment_ids = declared["assignment_ids"]
    switch.require(type(assignment_ids) is list, "selected switch assignment IDs invalid")
    named = {qs.ident(identifier, "assignment_id") for identifier in assignment_ids}
    switch.require(len(named) == len(assignment_ids), "selected switch assignment IDs duplicate")
    switch.require(type(declared["sole_writer_paths"]) is list, "selected switch writer paths invalid")
    selector = writer_scope(qs, {
        "resource_keys": declared["resource_keys"],
        "sole_writer_paths": [*map(str, physical), *declared["sole_writer_paths"]],
    })
    selector["assignment_ids"] = sorted(named)
    if previous is not None:
        # Keep earlier writer coverage across operations on the same loader.
        selector = {key: sorted(set(values) | set(previous["effect_scope"][key]))
                    for key, values in selector.items()}

    registered = state["registered"]
    intent_names = {qs.sha(pair.encode()).upper()+".yaml" for pair in registered}
    switch.require({path.name for path in qs.child(run, "intents").glob("*.yaml")} == intent_names,
                   "switch assignment intent projection requires recovery")
    snapshot = current["input_snapshot"]
    records = qs.load_launch_intents(run, snapshot)["records"]
    switch.require(records.keys() <= registered.keys(), "switch launch intent projection requires recovery")
    effects = set()
    registered_ids = set()
    native_unknown = set()
    for pair, entry in registered.items():
        assignment = entry["assignment"]
        occurrence = assignment.get("logical_occurrence_id")
        record = records.get(pair)
        chain = qs.checked_launch_chain(run, snapshot, pair, entry, record)
        if occurrence != request["logical_occurrence_id"] and not in_effect_scope(qs, assignment, selector):
            continue
        effects.add(switch.occurrence_id(occurrence))
        registered_ids.add(assignment["assignment_id"])
        launch_states = {entry.get("launch_state"), assignment.get("launch_state")}
        if record is not None:
            launch_states.add(record.get("state"))
        if chain:
            launch_states.add(chain[-1]["state"])
        if "DISPATCH_UNKNOWN" in launch_states:
            native_unknown.add(occurrence)
        switch.require(not chain or (record == chain[-1] and entry.get("launch_state") == chain[-1]["state"])
                       or occurrence in native_unknown, "switch applicable launch projection requires recovery")
    switch.require(set(selector["assignment_ids"]) <= registered_ids and
                   request["logical_occurrence_id"] in effects,
                   "selected switch assignment scope unregistered")
    return selector, sorted(effects), native_unknown


class NativeWitness:
    """Private inherited pipes identify the held child and its per-launch challenge."""

    def __init__(self, runtime, manifest, active):
        pin = manifest["supervisor_interpreter"]
        provider = manifest["provider"]
        self.challenge = secrets.token_hex(32)
        self.initial = self.initial_error = None
        self.process = None
        target = native(active.parent, check_path=False)
        entry = native(runtime / SUPERVISOR)
        powershell = native(provider["path"])
        try:
            self.process = subprocess.Popen(
                [pin["path"], "-I", "-B", entry], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            switch.send_message(self.process.stdin, {
                "target": target, "volume": provider["volume"],
                "powershell": powershell, "powershell_sha256": provider["sha256"],
                "challenge": self.challenge, "interpreter_sha256": pin["sha256"],
                "interpreter_version": pin["version"], "entry_sha256": manifest["members"][SUPERVISOR]})
            self.initial = self.receive()
        except (switch.Refused, ValueError, OSError) as error:
            self.close()
            self.initial_error = str(error)
        except BaseException:
            self.close()
            raise

    def receive(self):
        reply = switch.read_message(self.process.stdout)
        switch.fields(reply, {"challenge", "witness"})
        switch.require(self.process.poll() is None and reply["challenge"] == self.challenge,
                       "native continuity channel lost")
        switch.continuity_binding(reply["witness"])
        return reply["witness"]

    def observe(self):
        if self.process is None:
            return None
        switch.send_message(self.process.stdin, {"observe": self.challenge})
        return self.receive()

    def close(self):
        process, self.process = self.process, None
        if process is None:
            return
        try:
            if process.poll() is None:
                switch.send_message(process.stdin, {"close": self.challenge})
        except OSError:
            pass
        finally:
            # A failed flush can leave bytes buffered for another write on close.
            for cleanup in (process.stdin.close, process.stdout.close, process.wait):
                with suppress(OSError):
                    cleanup()


def accepted_evidence(qs, run, state, ref, kind):
    switch.fields(ref, {"id", "sha256"})
    switch.sha(ref["sha256"])
    entry, doc = qs._admitted_document(run, state, ref["id"], "EVIDENCE", verify_cache=True)
    switch.require(entry["content_hash"] == ref["sha256"] and
                   doc.get("knowledge_status") == "OBSERVED" and kind in doc,
                   "switch evidence binding differs: " + kind)
    pair = entry["assignment_id"], entry["attempt_id"]
    switch.require(state["current_attempts"].get(pair[0]) == pair[1] and
                   qs._immutable_accepted_attempt_result(run, state, *pair) == ref["id"],
                   "switch evidence attempt changed: " + kind)
    payload = doc[kind]
    if type(payload) is str:
        # The RC's strict YAML permits strings but rejects implicit typed scalars.
        canonical = payload.encode("utf-8")
        payload = switch.decode(canonical)
        switch.require(switch.encoded(payload) == canonical, "noncanonical switch evidence: " + kind)
    switch.require(type(payload) is dict, "invalid switch evidence payload: " + kind)
    return entry, payload


class Authority:
    """All methods run inside the same occurrence-before-run coordinator cut."""

    def __init__(self, qs, run, request, raw_sha, original_sha, expected_revision, envelope_action, grant_ref,
                 launch, witness, provider):
        self.qs, self.run, self.request = qs, run, request
        self.raw_sha, self.original_sha, self.envelope_action = raw_sha, original_sha, envelope_action
        self.expected_revision = expected_revision
        self.grant_ref, self.launch, self.witness, self.provider = grant_ref, launch, witness, provider
        self.operation = request["operation_id"]
        self.journal_dir = Path(request["journal"])
        self.occurrence = request["logical_occurrence_id"]
        self.occurrence_key = qs.sha(self.occurrence.encode())
        self.journal_adapter = qs._JournalAdmission(run)
        self.grant = None

    def command_cut(self):
        return self.qs.command_cut(self.journal_adapter.lock(),
                                   self.journal_adapter.occurrence_lock(self.occurrence_key))

    def cut(self):
        return True

    def physical(self):
        bound = self.request["target"].get("staged")
        backup = self.request.get("backup")
        return [Path(path) for path in (self.request["active"], self.request["holding"],
                bound and bound["path"], backup and backup["path"]) if path is not None]

    def validate_paths(self, grant):
        """Authenticate path bindings without observing a mutable loader namespace."""
        request, run = self.request, self.run
        occurrence_lock = run / "state/occurrences" / self.occurrence_key / "state/coordinator.lock"
        switch.require(Path(request["run_lock"]) == run / "state/coordinator.lock" and
                       Path(request["occurrence_lock"]) == occurrence_lock,
                       "request does not use the shared coordinator locks")
        for name in ("active", "journal", "pointer", "fence"):
            switch.require(request[name] == self.launch[name] == grant[name],
                           "fixed switch path differs: " + name)
        runtime = Path(self.launch["runtime_manifest"]).parent
        autoload = [switch.path_identity(switch.canonical_path(path)) for path in grant["autoload_roots"]]
        physical = self.physical()
        active = switch.path_identity(physical[0])
        switch.require(any(root != active and switch.contains_path(root, active) for root in autoload),
                       "active path outside intended loader surface")
        if self.launch["mode"] == "qualification":
            disposable = switch.canonical_path(self.launch["qualification_root"])
            disposable_id = switch.path_identity(disposable)
            switch.require(disposable_id and disposable_id[-1].startswith("qs-switch-qualification-") and
                           all(disposable_id != identity and switch.contains_path(disposable_id, identity)
                               for identity in map(switch.path_identity, physical)),
                           "qualification outside its reserved disposable namespace")
        metadata = [runtime, Path(request["journal"]), Path(request["pointer"]), Path(request["fence"]),
                    run, Path(request["run_lock"]), Path(request["occurrence_lock"])]
        for path in [*physical[1:], *metadata]:
            identity = switch.path_identity(path)
            switch.require(not any(switch.contains_path(root, identity) or switch.contains_path(identity, root)
                                   for root in autoload), "switch surface overlaps autoload")
        switch.check_path_separation(physical, metadata)

    def admit(self, fingerprint, raw_sha):
        qs, run, request = self.qs, self.run, self.request
        switch.require(fingerprint == request["operation_fingerprint"] and raw_sha == self.raw_sha,
                       "switch request identity differs")
        state, current, bank, journal = self.journal_adapter.read_committed_locked()
        switch.require(current.get("status") != "SEALED", "sealed_run_command_refused")
        switch.require(qs.command_revision(run,journal) == self.expected_revision,
                       "switch expected command cut changed")
        entry, grant = accepted_evidence(qs, run, state, self.grant_ref, "switch_grant")
        switch.fields(grant, {"adoption", "mode", "input_snapshot", "plan_sha256", "graph_sha256",
                              "authority_result_id", "authority_sha256", "request_sha256",
                              "quiescence", "restore_source", "recovery", "discovery",
                              "predecessors", "effects", "active", "journal", "pointer",
                              "fence", "autoload_roots"})
        selected, plan = qs.plan_projection_for_run(run, state, current)
        plan_sha = qs.sha(qs.canon_yaml(plan))
        roots = [rid for rid, logical in selected.items() if logical == "S-000"]
        switch.require(len(roots) == 1 and roots[0] == grant["authority_result_id"] and
                       current.get("current_plan_projection_sha256") == plan_sha == grant["plan_sha256"],
                       "current switch selection or root authority changed")
        root = state["results"][roots[0]]
        pair = root["assignment_id"], root["attempt_id"]
        assignment = state["registered"][pair[0] + "\0" + pair[1]]
        graph = qs.architecture_projection_for_run(run, state, current, bank)["architecture_ref"]
        switch.require(entry["agent_id"] == self.launch["root_agent_id"] and
                       assignment["assignment"].get("selection_authority") == "ROOT" and
                       assignment["assignment_sha256"] == grant["authority_sha256"] and
                       state["current_attempts"].get(pair[0]) == pair[1] and
                       qs._immutable_accepted_attempt_result(run, state, *pair) == roots[0] and
                       graph["graph_sha256"] == grant["graph_sha256"] and
                       current["input_snapshot"] == grant["input_snapshot"] and
                       grant["adoption"] == ADOPTION and grant["mode"] == self.launch["mode"] and
                       grant["request_sha256"] == self.original_sha,
                       "switch grant authority or exact cut differs")
        self.validate_paths(grant)
        blocked = set(current.get("stale_result_ids", []))
        for affected in qs.intake_affected_results(run, state, journal):
            blocked.update(affected)
        switch.fields(grant["predecessors"], {"C01", "C02", "C03"} |
                      ({"C04", "C05"} if grant["mode"] == "delivery" else set()))
        dependencies = {entry["result_id"], *selected, graph["synthesis_id"]}
        for check, reference in grant["predecessors"].items():
            proof, acceptance = accepted_evidence(qs, run, state, reference, "switch_predecessor")
            switch.require(proof["agent_id"] != entry["agent_id"] and
                           acceptance == {"check": check, "verdict": "PASS",
                                          "candidate_sha256": self.launch["candidate_sha256"]},
                           "switch predecessor not applicable: " + check)
            dependencies.add(proof["result_id"])
        fences = current_fences(journal)
        previous = fences.get(request["scope"])
        _, root_doc = qs._admitted_document(run, state, roots[0], "PLAN")
        selector, effects, native_unknown = selected_effects(
            qs, run, state, current, root_doc, request, self.physical(), previous)
        switch.require(effect_ids(grant["effects"]) == set(effects), "switch effect scope incomplete")
        proof, observed = accepted_evidence(qs, run, state, grant["quiescence"], "switch_quiescence")
        switch.require(proof["agent_id"] != entry["agent_id"] and observed == {
            "profile": request["profile"], "active": request["active"],
            "request_sha256": self.original_sha, "cached_writers": "QUIESCENT",
            "client_writers": "QUIESCENT", "session_id": request["continuity"]["session_id"],
            "effects": effects, "effect_scope_sha256": qs.sha(qs.canon_yaml(selector))},
            "switch quiescence evidence differs")
        dependencies.add(proof["result_id"])
        switch.require(not (blocked & dependencies), "switch authority cut stale or unresolved")
        switch.require(not native_unknown, "switch applicable native dispatch UNKNOWN")
        latest = delivery_states(journal)
        switch.require(all(latest.get(name) != "UNKNOWN" for name in effects),
                       "switch applicable effect UNKNOWN")
        if self.envelope_action == "reconcile":
            intent = switch.decode(switch.read(switch.archive(self.journal_dir, self.operation, "intent")))
            switch.require(intent["request_sha256"] == self.original_sha,
                           "reconcile original raw pin differs")
        self.state, self.current, self.grant, self.grant_entry = state, current, grant, entry
        self.blocked = blocked
        self.effect_scope, self.effects = selector, effects
        self.fence_before, self.fence_revision = previous, self.expected_revision
        return True

    def continuity(self):
        return self.witness.observe()

    def check_fence_close(self, before):
        request = self.request
        pointer = switch.record(Path(request["pointer"]))
        intent = switch.decode(switch.read(switch.archive(self.journal_dir, self.operation, "intent")))
        switch.fields(intent, {"request", "pre_pointer", "post_pointer", "request_sha256"})
        post = switch.encoded(switch.pointer_value(request))
        pre = base64.b64decode(intent["pre_pointer"], validate=True) if intent["pre_pointer"] is not None else None
        switch.require(intent["request"] == request and intent["request_sha256"] == self.original_sha and
                       intent["post_pointer"] == switch.digest(post) and pointer in (pre, post),
                       "switch fence request lineage differs")
        switch.expected_cut(request, pre)
        if before is None:
            return
        switch.require(before["logical_occurrence_id"] == self.occurrence and
                       set(before["effects"]) <= set(self.effects), "switch scope ownership differs")
        same_operation = (before["operation_id"], before["fingerprint"]) == (
            self.operation, request["operation_fingerprint"])
        if same_operation:
            if before["status"] == "SWITCH_CLOSED":
                switch.require(before["effect_scope"] == self.effect_scope and
                               before["pointer_sha256"] == request["expected_pointer_sha256"],
                               "pending switch fence binding differs")
            else:
                switch.require(pointer == post and switch.digest(post) == before["pointer_sha256"],
                               "released switch fence pointer differs")
            return

        # A successor may only close against the predecessor's still-current pointer.
        switch.require(pre is not None and pointer == pre and before["operation_id"] != self.operation,
                       "switch fence predecessor pointer differs")
        prior = switch.decode(pre)
        switch.require((prior["operation_id"], prior["operation_fingerprint"]) ==
                       (before["operation_id"], before["fingerprint"]),
                       "switch fence predecessor pointer differs")
        if before["status"] == "SWITCH_RELEASED":
            switch.require(switch.digest(pre) == before["pointer_sha256"], "released switch fence pointer differs")
        else:
            switch.require(request["action"] == "restore", "competing shared switch fence")

    def write_fence(self, status, witness, pointer_sha):
        switch.require(self.grant is not None, "switch grant required")
        qs, request = self.qs, self.request
        view = self.journal_adapter.read_committed_locked()
        journal = view[3]
        fences = current_fences(journal)
        before = fences.get(request["scope"])
        revision = qs.command_revision(self.run, journal)
        switch.require(before == self.fence_before and revision == self.fence_revision,
                       "switch fence admission cut changed")
        value = {"status": status, "scope": request["scope"], "logical_occurrence_id": self.occurrence,
                 "effects": self.effects, "effect_scope": self.effect_scope,
                 "fingerprint": request["operation_fingerprint"],
                 "operation_id": self.operation, "continuity": witness, "pointer_sha256": pointer_sha,
                 "provider": switch.WINDOWS, "recovery_scope": switch.WINDOWS_SCOPE, "adoption": ADOPTION}
        if status == "SWITCH_CLOSED":
            self.check_fence_close(before)
        if before == value:
            return True
        if status != "SWITCH_CLOSED":
            identity = ("operation_id", "fingerprint", "logical_occurrence_id", "effect_scope", "effects")
            switch.require(status == "SWITCH_RELEASED" and before is not None and
                           before["status"] == "SWITCH_CLOSED" and
                           all(before[key] == value[key] for key in identity),
                           "shared release has no matching closed guard")
        for scope, other in fences.items():
            if scope != request["scope"]:
                switch.require(not (set(other["effects"]) & set(value["effects"])) and
                               not (set(other["effect_scope"]["assignment_ids"]) &
                                    set(self.effect_scope["assignment_ids"])) and
                               not qs.dispatch_resource_conflict(other["effect_scope"], self.effect_scope),
                               "overlapping shared switch effect scope")
        body = qs.canon_yaml({**value, "continuity": switch.encoded(witness).decode("utf-8")})
        head = journal[-1]["raw_sha256"] if journal else "GENESIS"
        key = "switch-" + qs.sha(qs.canon_yaml([head, value]))
        switch.require(self.journal_adapter.lookup_committed_locked(view, key) is None,
                       "switch fence journal key conflicts")
        command = qs.Request(FENCE, key, self.grant_entry["agent_id"], self.grant["authority_sha256"],
                             self.run.name, self.current["input_snapshot"], revision, body, self.occurrence_key)
        self.journal_adapter.check_new_locked(view, command)
        self.journal_adapter.commit_locked(view, command, qs.Committed(command.fingerprint(), body), ())
        self.fence_before, self.fence_revision = value, qs.command_revision(self.run)
        return True

    def close_guard(self):
        return self.write_fence("SWITCH_CLOSED", self.request["continuity"],
                                self.request["expected_pointer_sha256"])

    def proof(self, name, expected):
        ref = self.grant[name]
        if ref is None:
            return False
        entry, payload = accepted_evidence(self.qs, self.run, self.state, ref, "switch_" + name)
        return (entry["result_id"] not in self.blocked and
                entry["agent_id"] != self.grant_entry["agent_id"] and payload == expected)

    def recovery_admit(self, witness, cut, cut_sha):
        if (self.envelope_action != "restore" or
                switch.record(switch.archive(self.journal_dir, self.operation, "intent")) is not None):
            return False
        local = switch.readonly_cut(self.request, self.physical(), self.journal_dir,
                                    Path(self.request["pointer"]), Path(self.request["fence"]))
        return (witness == self.request["continuity"] and cut == local and
                cut_sha == switch.digest(switch.encoded(local)) and
                self.proof("recovery", {"continuity": witness, "cut_sha256": cut_sha,
                                        "request_sha256": self.original_sha}))

    def restore_source(self):
        return self.proof("restore_source", {"staged": self.request["target"]["staged"],
                                              "prior_pointer_sha256": self.request["expected_pointer_sha256"],
                                              "request_sha256": self.original_sha})

    def discover(self, pointer_sha):
        ref = self.grant["discovery"]
        if ref is None:
            return None
        entry, payload = accepted_evidence(self.qs, self.run, self.state, ref, "switch_discovery")
        switch.require(entry["result_id"] not in self.blocked and
                       entry["agent_id"] != self.grant_entry["agent_id"], "discovery authority differs")
        switch.verify_discovery(self.request, pointer_sha, payload)
        return payload

    def release_guard(self, witness):
        post = switch.encoded(switch.pointer_value(self.request))
        active, holding, *_ = self.physical()
        target = self.request["target"].get("staged")
        backup = self.request.get("backup")
        pointer_sha = switch.digest(post)
        evidence = switch.decode(switch.read(switch.archive(self.journal_dir, self.operation, "verified")))
        switch.verify_discovery(self.request, pointer_sha, evidence)
        switch.require(self.continuity() == witness and
                       switch.record(Path(self.request["pointer"])) == post and
                       switch.record(Path(self.request["fence"])) is None and
                       switch.read(switch.archive(self.journal_dir, self.operation, "commit")) ==
                       switch.encoded(switch.commit_value(self.request, pointer_sha)) and
                       switch.matches(active, target) and switch.matches(holding, self.request["prior"]) and
                       (target is None or switch.matches(Path(target["path"]), None)) and
                       (backup is None or switch.matches(Path(backup["path"]), backup)),
                       "switch release cut changed")
        return self.write_fence("SWITCH_RELEASED", witness, pointer_sha)

    def observe_roots(self):
        switch.require(self.grant is not None, "switch grant required")
        roots = list(self.grant["autoload_roots"])
        if self.launch["mode"] == "qualification":
            roots.append(self.launch["qualification_root"])
        for path in roots:
            switch.path_at(path)

    def qualify(self, paths, operation):
        switch.require(operation == self.operation and paths == list(map(str, self.physical())),
                       "provider qualification scope differs")
        self.provider.qualify(self.physical(), operation)

    def move(self, source, destination, operation):
        active, holding, *others = self.physical()
        allowed = [(str(active), str(holding))]
        if self.request["target"]["tag"] == "TARGET_TREE":
            allowed.append((str(others[0]), str(active)))
        row = current_fences(self.qs.command_journal(self.run)).get(self.request["scope"])
        switch.require(operation == self.operation and (source, destination) in allowed and row and
                       row["status"] == "SWITCH_CLOSED" and
                       row["fingerprint"] == self.request["operation_fingerprint"] and
                       self.continuity() == self.request["continuity"], "provider move outside live fence")
        self.provider.move(Path(source), Path(destination), operation)

    def dispatch(self, message):
        switch.fields(message, {"method", "args"})
        method = message["method"]
        switch.require(method in {"cut", "admit", "continuity", "close_guard", "recovery_admit",
                                  "release_guard", "restore_source", "discover", "observe_roots", "qualify", "move"}
                       and type(message["args"]) is list, "invalid coordinator method")
        return getattr(self, method)(*message["args"])


def execute_child(authority, runtime, manifest, command):
    manifest_path = authority.launch["runtime_manifest"]
    with authority.command_cut():
        return run_child(authority, runtime, manifest, command, manifest_path)


def run_child(authority, runtime, manifest, command, manifest_path):
    parent, child = socket.socketpair()
    argv = [manifest["interpreter"]["path"], "-I", "-B", str(runtime / ENTRY),
            authority.envelope_action, command["request"], authority.raw_sha,
            "--runtime-manifest", manifest_path, authority.request["runtime"],
            "--authority-fd", str(child.fileno())]
    with parent, child, subprocess.Popen(argv, pass_fds=(child.fileno(),), stdout=subprocess.PIPE,
                                          stdin=subprocess.DEVNULL) as process:
        child.close()
        try:
            with parent.makefile("rwb") as stream:
                while (message := switch.read_message(stream)) is not None:
                    if message == {"method": "checkpoint", "args": [command.get("interrupt_at")]}:
                        process.kill()
                        break
                    try:
                        if message.get("method") == "checkpoint":
                            switch.fields(message, {"method", "args"})
                            reply = {"result": None}
                        else:
                            reply = {"result": authority.dispatch(message)}
                        if message["method"] == "move":
                            request = authority.request
                            old_move = message["args"][0] == request["active"]
                            phase = ("N_TO_HOLDING" if request["target"]["tag"] == "VERIFIED_ABSENT"
                                     else "O_TO_ABSENT") if old_move else "ABSENT_TO_N"
                            if command.get("interrupt_at") == "after_provider_effect_before_ack_" + phase:
                                process.kill()
                                break
                    except (authority.qs.QSError, switch.Refused, ValueError, TypeError, OSError) as error:
                        reply = {"error": str(error)}
                    switch.send_message(stream, reply)
            output = switch.read_message(process.stdout)
            switch.require(process.wait() == 0 and type(output) is dict, "switch child interrupted")
            return output
        finally:
            if process.poll() is None:
                process.kill()


def check_released(launch, witness, receipt):
    """Revalidate the applicable committed cut under the ordinary claim locks."""
    journal = switch.path_at(launch["journal"])
    operation = receipt["operation_id"]
    intent = switch.decode(switch.read(switch.archive(journal, operation, "intent")))
    request = switch.validate(intent["request"])
    active, holding = (switch.path_at(request[key]) for key in ("active", "holding"))
    target, backup = request["target"].get("staged"), request.get("backup")
    physical = [active, holding] + [Path(bound["path"]) for bound in (target, backup) if bound]
    pointer, fence = (switch.path_at(launch[key]) for key in ("pointer", "fence"))
    cut = switch.readonly_cut(request, physical, journal, pointer, fence)
    post = switch.encoded(switch.pointer_value(request))
    post_sha = switch.digest(post)
    verified = switch.decode(switch.read(switch.archive(journal, operation, "verified")))
    switch.verify_discovery(request, post_sha, verified)
    return (
        receipt["fingerprint"] == request["operation_fingerprint"]
        and receipt["scope"] == request["scope"]
        and request["runtime"] == launch["runtime_sha256"]
        and all(request[key] == launch[key] for key in ("active", "journal", "pointer", "fence"))
        and witness.observe() == receipt["continuity"]
        and cut["pointer_sha256"] == receipt["pointer_sha256"] == post_sha
        and cut["fence_sha256"] == switch.ABSENT
        and switch.read(switch.archive(journal, operation, "commit")) ==
        switch.encoded(switch.commit_value(request, post_sha))
        and switch.matches(active, target)
        and switch.matches(holding, request["prior"])
        and (target is None or switch.matches(Path(target["path"]), None))
        and (backup is None or switch.matches(Path(backup["path"]), backup))
    )


def session(args, qs, launch, candidate):
    switch.require(type(launch) is dict and launch.get("mode") in {"qualification", "delivery"},
                   "unknown switch scope mode")
    switch.fields(launch, {"runtime_manifest", "runtime_sha256", "candidate_manifest", "candidate_sha256",
                           "root_agent_id", "active", "journal", "pointer", "fence", "mode"} |
                  ({"qualification_root"} if launch["mode"] == "qualification" else set()))
    switch.sha(launch["candidate_sha256"])
    qs.opaque_agent(launch["root_agent_id"])
    manifest_path = switch.path_at(launch["runtime_manifest"])
    runtime = manifest_path.parent
    manifest = switch.capture_runtime(manifest_path, launch["runtime_sha256"], {
        "runtime": launch["runtime_sha256"], "provider": switch.WINDOWS,
        "recovery_scope": switch.WINDOWS_SCOPE})
    switch.require(all(candidate["entries"][member] == raw_sha
                       for member, raw_sha in manifest["members"].items()),
                   "standalone runtime is outside the admitted candidate")
    run = qs.run_dir(qs.root(args.root), args.run_id)
    active = switch.canonical_path(launch["active"])
    policy = manifest["provider"]
    provider = switch.WindowsMove(policy["path"], policy["sha256"], native, policy["volume"])
    witness = NativeWitness(runtime, manifest, active)
    try:
        greeting = {"continuity": witness.initial}
        if witness.initial_error is not None:
            greeting["continuity_error"] = witness.initial_error
        switch.send_message(sys.stdout.buffer, greeting)
        while (command := switch.read_message(sys.stdin.buffer)) is not None:
            try:
                if isinstance(command, dict) and set(command) == {"claim_effect"}:
                    effect = argparse.Namespace(command="claim-effect", root=args.root,
                                                run_id=args.run_id, request=command["claim_effect"],
                                                external_dispatch=True)
                    result = qs.journal_command(effect, switch_guard=lambda receipt:
                                                check_released(launch, witness, receipt))
                    switch.send_message(sys.stdout.buffer, {"result": result})
                    continue
                required = {"request", "request_sha256", "original_sha256", "grant", "expected_revision"}
                switch.fields(command, required | ({"interrupt_at"} if "interrupt_at" in command else set()))
                if "interrupt_at" in command:
                    switch.require(launch["mode"] == "qualification" and
                                   command["interrupt_at"] in switch.QUALIFICATION_CUTS,
                                   "process interruption outside disposable qualification")
                envelope = switch.decode(pinned(command["request"], command["request_sha256"]))
                request = switch.validate(envelope, check_paths=False)
                switch.require(request["provider"] == switch.WINDOWS and
                               request["runtime"] == launch["runtime_sha256"] and
                               (envelope["action"] == "reconcile" or
                                command["request_sha256"] == command["original_sha256"]),
                               "operation runtime or original request binding differs")
                authority = Authority(qs, run, request, command["request_sha256"],
                                      command["original_sha256"], command["expected_revision"],
                                      envelope["action"], command["grant"],
                                      launch, witness, provider)
                result = execute_child(authority, runtime, manifest, command)
                switch.send_message(sys.stdout.buffer, {"result": result})
            except (qs.QSError, switch.Refused, ValueError, OSError, TypeError) as error:
                switch.send_message(sys.stdout.buffer, {"error": str(error)})
    finally:
        witness.close()
    return {"status": "SWITCH_SESSION_CLOSED"}
