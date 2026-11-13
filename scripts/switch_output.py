"""Hash binding of the standalone switch capability in portable OUTPUT04."""

import skill_generation_switch as switch

DESIGN = "a819ba8a42f06c1f566a8990c1cf80e6d926a8d9876585c054e2018433347081"
REVIEW = "ac23efcf707540004cfa72c843d25d223ed6d3573799619922e34bcdf35bee64"
ADOPTION = "4056a1bbec6c116ba7b291aa4619faf9e0d8734b637eabc588a369346961998d"
ENTRY = "scripts/skill_generation_switch.py"
SUPERVISOR = "scripts/continuity_supervisor.py"
SCHEMA = "schemas/skill-generation-switch.yaml"
OUTPUT = "switch-output.json"
ATTACHMENTS = {OUTPUT, "switch-runtime.json", "switch-request.json",
               "switch-fixture-runtime.json", "switch-fixture-request.json"}
PACKAGE_MEMBERS = {ENTRY, SUPERVISOR, SCHEMA, "scripts/switch_admission.py", "scripts/switch_output.py"}


def verify(read, subject):
    raw = read(OUTPUT)
    output = switch.decode(raw)
    switch.fields(output, {"id", "version", "provider", "recovery_scope", "design_sha256",
                           "review_sha256", "adoption_sha256", "selected_subject",
                           "runtime_sha256", "request_sha256", "fixture_runtime_sha256",
                           "fixture_request_sha256"})
    switch.require(output["id"] == "OUTPUT-QSSELF-04" and output["version"] == 3 and
                   output["provider"] == switch.WINDOWS and
                   output["recovery_scope"] == switch.WINDOWS_SCOPE and
                   output["design_sha256"] == DESIGN and output["review_sha256"] == REVIEW and
                   output["adoption_sha256"] == ADOPTION and
                   output["selected_subject"] == {key: subject[key] for key in
                                                  ("run_id", "input_snapshot", "plan", "task_id", "check_id")},
                   "switch output or current subject binding differs")
    for suffix, key, provider, scope, members in (
        ("", "", switch.WINDOWS, switch.WINDOWS_SCOPE, {ENTRY, SUPERVISOR}),
        ("-fixture", "fixture_", switch.FIXTURE, switch.FIXTURE_SCOPE, {ENTRY}),
    ):
        runtime_raw = read("switch" + suffix + "-runtime.json")
        request_raw = read("switch" + suffix + "-request.json")
        runtime, request = map(switch.decode, (runtime_raw, request_raw))
        switch.require(switch.digest(runtime_raw) == output[key + "runtime_sha256"] and
                       switch.digest(request_raw) == output[key + "request_sha256"] and
                       request["runtime"] == output[key + "runtime_sha256"] and
                       request["request_version"] == switch.REQUEST and
                       request["provider"] == runtime["provider"]["id"] == provider and
                       request["recovery_scope"] == runtime["provider"]["recovery_scope"] == scope and
                       request["operation_fingerprint"] == switch.fingerprint(request),
                       "switch exported runtime/request binding differs: " + provider)
        switch.require(request["action"] in {"activate", "restore"}, "invalid exported switch action")
        switch.validate(request, check_paths=False)
        runtime_keys = {"members", "interpreter", "provider"}
        if provider == switch.WINDOWS:
            runtime_keys.add("supervisor_interpreter")
        switch.fields(runtime, runtime_keys)
        for name in runtime_keys & {"interpreter", "supervisor_interpreter"}:
            pin = runtime[name]
            switch.fields(pin, {"path", "version", "sha256"})
            switch.canonical_path(pin["path"])
            switch.sha(pin["sha256"])
            switch.require(type(pin["version"]) is str and bool(pin["version"]),
                           "missing exported interpreter version")
        provider_keys = {"id", "recovery_scope"}
        if provider == switch.WINDOWS:
            provider_keys.update({"path", "sha256", "volume"})
            switch.canonical_path(runtime["provider"]["path"])
            switch.sha(runtime["provider"]["sha256"])
        switch.fields(runtime["provider"], provider_keys)
        switch.fields(runtime["members"], members)
        for member in members:
            switch.require(switch.digest(read(member)) == runtime["members"][member],
                           "switch exported member hash differs: " + member)
    return switch.digest(raw)
