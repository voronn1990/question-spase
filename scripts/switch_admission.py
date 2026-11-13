"""Coordinator binding for the standalone ICR-002-R3 generation switch."""

import argparse
import base64
from contextlib import suppress
from pathlib import Path
import re
import secrets
import socket
import subprocess
import stat
import sys

import skill_generation_switch as switch

FENCE = "skill-switch-fence"
ADOPTION = "4056a1bbec6c116ba7b291aa4619faf9e0d8734b637eabc588a369346961998d"
SUPERVISOR = "scripts/continuity_supervisor.py"
ENTRY = "scripts/skill_generation_switch.py"
BASELINE = "question-space-adopt-legacy-v1"
DELIVERY = "question-space-sealed-delivery-v1"
REPOSITORY = "voronn1990/question-spase"
SWITCH_PATHS = ("active", "journal", "pointer", "fence")
CHECKS = {"C01", "C02", "C03", "C04", "C05"}


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


def validate_effect_scope(declared, request):
    switch.fields(declared, {"profile", "active", "assignment_ids", "resource_keys", "sole_writer_paths"})
    switch.require(all(declared[name] == request[name] for name in ("profile", "active")),
                   "selected switch effect scope subject differs")
    switch.require(type(declared["assignment_ids"]) is list, "selected switch assignment IDs invalid")
    switch.require(type(declared["resource_keys"]) is list, "selected switch resource keys invalid")
    switch.require(type(declared["sole_writer_paths"]) is list, "selected switch writer paths invalid")


def selected_effects(qs, run, state, current, root_doc, request, physical, previous, *, external=False):
    scopes = root_doc.get("switch_effect_scopes")
    declared = scopes.get(request["scope"]) if isinstance(scopes, dict) else None
    switch.require(declared is not None, "selected switch effect scope missing")
    validate_effect_scope(declared, request)
    assignment_ids = declared["assignment_ids"]
    named = {qs.ident(identifier, "assignment_id") for identifier in assignment_ids}
    switch.require(len(named) == len(assignment_ids), "selected switch assignment IDs duplicate")
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
                   (external or request["logical_occurrence_id"] in effects),
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


def raw_reference(ref):
    switch.fields(ref, {"path", "sha256"})
    return pinned(ref["path"], ref["sha256"])


def sealed_subject(qs, source, request, physical, scope):
    switch.fields(source, {"root", "run_id", "release_sha256"})
    root = switch.path_at(source["root"])
    run = qs.run_dir(root, source["run_id"])
    release = pinned(str(run / "release.yaml"), source["release_sha256"])
    revision = qs.command_revision(run)
    state, current, bank = qs.accepted(run)
    switch.require(current.get("status") == "SEALED" and
                   current.get("release_sha256") == switch.digest(release),
                   "delivery source is not the pinned sealed release")
    checked = qs.verify(argparse.Namespace(root=str(root), run_id=run.name))
    switch.require(checked["status"] == "PASS", "delivery sealed verification failed")
    selected, plan = qs.plan_projection_for_run(run, state, current)
    (root_id,) = [rid for rid, stage in selected.items() if stage == "S-000"]
    entry, root_doc = qs._admitted_document(run, state, root_id, "PLAN")
    aid, tid = entry["assignment_id"], entry["attempt_id"]
    assignment = state["registered"][aid + "\0" + tid]
    switch.require(assignment["assignment"].get("selection_authority") == "ROOT" and
                   state["current_attempts"].get(aid) == tid and
                   qs._immutable_accepted_attempt_result(run, state, aid, tid) == root_id,
                   "delivery source root authority differs")
    stages = {}
    for number in ("04", "05", "06"):
        stage = "S-QSSELF-" + number
        (rid,) = [rid for rid, logical in selected.items() if logical == stage]
        row, doc = qs._admitted_document(run, state, rid, "PLAN")
        task, check = "T-QSSELF-" + number, "C-QSSELF-" + number
        switch.require(any(item.get("id") == task and check in item.get("checks", [])
                           for item in doc["tasks"]),
                       "delivery task/check not in sealed stage: " + stage)
        stages[stage] = {"id": rid, "sha256": row["content_hash"]}
    validate_effect_scope(scope, request)
    external_scope = {**scope, "assignment_ids": []}
    source_scopes = root_doc.get("switch_effect_scopes", {})
    switch.require(type(source_scopes) is dict, "sealed switch effect scopes invalid")
    if request["scope"] in source_scopes:
        source_scope = source_scopes[request["scope"]]
        validate_effect_scope(source_scope, request)
        # Assignment IDs are run-local. Physical and resource coverage is additive.
        external_scope["assignment_ids"] = source_scope["assignment_ids"]
        for key in ("resource_keys", "sole_writer_paths"):
            external_scope[key] = [*scope[key], *source_scope[key]]
    _, effects, unknown = selected_effects(
        qs, run, state, current, {"switch_effect_scopes": {request["scope"]: external_scope}},
        request, physical, None, external=True)
    latest = delivery_states(qs.command_journal(run))
    switch.require(not unknown and all(latest.get(name) != "UNKNOWN" for name in effects),
                   "delivery source has applicable UNKNOWN")
    graph_sha = qs.architecture_projection_for_run(run, state, current, bank)[
        "architecture_ref"]["graph_sha256"]
    switch.require(qs.command_revision(run) == revision and qs.load_state(run, "current") == current and
                   switch.read(run / "release.yaml") == release,
                   "delivery sealed source changed during verification")
    subject = {"input_snapshot": current["input_snapshot"], "plan_sha256": qs.sha(qs.canon_yaml(plan)),
               "graph_sha256": graph_sha,
               "authority_result_id": root_id, "authority_sha256": assignment["assignment_sha256"],
               "source_revision": revision, "stages": stages}
    return run, subject, plan, effects


def delivery_anchor(launch, run, request, *, create=False):
    delivery = launch["delivery"]
    namespace = switch.path_at(delivery["metadata_root"])
    identity = list(switch.path_identity(request["active"]))
    value = {"active_identity": identity, "run": str(run), "scope": request["scope"],
             "profile": request["profile"], "logical_occurrence_id": request["logical_occurrence_id"],
             "owner": delivery["owner"], "handoff": delivery["handoff"],
             "metadata_root": str(namespace),
             **{key: request[key] for key in SWITCH_PATHS}}
    path = namespace / ("active-" + switch.digest(switch.encoded(identity)) + ".json")
    switch.require(path not in (Path(request["pointer"]), Path(request["fence"])),
                   "delivery anchor aliases switch metadata")
    physical_id = request["prior"]["identity"] if request["prior"] else None
    if create:
        # A physical-root alias (including any backend-specific short spelling)
        # cannot acquire another anchor while the same legacy inode is active.
        with switch.locked(namespace / "delivery-anchor.lock"):
            for record in namespace.glob("active-*.json"):
                claimed = switch.decode(switch.read(record))
                switch.require(claimed["initial_identity"] != physical_id or
                               claimed["active_identity"] == identity, "delivery active physical alias")
            if switch.record(path) is None:
                switch.durable(path, switch.encoded({**value, "initial_identity": physical_id}))
    existing = switch.record(path)
    switch.require(existing is not None, "delivery active anchor absent")
    observed = switch.decode(existing)
    switch.fields(observed, set(value) | {"initial_identity"})
    switch.require(all(observed[k] == v for k, v in value.items()) and
                   (not create or observed["initial_identity"] == physical_id),
                   "delivery active anchor rekeyed")
    if create:
        switch.flush_dir(namespace)
    return path


def product_inventory(root, manifest_sha, *, modes=None, legacy=False):
    """Use the package manifest for files and switch.tree for physical custody."""
    path = switch.path_at(str(root))
    manifest_raw = pinned(str(path / "manifest.json"), manifest_sha)
    manifest = switch.decode(manifest_raw)
    entries = manifest["entries"]
    metadata = manifest["metadata"]
    required = {"SKILL.md", "release.yaml"} if legacy else {"README.md", "SKILL.md", "release.yaml"}
    switch.require(manifest["kind"] == "skill" and
                   manifest["format"] == "question-space-portable-package-v1" and
                   metadata["name"] == "question-space" and
                   (not legacy or metadata["version"] == "4.2.2") and
                   type(entries) is dict and required <= entries.keys() and
                   "manifest.json" not in entries, "invalid delivery product manifest")
    expected = {**entries, "manifest.json": manifest_sha}
    for name, digest in expected.items():
        part = Path(name)
        switch.require(not part.is_absolute() and str(part) == name and
                       ".." not in part.parts and bool(part.parts), "invalid product member")
        pinned(str(path / part), digest)
    if not legacy:
        parents = {parent.as_posix() for name in expected for parent in Path(name).parents
                   if parent != Path(".")}
        switch.require({item.relative_to(path).as_posix() for item in path.rglob("*")} ==
                       set(expected) | parents, "delivery product inventory differs")
    if modes is not None:
        switch.require(type(modes) is dict and modes.keys() == expected.keys() and
                       all(mode in {"100644", "100755"} and
                           bool((path / name).stat().st_mode & stat.S_IXUSR) == (mode == "100755")
                           for name, mode in modes.items()), "retrieved Git member mode differs")
    observed = switch.tree(path)
    return expected, observed


def validate_baseline(request):
    switch.fields(request, switch.COMMON | {"backup", "legacy_manifest_sha256"})
    switch.require(request["request_version"] == BASELINE and request["action"] == "adopt_legacy" and
                   request["provider"] == switch.WINDOWS and request["recovery_scope"] == switch.WINDOWS_SCOPE and
                   type(request["expected_generation"]) is int and request["expected_generation"] == 0 and
                   type(request["target_generation"]) is int and request["target_generation"] == 1 and
                   request["expected_pointer_sha256"] == switch.ABSENT,
                   "invalid legacy adoption cut")
    for key in ("operation_id", "scope", "profile"):
        switch.token(request[key])
    switch.occurrence_id(request["logical_occurrence_id"])
    for key in ("runtime", "operation_fingerprint", "legacy_manifest_sha256"):
        switch.sha(request[key])
    switch.continuity_binding(request["continuity"])
    for key in (*SWITCH_PATHS, "holding", "occurrence_lock", "run_lock"):
        switch.canonical_path(request[key])
    prior, backup = request["prior"], request["backup"]
    switch.binding(prior, check_paths=False)
    switch.binding(backup, check_paths=False)
    switch.require(prior["path"] == request["active"] and backup["identity"] != prior["identity"] and
                   backup["manifest_sha256"] == prior["manifest_sha256"] and
                   request["operation_fingerprint"] == switch.fingerprint(request),
                   "invalid legacy tree binding")
    return request


def baseline_pointer(request):
    return switch.encoded(switch.pointer_value({
        **request, "target": {"tag": "TARGET_TREE", "staged": request["prior"]}}))


def baseline_files(journal, operation):
    # Standalone's readonly_cut reserves *.intent.json for its unchanged wire.
    return [switch.archive(journal, operation, tag) for tag in ("baseline", "baseline-commit")]


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
        bound = self.request.get("target", {}).get("staged")
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
        for name in SWITCH_PATHS:
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
        if "delivery" in self.launch:
            delivery = self.launch["delivery"]
            extra = [switch.canonical_path(self.launch["candidate_manifest"]),
                     switch.canonical_path(delivery["owner"]["path"])]
            switch.require(not any(switch.contains_path(root, switch.path_identity(path))
                                   for path in extra for root in autoload),
                           "delivery authority source overlaps autoload")
            metadata.extend(extra)
            namespace = switch.canonical_path(delivery["metadata_root"])
            namespace_id = switch.path_identity(namespace)
            switch.require(namespace_id[:2] != ("mnt", "c") and
                           not any(switch.contains_path(root, namespace_id) or
                                   switch.contains_path(namespace_id, root) for root in autoload),
                           "delivery metadata is outside its trusted Linux namespace")
            for name in ("journal", "pointer", "fence"):
                identity = switch.path_identity(request[name])
                switch.require(namespace_id != identity and switch.contains_path(namespace_id, identity),
                               "delivery metadata path escapes its namespace: " + name)
            switch.check_path_separation(physical, [namespace])
        switch.check_path_separation(physical, metadata)

    def delivery_handoff(self, state, grant, grant_entry):
        qs, run, request = self.qs, self.run, self.request
        launch = self.launch
        delivery = launch["delivery"]
        handoff_entry, handoff = accepted_evidence(
            qs, run, state, delivery["handoff"], "switch_delivery_handoff")
        switch.fields(handoff, {"version", "source", "sealed", "candidate_sha256", "repository",
                                "ref", "owner", "metadata_root", "scope", "logical_occurrence_id",
                                "effect_scope", "paths", "autoload_roots", "predecessors"})
        switch.require(handoff_entry["agent_id"] == launch["root_agent_id"] == grant_entry["agent_id"] and
                       handoff["version"] == DELIVERY and
                       handoff["candidate_sha256"] == launch["candidate_sha256"] and
                       all(handoff[key] == delivery[key] for key in
                           ("owner", "repository", "ref", "metadata_root")) and
                       all(handoff[key] == request[key] for key in ("scope", "logical_occurrence_id")) and
                       handoff["paths"] == {key: request[key] for key in SWITCH_PATHS} and
                       all(handoff[key] == grant[key] for key in ("autoload_roots", "predecessors")),
                       "delivery handoff does not bind the protected subject")
        raw_reference(handoff["owner"])
        switch.require(delivery["source"] == handoff["source"], "delivery source pin differs")
        source_run, subject, plan, effects = sealed_subject(
            qs, handoff["source"], request, self.physical(), handoff["effect_scope"])
        switch.require(source_run != run and handoff["sealed"] == subject and
                       all(grant[key] == subject[key] for key in (
                           "plan_sha256", "graph_sha256", "authority_result_id", "authority_sha256")),
                       "delivery handoff differs from sealed authority")
        self.delivery_subject = {"run_id": source_run.name, "input_snapshot": subject["input_snapshot"],
                                 "plan": plan, "task_id": "T-QSSELF-04", "check_id": "C-QSSELF-04"}
        self.source_effects, self.source_revision = effects, subject["source_revision"]
        return {"switch_effect_scopes": {request["scope"]: handoff["effect_scope"]}}, {handoff_entry["result_id"]}

    def predecessor(self, check, acceptance):
        expected = {"check": check, "verdict": "PASS", "candidate_sha256": self.launch["candidate_sha256"]}
        if "delivery" not in self.launch:
            return acceptance == expected
        switch.fields(acceptance, set(expected) | {"receipt"} |
                      ({"output"} if check in {"C04", "C05"} else set()))
        raw_reference(acceptance["receipt"])
        if check == "C04":
            from switch_output import verify as verify_output

            output = acceptance["output"]
            switch.fields(output, {"path", "sha256"})
            directory = switch.path_at(output["path"])
            read = lambda name: switch.read(directory / name)
            switch.require(verify_output(read, self.delivery_subject) == output["sha256"],
                           "delivery OUTPUT-QSSELF-04 differs")
            recorded = switch.decode(read("switch-runtime.json"))
            live = switch.decode(pinned(self.launch["runtime_manifest"], self.launch["runtime_sha256"]))
            switch.require(recorded["members"] == live["members"] and
                           recorded["provider"]["id"] == live["provider"]["id"] and
                           recorded["provider"]["recovery_scope"] == live["provider"]["recovery_scope"],
                           "delivery runtime differs from accepted OUTPUT-QSSELF-04")
        if check == "C05":
            output = acceptance["output"]
            switch.fields(output, {"id", "readme_sha256", "review"})
            manifest = switch.decode(pinned(self.launch["candidate_manifest"], self.launch["candidate_sha256"]))
            switch.require(output["id"] == "OUTPUT-QSSELF-05" and
                           output["readme_sha256"] == manifest["entries"]["README.md"],
                           "delivery OUTPUT-QSSELF-05 README differs")
            raw_reference(output["review"])
        return all(acceptance[key] == value for key, value in expected.items())

    def delivery_quiescence(self, observed, expected):
        switch.fields(observed, set(expected) | {"external", "source_revision", "source_effects"})
        external = observed["external"]
        switch.fields(external, {"receipt", "effects"})
        raw_reference(external["receipt"])
        switch.require(type(external["effects"]) is list, "external applicable effects were not observed")
        names = set()
        for effect in external["effects"]:
            switch.fields(effect, {"id", "state"})
            name = switch.occurrence_id(effect["id"])
            switch.require(name not in names and effect["state"] in {"RECONCILED", "TERMINAL", "QUIESCENT"},
                           "delivery external applicable effect unresolved")
            names.add(name)
        return (all(observed[key] == value for key, value in expected.items()) and
                observed["source_revision"] == self.source_revision and
                observed["source_effects"] == self.source_effects)

    def admit(self, fingerprint, raw_sha):
        qs, run, request = self.qs, self.run, self.request
        switch.require(fingerprint == request["operation_fingerprint"] and raw_sha == self.raw_sha,
                       "switch request identity differs")
        state, current, bank, journal = self.journal_adapter.read_committed_locked()
        switch.require(current.get("status") != "SEALED", "sealed_run_command_refused")
        switch.require(self.launch["mode"] != "delivery" or "delivery" in self.launch,
                       "protected delivery launch requires a sealed handoff")
        switch.require(qs.command_revision(run,journal) == self.expected_revision,
                       "switch expected command cut changed")
        entry, grant = accepted_evidence(qs, run, state, self.grant_ref, "switch_grant")
        delivery = "delivery" in self.launch
        switch.fields(grant, {"adoption", "mode", "input_snapshot", "plan_sha256", "graph_sha256",
                              "authority_result_id", "authority_sha256", "request_sha256",
                              "quiescence", "restore_source", "recovery", "discovery",
                              "predecessors", "effects", "active", "journal", "pointer",
                              "fence", "autoload_roots"} |
                      ({"publication", "legacy", "bootstrap_recovery"} if delivery else set()))
        switch.require(entry["agent_id"] == self.launch["root_agent_id"] and
                       current["input_snapshot"] == grant["input_snapshot"] and
                       grant["adoption"] == ADOPTION and grant["mode"] == self.launch["mode"] and
                       grant["request_sha256"] == self.original_sha,
                       "switch grant authority or exact cut differs")
        self.validate_paths(grant)
        if delivery:
            switch.require(current["status"] == "INITIALIZED" and
                           all(item["assignment"]["artifact_type"] == "EVIDENCE"
                               for item in state["registered"].values()),
                           "delivery needs an initialized evidence-only execution run")
            root_doc, dependencies = self.delivery_handoff(state, grant, entry)
        else:
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
            switch.require(assignment["assignment"].get("selection_authority") == "ROOT" and
                           assignment["assignment_sha256"] == grant["authority_sha256"] and
                           state["current_attempts"].get(pair[0]) == pair[1] and
                           qs._immutable_accepted_attempt_result(run, state, *pair) == roots[0] and
                           graph["graph_sha256"] == grant["graph_sha256"],
                           "switch grant authority or exact cut differs")
            _, root_doc = qs._admitted_document(run, state, roots[0], "PLAN")
            dependencies = {*selected, graph["synthesis_id"]}
        blocked = set(current.get("stale_result_ids", []))
        for affected in qs.intake_affected_results(run, state, journal):
            blocked.update(affected)
        switch.fields(grant["predecessors"], CHECKS if grant["mode"] == "delivery" else {"C01", "C02", "C03"})
        dependencies.add(entry["result_id"])
        for check, reference in grant["predecessors"].items():
            proof, acceptance = accepted_evidence(qs, run, state, reference, "switch_predecessor")
            switch.require(proof["agent_id"] != entry["agent_id"] and self.predecessor(check, acceptance),
                           "switch predecessor not applicable: " + check)
            dependencies.add(proof["result_id"])
        fences = current_fences(journal)
        previous = fences.get(request["scope"])
        selector, effects, native_unknown = selected_effects(
            qs, run, state, current, root_doc, request, self.physical(), previous)
        switch.require(effect_ids(grant["effects"]) == set(effects), "switch effect scope incomplete")
        proof, observed = accepted_evidence(qs, run, state, grant["quiescence"], "switch_quiescence")
        quiet_witness = self.continuity() if self.envelope_action == "adopt_legacy" else request["continuity"]
        switch.continuity_binding(quiet_witness)
        quiet = {"profile": request["profile"], "active": request["active"],
                 "request_sha256": self.original_sha, "cached_writers": "QUIESCENT",
                 "client_writers": "QUIESCENT", "session_id": quiet_witness["session_id"],
                 "effects": effects, "effect_scope_sha256": qs.sha(qs.canon_yaml(selector))}
        switch.require(proof["agent_id"] != entry["agent_id"] and
                       (self.delivery_quiescence(observed, quiet) if delivery else observed == quiet),
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
        self.quiet_witness = quiet_witness
        if delivery and self.envelope_action != "adopt_legacy":
            delivery_anchor(self.launch, run, request)
            switch.require(request["prior"] is not None and request["target"]["tag"] == "TARGET_TREE" and
                           request["target_generation"] == request["expected_generation"] + 1,
                           "delivery must advance an installed generation by one")
            self.check_baseline_successor(previous, switch.record(Path(request["pointer"])))
            if request["action"] == "activate":
                self.verify_publication()
            else:
                self.verify_legacy_restore()
        return True

    def verify_publication(self):
        ref = self.grant["publication"]
        switch.require(ref is not None, "independent delivery publication required")
        entry, publication = accepted_evidence(
            self.qs, self.run, self.state, ref, "switch_publication")
        switch.fields(publication, {"repository", "ref", "commit", "receipt", "retrieved_tree",
                                    "candidate_sha256", "tree_sha256", "git_modes"})
        delivery = self.launch["delivery"]
        switch.require(entry["agent_id"] != self.grant_entry["agent_id"] and
                       entry["result_id"] not in self.blocked and
                       all(publication[key] == delivery[key] for key in ("repository", "ref")) and
                       publication["candidate_sha256"] == self.launch["candidate_sha256"] and
                       re.fullmatch(r"[0-9a-f]{40}", publication["commit"]) is not None,
                       "delivery publication subject differs")
        raw_reference(publication["receipt"])
        retrieved = switch.path_at(publication["retrieved_tree"])
        # Git modes apply to independently retrieved Linux bytes. The qualified
        # Windows physical tree digest carries exact files/directories and raw SHA.
        switch.require(switch.path_identity(retrieved)[:2] != ("mnt", "c"),
                       "retrieved Git modes need a protected Linux tree")
        self.check_extra_protected_paths([retrieved])
        _, tree = product_inventory(retrieved, publication["candidate_sha256"],
                                    modes=publication["git_modes"])
        staged = self.request["target"]["staged"]
        switch.require(tree["manifest_sha256"] == publication["tree_sha256"] ==
                       staged["manifest_sha256"], "staged delivery differs from reviewed publication")

    def check_extra_protected_paths(self, paths):
        for path in paths:
            identity = switch.path_identity(path)
            for path_text in self.grant["autoload_roots"]:
                root = switch.path_identity(path_text)
                switch.require(not switch.contains_path(root, identity) and
                               not switch.contains_path(identity, root),
                               "delivery evidence surface overlaps autoload")
        switch.check_path_separation(self.physical(), paths)

    def committed_baseline(self, operation=None):
        if operation is None:
            receipts = sorted(self.journal_dir.glob("*.baseline-commit.json"))
            switch.require(len(receipts) == 1, "delivery needs one committed legacy baseline")
            operation = receipts[0].name.removesuffix(".baseline-commit.json")
        switch.token(operation)
        intent_path, commit_path = baseline_files(self.journal_dir, operation)
        intent_raw = switch.read(intent_path)
        intent = switch.decode(intent_raw)
        switch.fields(intent, {"request", "request_sha256", "post_pointer"})
        baseline = validate_baseline(intent["request"])
        switch.sha(intent["request_sha256"])
        raw = baseline_pointer(baseline)
        switch.require(baseline["operation_id"] == operation and
                       all(baseline[key] == self.request[key] for key in
                           (*SWITCH_PATHS, "scope", "profile", "logical_occurrence_id")) and
                       intent["post_pointer"] == switch.digest(raw) and
                       switch.read(commit_path) == switch.encoded({
                           **switch.commit_value(baseline, switch.digest(raw)),
                           "intent_sha256": switch.digest(intent_raw)}),
                       "delivery legacy baseline receipt differs")
        return baseline, raw

    def check_baseline_successor(self, before, pointer):
        if "delivery" not in self.launch or before is None or before["status"] != "SWITCH_CLOSED":
            return False
        operation = before["operation_id"]
        intent_path, _ = baseline_files(self.journal_dir, operation)
        if switch.record(intent_path) is None:
            return False
        request = self.request
        switch.require(request["action"] == "activate" and self.operation != operation,
                       "first delivery switch must activate the reviewed release")
        baseline, raw = self.committed_baseline(operation)
        switch.require(raw == pointer and before["pointer_sha256"] == switch.ABSENT and
                       before["fingerprint"] == baseline["operation_fingerprint"] and
                       before["logical_occurrence_id"] == self.occurrence and
                       baseline["prior"] == request["prior"] and baseline["backup"] == request.get("backup") and
                       request["expected_generation"] == 1 and request["target_generation"] == 2 and
                       request["expected_pointer_sha256"] == switch.digest(raw),
                       "first activation differs from committed legacy baseline")
        return True

    def verify_legacy_restore(self):
        baseline, _ = self.committed_baseline()
        switch.require(self.request["target"]["staged"]["manifest_sha256"] ==
                       baseline["prior"]["manifest_sha256"], "restore differs from adopted legacy tree")

    def adopt_legacy(self, checkpoint=lambda _: None):
        """Initialize the pointer of the observed installed tree without moving it."""
        request = self.request
        switch.require("delivery" in self.launch and self.envelope_action == "adopt_legacy",
                       "legacy adoption requires sealed delivery authority")
        validate_baseline(request)
        active, holding, backup = self.physical()
        journal, pointer, fence = map(Path, (request["journal"], request["pointer"], request["fence"]))
        intent_path, commit_path = baseline_files(journal, self.operation)
        post = baseline_pointer(request)
        post_sha = switch.digest(post)
        intent = {"request": request, "request_sha256": self.original_sha, "post_pointer": post_sha}
        encoded_intent = switch.encoded(intent)
        result = {**switch.commit_value(request, post_sha), "intent_sha256": switch.digest(encoded_intent)}
        with self.command_cut():
            self.admit(request["operation_fingerprint"], self.raw_sha)
            self.observe_roots()
            for path in (active, holding, backup, journal, pointer, fence):
                switch.path_at(str(path))
            metadata = {journal, pointer.parent, fence.parent, Path(self.launch["delivery"]["metadata_root"])}
            switch.require({path.parent.stat().st_dev for path in self.physical()}.isdisjoint(
                {path.stat().st_dev for path in metadata}), "Windows physical and Linux metadata surfaces overlap")
            old = switch.record(intent_path)
            current = switch.record(pointer)
            committed = switch.record(commit_path)
            switch.require(all(path in (intent_path, commit_path) for path in
                               journal.glob("*.baseline*.json")) and
                           switch.record(switch.archive(journal, self.operation, "intent")) is None and
                           old in (None, encoded_intent) and current in (None, post) and
                           (old is not None or current is None) and
                           (committed is None or (current == post and committed == switch.encoded(result))) and
                           switch.record(fence) is None, "legacy adoption pointer, intent or fence conflicts")
            before = self.fence_before
            switch.require((current is None or before is not None) and
                           (before is None or (old == encoded_intent and
                           before["status"] == "SWITCH_CLOSED" and
                           before["effect_scope"] == self.effect_scope and
                           before["logical_occurrence_id"] == self.occurrence and
                           before["operation_id"] == self.operation and
                           before["fingerprint"] == request["operation_fingerprint"] and
                           before["pointer_sha256"] == switch.ABSENT)),
                           "legacy adoption conflicts with an existing shared fence")

            def verify_cut(expected_pointer, witness):
                switch.require(self.continuity() == witness and
                               switch.record(pointer) == expected_pointer and
                               switch.record(fence) is None and
                               switch.matches(active, request["prior"]) and
                               switch.matches(backup, request["backup"]) and switch.matches(holding, None) and
                               self.continuity() == witness, "legacy adoption live cut changed")

            witness = self.continuity()
            switch.continuity_binding(witness)
            switch.require(witness == self.quiet_witness and self.provider.name == switch.WINDOWS and
                           witness["volume_guid"].casefold() == self.provider.volume.casefold(),
                           "legacy adoption native witness differs")
            if witness != request["continuity"]:
                cut = switch.readonly_cut(request, self.physical(), journal, pointer, fence)
                cuts = [cut]
                if current == post:
                    # The deterministic stages this exact request can already
                    # have published are its proposed pointer and receipt.
                    pre_commit = {**cut, "journal": {key: digest for key, digest in cut["journal"].items()
                                                    if key != commit_path.name}}
                    cuts.extend([pre_commit, {**pre_commit, "pointer_sha256": switch.ABSENT}])
                proof = {"continuity": witness, "request_sha256": self.original_sha,
                         "intent_sha256": switch.digest(encoded_intent), "proposed_pointer_sha256": post_sha}
                switch.require(old == encoded_intent and any(self.proof("bootstrap_recovery", {
                    **proof, "cut_sha256": switch.digest(switch.encoded(view))}) for view in cuts),
                    "legacy adoption needs exact independently accepted recovery")
            verify_cut(current, witness)
            _, legacy = product_inventory(active, request["legacy_manifest_sha256"], legacy=True)
            switch.require(legacy == {key: request["prior"][key] for key in ("identity", "manifest_sha256")},
                           "legacy manifest and observed installation differ")
            candidate = switch.decode(pinned(self.launch["candidate_manifest"], self.launch["candidate_sha256"]))
            switch.require(candidate["entries"].get("history/4.2.2/manifest.json") ==
                           request["legacy_manifest_sha256"], "legacy manifest is outside the reviewed history")
            verify_cut(current, witness)
            switch.require(self.proof("legacy", {
                "request_sha256": self.original_sha, "prior": request["prior"], "backup": request["backup"],
                "legacy_manifest_sha256": request["legacy_manifest_sha256"], "authoritative_pointer": switch.ABSENT,
            }), "independent legacy backup or authoritative pointer observation differs")
            self.provider.qualify(self.physical(), self.operation)
            for parent in sorted(metadata):
                switch.qualify_metadata(parent)
            verify_cut(current, witness)
            if witness != request["continuity"]:
                switch.require(switch.readonly_cut(request, self.physical(), journal, pointer, fence) == cut,
                               "legacy recovery physical cut changed")
            delivery_anchor(self.launch, self.run, request, create=True)
            switch.immutable(intent_path, intent)
            self.close_guard()
            checkpoint("before_baseline_CAS")
            verify_cut(current, witness)
            if current is None:
                switch.durable(pointer, post, replace=False)
            else:
                switch.flush_dir(pointer.parent)
            checkpoint("after_baseline_CAS")
            verify_cut(post, witness)
            switch.immutable(commit_path, result)
            return {"status": "LEGACY_ADOPTED_FENCED", "commit": result}

    def continuity(self):
        return self.witness.observe()

    def check_fence_close(self, before):
        request = self.request
        pointer = switch.record(Path(request["pointer"]))
        if self.envelope_action == "adopt_legacy":
            intent_path, _ = baseline_files(self.journal_dir, self.operation)
            intent = switch.decode(switch.read(intent_path))
            post = baseline_pointer(request)
            switch.require(intent == {"request": request, "post_pointer": switch.digest(post),
                                      "request_sha256": self.original_sha} and pointer in (None, post),
                           "legacy fence request lineage differs")
            return
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
        elif not self.check_baseline_successor(before, pre):
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
        action = self.envelope_action
        if action != "restore" and not ("delivery" in self.launch and action == "activate"):
            return False
        if switch.record(switch.archive(self.journal_dir, self.operation, "intent")) is not None:
            return False
        pointer, fence = map(Path, (self.request["pointer"], self.request["fence"]))
        local = switch.readonly_cut(self.request, self.physical(), self.journal_dir, pointer, fence)
        verified = (witness == self.request["continuity"] and cut == local and
                    cut_sha == switch.digest(switch.encoded(local)) and
                    self.proof("recovery", {"continuity": witness, "cut_sha256": cut_sha,
                                            "request_sha256": self.original_sha}))
        return verified and (action == "restore" or self.activation_predecessor(switch.read(pointer), local))

    def activation_predecessor(self, pointer, cut):
        before = self.fence_before
        if before is None or cut["fence_sha256"] != switch.ABSENT:
            return False
        operation = before["operation_id"]
        if before["status"] == "SWITCH_CLOSED":
            if switch.record(baseline_files(self.journal_dir, operation)[1]) is None:
                return False
            baseline, raw = self.committed_baseline(operation)
            return (raw == pointer and baseline["backup"] == self.request.get("backup") and
                    baseline["prior"] == self.request["prior"])
        intent = switch.decode(switch.read(switch.archive(self.journal_dir, operation, "intent")))
        predecessor = switch.validate(intent["request"], check_paths=False)
        if predecessor["action"] != "restore" or predecessor["target"]["tag"] != "TARGET_TREE":
            return False
        post = switch.encoded(switch.pointer_value(predecessor))
        digest = switch.digest(post)
        discovery = switch.decode(switch.read(switch.archive(self.journal_dir, operation, "verified")))
        switch.verify_discovery(predecessor, digest, discovery)
        target, backup = predecessor["target"]["staged"], predecessor.get("backup")
        return (post == pointer and digest == before["pointer_sha256"] and
                predecessor["operation_fingerprint"] == before["fingerprint"] and
                all(predecessor[key] == self.request[key] for key in
                    (*SWITCH_PATHS, "scope", "profile", "logical_occurrence_id")) and
                switch.read(switch.archive(self.journal_dir, operation, "commit")) ==
                switch.encoded(switch.commit_value(predecessor, digest)) and
                switch.matches(Path(predecessor["active"]), target) and
                switch.matches(Path(predecessor["holding"]), predecessor["prior"]) and
                switch.matches(Path(target["path"]), None) and
                (backup is None or switch.matches(Path(backup["path"]), backup)) and
                target["manifest_sha256"] == self.committed_baseline()[0]["prior"]["manifest_sha256"])

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
                           "root_agent_id", *SWITCH_PATHS, "mode"} |
                  ({"qualification_root"} if launch["mode"] == "qualification" else {"delivery"}))
    if launch["mode"] == "delivery":
        delivery = launch["delivery"]
        switch.fields(delivery, {"source", "owner", "repository", "ref", "metadata_root", "handoff"})
        switch.require(delivery["repository"] == REPOSITORY and
                       re.fullmatch(r"refs/(?:heads|tags)/[A-Za-z0-9._/-]+", delivery["ref"]) is not None,
                       "delivery repository or ref differs")
        switch.canonical_path(delivery["metadata_root"])
        raw_reference(delivery["owner"])
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
                    switch.require(launch["mode"] != "delivery", "delivery runs only switch effects")
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
                if isinstance(envelope, dict) and envelope.get("action") == "adopt_legacy":
                    switch.require(launch["mode"] == "delivery", "legacy adoption outside delivery")
                    request = validate_baseline(envelope)
                else:
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
                result = (authority.adopt_legacy() if envelope["action"] == "adopt_legacy" else
                          execute_child(authority, runtime, manifest, command))
                switch.send_message(sys.stdout.buffer, {"result": result})
            except (qs.QSError, switch.Refused, ValueError, OSError, TypeError) as error:
                switch.send_message(sys.stdout.buffer, {"error": str(error)})
    finally:
        witness.close()
    return {"status": "SWITCH_SESSION_CLOSED"}
