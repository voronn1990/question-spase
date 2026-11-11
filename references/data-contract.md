# Локальный YAML-контракт v4

`scripts/qs.py` является небольшим локальным контроллером. Он не запускает
агентов и не доказывает предметную правильность. Его `PASS` всегда означает
только `STRUCTURAL_ONLY`.

## Run и неизменяемый вход

```text
runs/<run_id>/
  input/original-bank.<ext>  # если приложен исходник
  input/bank.yaml            # разобранный исходный банк, никогда не результаты
  input/snapshot.yaml        # неизменяемый дескриптор
  state/{current,assignments,events}.yaml
  state/release-cores/<release_id>.yaml
  gates/GATE-1.yaml ... gates/GATE-5.yaml and gates/GATE-6/<audit-result-id>.yaml
  positions/ answers/source-outcomes/<result_id>.yaml hypotheses/ evidence/ decisions/ reviews/
  operators/ experiments/ synthesis/ plan/ audit/final/<result_id>.yaml
  release.yaml
```

`input_snapshot` равен SHA-256 точных байтов `input/snapshot.yaml`.
Дескриптор содержит SHA-256 исходного parsed bank, канонических requirements и,
если он передан, точных байтов raw source. `assign`, `accept`, `recover`,
`validate` и `seal` повторно проверяют эти связи. Изменение входа требует
нового run, а не переписывания снимка.

Все YAML читаются SafeLoader-подклассом: duplicate keys, aliases, anchors,
explicit tags и неявные числовые/boolean/date/null scalars отвергаются.
Идентификаторы, версии и даты передаются как quoted strings. `agent_id` —
непустая opaque string, поэтому реальный native ID `/root/name` допустим и
никогда не применяется как путь.

## Команды

```text
python3 scripts/qs.py init --root WORKSPACE --run-id R --bank bank.yaml --original-bank raw.md
python3 scripts/qs.py init --root WORKSPACE --run-id R --mode GOAL_TO_PLAN --goal goal.md [--material fact.md ...] [--test-fixture]
python3 scripts/qs.py init --root WORKSPACE --run-id R --mode BANK_TO_PLAN --goal goal.md --bank bank.yaml --approved-vector vector.yaml --vector-approval approval.yaml --approval-statement statement.md [--material fact.md ...] [--test-fixture]
python3 scripts/qs.py init --root WORKSPACE --run-id R --mode BANK_TO_PLAN --goal goal.md --bank bank.yaml --external-bank original-bank.md [--material fact.md ...] [--test-fixture]
python3 scripts/qs.py approve-vector --root WORKSPACE --run-id R --approval approval.yaml
python3 scripts/qs.py publish-perspective-map --root WORKSPACE --run-id R --perspective-map root-map.yaml
python3 scripts/qs.py admit-bank --root WORKSPACE --run-id R --bank bank.yaml --admission admission.yaml [--test-fixture]
python3 scripts/qs.py assign --root WORKSPACE --run-id R --assignment assignment.yaml
python3 scripts/qs.py accept --root WORKSPACE --run-id R --assignment assignment.yaml --result result.yaml
python3 scripts/qs.py commit --root WORKSPACE --run-id R --assignment assignment.yaml --result result.yaml
python3 scripts/qs.py context --root WORKSPACE --run-id R --assignment assignment.yaml --output context.yaml [--expand RESULT_ID ...]
python3 scripts/qs.py context --root WORKSPACE --run-id R --assignment assignment.yaml --output history.yaml --historical [--packet-sha256 HASH]
python3 scripts/qs.py validate --root WORKSPACE --run-id R [--phase core|final]
python3 scripts/qs.py recover --root WORKSPACE --run-id R [--bootstrap-legacy]
python3 scripts/qs.py resume --root WORKSPACE --run-id R [--changed EVIDENCE_RESULT_ID ...] [--approved-map root-map.yaml]
python3 scripts/qs.py launch-intent --root WORKSPACE --run-id R --assignment-id A --attempt-id ATT --state NOT_LAUNCHED|READY_TO_DISPATCH|DISPATCH_UNKNOWN
python3 scripts/qs.py seal --root WORKSPACE --run-id R --release-id REL --phase prepare|finalize
python3 scripts/qs.py verify --root WORKSPACE --run-id R
python3 scripts/qs.py render --source object.yaml --output view.md
python3 scripts/qs.py render --root WORKSPACE --run-id R --architecture-view functional|data|implementation --output view.md [--subsystem ID] [--goal-path] [--changed-node ID ...]
```

`assign` durably registers an exact assignment before any result is accepted.
`accept` accepts one exact assignment/attempt/result tuple; changed bytes for
the same pair fail. It records immutable result metadata and an event. A crash
after registration is reported by `recover` as a pending assignment; completed
delivery is idempotent only for the same bytes.

## Подготовка GOAL_TO_PLAN и BANK_TO_PLAN

Новые режимы имеют точные `goal_path`/`goal_sha256`, materials и
`bank_binding` в immutable snapshot. В `GOAL_TO_PLAN` банк отсутствует до
admission. В `BANK_TO_PLAN` approved prior-run import копирует bank, Vector,
APPROVE и statement один раз в `input/imports/<import_id>/`. Plain external
bank uses `--external-bank ORIGINAL`; foreign import flags are forbidden,
snapshot records `bank_entry_kind: EXTERNAL_READY_BANK` and exact
`external_binding` with `schema_version`, `source_kind`, `current_run_id`,
`original_path`, `original_sha256`, `normalized_path`, `normalized_sha256`,
`external_payload_sha256`. Both branches hash local exact bytes, never the
external path. External intake then uses local OP-VECTOR, a genuine approval
record only for actual authority (or transparently declared fixture authority
inside an authorized test), perspective map, local `BANK_APPLICABILITY` and
`admit-bank` with `generation_performed: NO`.

`approve-vector` разрешён для `GOAL_TO_PLAN` и `BANK_TO_PLAN/EXTERNAL_READY_BANK` после local OP-VECTOR в `PREP_OPEN`; `APPROVED_RUN_IMPORT` переиспользует exact foreign approval через applicability и не создаёт новый. APPROVE
связывает один accepted OP-VECTOR с точными hash/revision/statement. Для
`SIMULATED_TEST` нужны `fixture_only: "YES"` и
`approver_id: "ROOT-AS-SIMULATED_TEST_OWNER"`; `ACTUAL_OWNER` хранит
адресуемое observation provenance, без утверждения, что контроллер
аутентифицировал человека.

До P-работы Root публикует одну immutable `input/perspective-map.yaml` через
`publish-perspective-map`: P-01…P-10, десять разных agent_id, binding к
Vector/APPROVE и `profile_sha256` для ровно восьми полей профиля. Те же байты
идемпотентны, другая карта не переписывает вход. PREP-2 assignment содержит
`lifecycle_scope: "PREP"`, `prep_stage: "PREP-2_PERSPECTIVES"`,
`goal_result_id`, `vector_sha256`, `approval_sha256`, profile и profile hash;
он обязан совпасть с картой. Контекст содержит approved Vector
(G/NOT_G/definitions/границы), materials и собственный профиль, но
`bank: "ABSENT"`, peer-results, `--expand` и произвольные пути запрещены.
FRAMING result повторяет stage, связывает `vector_result_id` с
`assignment.goal_result_id` и повторяет остальные exact hashes. Его обязательные
верхнеуровневые поля также содержат `context_packet_sha256`: это SHA-256 точного
контекстного пакета, сохранённого контроллером в
`state/contexts/<assignment_id>--<attempt_id>.yaml`. `blind_input` обязательно
содержит `contains_bank: "NO"`, `contains_foreign_p_results: "NO"`,
`contains_preferred_solution: "NO"` и непустой список разных непустых строк
`allowed`; он может нести дополнительные адресуемые поля protocol/freeze/continuity
provenance. Это связанное с пакетом заявление о составе входа; оно не является
доказательством технического ACL.

До PREP-4 в `GOAL_TO_PLAN` `PREP-3_EXPAND_NORMALIZE` не читает
несуществующий bank. Если assignment содержит initial Q, `context` строит
`PREP_EXPANSION_CONTEXT` только из явно назначенных `initial_questions` в
accepted PREP-2 FRAMING result. Для каждого Q пакет сохраняет
`question_id`, `source_result_id`, `source_sha256` и `source_role_id`; все
такие source result обязаны быть в `dependency_result_ids` и их exact hash —
в `dependency_hashes`. `accepted_dependencies` использует
`CONTEXT_DEPENDENCY_PROJECTION_V1`: сохраняет addressable `result_id`,
`artifact_type`, `path`, `sha256` и `role_id`, но default-view FRAMING содержит
только назначенные `initial_questions`; PRIMARY/ANSWER содержит только claims
по назначенным Q, а иной source содержит только provenance. Полный `document`
возможен только для exact accepted source, явно связанного в assignment и
названного через `--expand`. Повторное расширение до отправки создаёт immutable
ревизию того же assignment/attempt. Оно сохраняет предшествующий пакет и
его внешний файл, добавляет полные projections и повторно проверяет доступ
ко всем ранее раскрытым источникам. Свежий результат расширенного назначения
связывается с текущим `context_packet_sha256`. Историческая доставка и
проверка принятых результатов разрешают точные байты по хешу.
R берутся только из approved Vector
`requirement_definitions`; пакет несёт полные Vector G/NOT_G, определения R,
границы, resources и authority, если они утверждены Vector. Неизвестный или
повторно объявленный initial Q/R, потерянная source binding или непривязанная
dependency прекращают выпуск context. Пакет остаётся `bank: "ABSENT"` и не
формирует bank до admission.

Each accepted PREP-2 `initial_questions` row uses nonempty quoted strings
`id`, `unknown`, `why_material`, `changeable_decision`, `applicability`,
`evidence_needed`, and `origin_role`; `g_refs`, `r_refs`, and `not_g_refs` are
string lists and `g_refs` is nonempty. The controller rejects missing,
ambiguous, or colliding initial Q identifiers before it builds PREP-3 context.

`admit-bank` создаёт один PREP_ADMISSION transaction до установки bank,
admission и post-admission current. `prep-admission.question_count` является
канонической десятичной строкой `"0"` или `[1-9][0-9]*`: strict YAML не
принимает некавыченные числовые scalar. После admission каждый CORE assignment intent, launch journal,
`ROLE_CONTEXT`, accepted result, registry entry и committed transaction
(верхний уровень, `assignment`, `entry`) повторяет exact
`lifecycle_scope: "CORE"` и `prep_admission_sha256`; validate, recover, seal
и verify прекращаются при пропуске или расхождении. PREP запрещён. validate и
seal допускают новый режим только из CORE_ADMITTED после pure structural проверки. PREP
resume возвращает только незавершённые задания; accepted foundations остаются
в immutable registry и `accepted_result_ids`.

`commit` is the literal public alias of `accept`; it neither executes an agent
nor weakens immutable/idempotent acceptance. `context` writes a `ROLE_CONTEXT`
only for a registered exact assignment. Its `readset` has exact bytes and
SHA-256 for addressable declared reads, and it includes accepted Vector
`G`/`NOT_G`, nested assigned Q/R, and only accepted hash-bound dependencies.
A `FRAMING` packet is produced only when its assignment has a schema-exact, assignment-bound `blind_handoff` with `handoff_id`, assignment/attempt/role/snapshot bindings, `phase: "FRAMING"`, a bank-free objective and nonempty constraints. Its declared reads must include `references/roles/perspectives.md` and `references/phases/01-intake.md`; `references/invariants.md` is the only optional additional instruction. No bank, original raw source, snapshot, state, result, `runs/*` path, dependency, assigned Q/R, contradiction record or `--expand` is permitted. The accepted Vector goal must itself be an `OP-VECTOR` `OPERATOR` and must not disclose bank Q/R. A valid blind packet includes only that fixed readset, the checked handoff and this bounded goal; it includes no Q, peer result or foreign primary. Nonblind packets retain the normal exact linked-dependency path.
`resume` runs durable recovery and reports recorded `ACCEPTED`, `READY`,
`WAITING`, `DISPATCH_UNKNOWN`, and `STALE` states without spawning agents.
`--changed` accepts only an `EVIDENCE` result; its typed descendants are saved
as stale in `state/current.yaml`, blocking validation and sealing. Byte metrics
are observable; tokens and cost are `UNAVAILABLE` when telemetry is absent.

`resume --approved-map root-map.yaml` additionally binds the exact source bytes
at `state/approved-map.yaml` and records its SHA-256 in `current.yaml`. A
replacement map must carry a nonempty `version` and
`supersedes_sha256` equal to the stored prior hash; the previous exact bytes
are archived under `state/approved-maps/<prior-sha256>.yaml`. `validate` and
both seal phases rehash and revalidate the stored map, so omitting this option
later cannot hide a cache, applicability, or routing drift.
The map has the same input snapshot, all 18 approved role IDs, explicit role
competence tags, per-Q primary routing, critical D subject/system reviewers,
and approved evidence consumers. The deterministic helper only checks that
ROOT supplied a coherent exact map: it does not infer competence or choose a
reviewer. It returns proposals, states, blockers, reuse and stale closure. A
different or malformed map is rejected before it can be persisted.

An evidence reuse declaration carries exact strings `evidence_id`,
`content_hash`, `input_snapshot`, `tool_id`, `tool_version`, `policy_hash`,
`exposure_hash`, `freshness`, and `consumer_ids`. Empty, `UNKNOWN`, or
`EXPIRED` freshness is invalid. The controller does not infer freshness from a
clock. Declared dependencies form the task DAG; unknown endpoints, cycles,
unbound dependency hashes, fourth groups and un-serialized write/resource
conflicts fail closed. Independent branches outside a changed-E closure remain
current.

Before mutable registry and event updates, `accept` writes an immutable
`transactions/<result_id>.yaml` commit containing exact assignment and result
bytes (base64), their hashes, parsed assignment body, and the registry entry.
`recover` rebuilds a lost/corrupt result index and event list from verified
commits without rerunning accepted work. It checks that the commit's exact
assignment bytes equal the immutable assignment intent. A result file without
a commit is an orphan and fails closed. A pre-transaction legacy run requires
the explicit `--bootstrap-legacy` flag and a registry whose every exact result
byte still matches its recorded hash; unknown files are never bootstrapped.

Assignment required fields are `assignment_id`, `attempt_id`, `role_id`,
`agent_id`, `input_snapshot`, `artifact_type`, `output_path`,
`dependency_hashes`, and `result_id`. Artifact types are `FRAMING`, `PRIMARY`,
`OPERATOR`, `ANSWER`, `SOURCE_OUTCOMES`, `EVIDENCE`, `HYPOTHESIS`, `DECISION`,
`REVIEW`, `EXPERIMENT`, `SYNTHESIS`, `PLAN`, `FINAL_AUDIT`. The output root
must match its type.

`assigned_ids` is the canonical optional mapping with string lists named
`questions`, `requirements`, `decisions`, and `evidence`. `assigned_q_ids` is
not a contract field. Optional `group_id` is `"1"`, `"2"`, or `"3"`;
`selection_authority`, if supplied, is exactly `"ROOT"`; `launch_state` is
`"NOT_LAUNCHED"`, `"READY_TO_DISPATCH"`, or `"DISPATCH_UNKNOWN"`.
The `launch-intent --assignment-id ... --attempt-id ... --state ...` command
records each transition in `state/launch-intents.yaml`, bound to the exact
assignment hash. `READY_TO_DISPATCH` is only an explicit pre-dispatch mark;
`DISPATCH_UNKNOWN` records that no native outcome is known and is never
replayed as ready. Recovery rechecks this journal rather than restoring the
initial assignment field. An omitted launch state starts durably as
`NOT_LAUNCHED`.

`state/assignments.yaml.current_attempts` explicitly selects one attempt for
each `assignment_id`. A retry declares `supersedes_attempt_id`; its predecessor
must already be terminal and must not remain `DISPATCH_UNKNOWN`. No lexical or
time ordering selects an attempt. Retained history stays addressable to the
orchestration validator, which rejects a multi-attempt history without an
explicit current selection or a complete supersession chain.

## Accepted result and outcomes

Every accepted result has these common fields:

```yaml
schema_version: "question-space-v4"
id: "RESULT-ID"
run_id: "R"
input_snapshot: "sha256..."
author_role: "P-01"
agent_id: "/root/native-agent"
knowledge_status: "DERIVED"
workflow_status: "ACCEPTED"
source_refs: []
related_ids: []
revision: "1"
created_at: "2026-10-21T00:00:00Z"
updated_at: "2026-10-21T00:00:00Z"
```

It also repeats its exact `assignment_id`, `attempt_id` and `artifact_type`.
The controller compares these values, role, agent, snapshot and result ID to
the previously registered assignment before accepting its exact bytes.

`workflow_status` is `NOT_RUN`, `RUNNING`, `ACCEPTED`, `BLOCKED`, `REJECTED`,
`STALE`, or `NEEDS_RECHECK`. A plan task may separately use
`execution_status` including `NOT_STARTED` and `FAILED`.

There is one selected current `SOURCE_OUTCOMES` accepted result at
`answers/source-outcomes/<result_id>.yaml`; retained predecessors remain immutable. `state/current.yaml.current_source_outcomes` binds its exact `result_id/sha256/path/assignment_id/attempt_id` under [the v4.1 lifecycle contract](contracts-v4.1.md#неизменяемая-история-и-текущие-версии). A successor declares `supersedes`; it has `records`. A `SOURCE_OUTCOME` record
contains `original_q_ids`, `source_disposition`, `answer_status`,
`knowledge_state`, `author_assignment_id`, `answer_result_id`, `evidence_ids`. One source ID has
one current outcome. `answer_status` is `ANSWERED|OPEN|NOT_APPLICABLE`;
`knowledge_state` uses `OBSERVED|DERIVED|HYPOTHESIS|REFUTED|UNKNOWN`.
Missing, nonstring, or unknown values fail before immutable acceptance.
`SPLIT`/`MERGED` also declares `disposition_target_ids`;
those IDs must be present as `DERIVED_Q` records with `q_id` and
`parent_source_ids`. The immutable bank does not contain outcomes.

`SPLIT`/`MERGED` targets are nonempty, unique `DERIVED_Q` records whose
nonempty parents preserve the exact source origins. Every source Q occurs once
and every source/derived Q names an accepted `ANSWER` or `PRIMARY` with that
exact Q in `addressed_q_ids` or `per_question_outcomes[].q_id`; `DONE`, a
self-reference, and an answer assigned to another Q do not close coverage.
Failure output includes exact `unclosed_q_ids` and `unclosed_r_ids`.

## Gates, plan, audit, release

The exact lifecycle, A/B/C, semantic/provenance/execution edges, plan-input, gate and open-condition field contracts are centralized in [contracts-v4.1.md](contracts-v4.1.md). The marked architecture extension is centralized in [architecture-graph.md](architecture-graph.md). Schemas and templates mirror these sources; they do not authorize weaker alternatives.

Each GATE-1…5 root `gates/GATE-N.yaml` and versioned `gates/GATE-6/<audit-result-id>.yaml` carry `gate_id`, `input_snapshot`,
`required_objects`, `completed_checks`, `content_review`,
`unresolved_critical`, `decision`, `allowed_next`, `blocked_dependencies`,
`evidence_refs`. The fixed phase minimum in [the v4.1 gate contract](contracts-v4.1.md#минимум-каждой-фазы-и-гейта) cannot be replaced by agent-supplied `required_objects`. `READY` remains prohibited where mandatory coverage,
critical reviews or the final audit is incomplete.

`plan/S-000.yaml` and each stage contain all eight sections. Each task includes
result, procedure, algorithms, inputs/producers, dependencies, outputs,
errors/recovery, resources, acceptance criteria, concrete checks and nonempty
R/D/E references. `validate --phase final` requires separate `semantic_edges`, acyclic E→E `evidence_provenance_edges` and acyclic `execution_edges` under [the v4.1 graph contract](contracts-v4.1.md#три-разных-графа); changed E requires each affected provenance descendant and execution consumer to be `NEEDS_RECHECK`. A
critical decision needs distinct nonauthor SUBJECT and SYSTEM accepted PASS
reviews on its exact accepted hash, each with independent check provenance,
blind input hash and risk coverage. Strict YAML uses string enums:
`criticality: "CRITICAL"|"NONCRITICAL"` and evidence
`changed: "CHANGED"|"UNCHANGED"`; it does not use YAML booleans.

Every plan input is typed `EXISTING|PLANNED|EXTERNAL` and every task/check input resolves through `inputs_contracts`; no hash is fabricated for a planned future result. Output availability is `UNCONDITIONAL|SELECTED_BRANCH|UNSELECTED_BRANCH`, and an unselected branch creates no consumable output. The exact fields and ordering rules are in [the plan contract](contracts-v4.1.md#входы-и-выходы-плана).

The final audit is the selected accepted `FINAL_AUDIT` by `FINAL-AUDITOR` at
`audit/final/<result_id>.yaml`, bound by `state/current.yaml.current_final_audit`. A successor declares `supersedes`; retained history is immutable. It covers areas `1`…`8`, binds the prepared core manifest,
and declares six readiness axes: `package`, `runtime`, `quality`, `plan`,
`implementation`, `effect` with `READY`, `NOT_READY`, `UNVERIFIED`, or
`BLOCKED` values.

Every audit open condition uses the typed contract in [contracts-v4.1.md](contracts-v4.1.md#условия-и-вердикт-аудита). A legitimate residual/future condition can coexist with `READY`; central uncertainty, repairable defect and unavailable required external input map to their stronger verdicts regardless of a `NONCRITICAL` label.

When accepted PLAN history contains more than one immutable revision for a
logical stage, `state/current.yaml.current_plan_projection` selects the only
current graph. Its exact hash is `current_plan_projection_sha256`; the same
object is stored in the Root-approved map as `current_plan_graph` before it is
persisted. The object is:

```yaml
plan_version: "2"
stages:
  - logical_stage_id: "S-000" # normative logical root remains S-000
    result_id: "S-000-R2"     # immutable accepted revision
    sha256: "..."
    assignment_id: "PLAN-S-000"
    attempt_id: "ATT-2"
```

Each row must bind an accepted PLAN result's exact bytes, current selected
attempt, matching `plan_version`, and `identity_result.stage_id`. All retained
revisions for that logical stage must make one complete acyclic `supersedes`
chain ending at the selected result; missing, branched, stale, mixed-version or
hash/attempt disagreement fails closed. Model validation, current-plan core
selection, recovery, sealing and release verification use this projection;
prior results remain immutable audit history. `release.yaml` repeats the exact
projection and its hash, and `verify` rejects either current-selection or
selected-result drift.

`seal --phase prepare` validates core and stores its manifest excluding audit.
The independent audit binds that exact SHA-256. `seal --phase finalize`
revalidates the unchanged core, writes `release.yaml` and then atomically moves
the current release pointer. A GATE-6 review is omitted from core only through
its accepted result/hash, exact final-audit target and GATE-6 evidence binding,
never by filename. `release.yaml.administrative_review` binds that actual path
and hash; `verify` rehashes it with the manifest, core, audit and gate.

## `EXTERNAL_IMPLEMENTATION` evidence subkind

External mechanisms reuse `artifact_type: EVIDENCE`, the existing `evidence/` output root, common result fields and immutable assignment/result transaction. They add `evidence_subkind: EXTERNAL_IMPLEMENTATION`, `authority_class: C_EXTERNAL_IMPLEMENTATION` and exact fields shown in [the typed template](../templates/external-implementation.yaml): `reference_status`, duplicate-free `question_ids`/`decision_ids` with at least one total and union covered by `related_ids`; `discovery`; `repository`; `implementation`; `tests_inspected`; `check_receipts`; `version_fix_history`; `current_limits`; `maturity`; `license`; `origin`; `interpretation`; `disposition`/basis; `transfer`; and `unknown_fields`.

`reference_status` is exactly `DISCOVERED|INSPECTED|TESTED|TRANSFER_VALIDATED|REJECTED` and remains independent of common `knowledge_status`. Discovery route is exactly `DIRECT_ANALOG|FUNCTIONAL_ANALOG|ALTERNATIVE_MECHANISM|NEGATIVE_EXPERIENCE`. `repository.commit_sha` is a verified full commit object SHA, not branch/tag/abbreviation/tree/blob SHA; each material/test permalink pins it and each read material stores its exact read scope, SHA-256 of exact bytes and, when cached, a safe-relative raw/chunk artifact path with the same byte hash. `INSPECTED` requires a material ref. `TESTED` requires an actual named receipt with execution scope, exact commit, result, time and address; `LOCAL_REPRODUCTION` cannot be labelled upstream CI. `TRANSFER_VALIDATED` requires target conditions plus accepted addressable applicability E. `REJECTED` requires `disposition: REJECT` and basis.

Every conditionally unresolved scalar remains present as literal `UNKNOWN` and has its exact dotted leaf in `unknown_fields`, including status leaves such as `maturity.status` and `license.status`; a broad parent or typo is invalid. Every unresolved typed collection remains `[]` and has its exact dotted collection path in `unknown_fields`; `UNKNOWN` as a collection member is invalid. An empty collection without that marker means known none, not unknown. Each marker must resolve to an actual allowed conditional scalar leaf or collection, while required identity, provenance, status prerequisites and accepted target conditions cannot be waived by any unknown marker.

`origin_family` groups fork/copy/derivative/shared-primary sources; copies are not independent E. Historical issue/fix records do not assert a current bug without current-version E. Raw reuse keys `repository + commit_sha + path + content_sha256`; a shared immutable E links all Q/D consumers, while interpretations remain role results. These are normative fields; until the separate validator owner implements enforcement and its gate passes, documentation/template conformance is not controller enforcement.

## Double-Iceberg machine contract

New snapshots carry `methodology_features: ["DOUBLE_ICEBERG_V1", "ARCHITECTURE_GRAPH_V1"]`. Exact fields for blind positions, normative anchors, nested views, current bindings, semantic relations and lineage are centralized in `schemas/iceberg-view.yaml` and [references/double-iceberg.md](double-iceberg.md). `CAUSAL_INFLUENCE` is machine-distinct from `CAUSES_HYPOTHESIS` and requires exact accepted E subsets in `causal_confirmation`. New marked releases fail closed on phased omissions; older snapshots remain `LEGACY_QUALIFIED` for each absent feature.

## Architecture graph machine contract

For `ARCHITECTURE_GRAPH_V1`, one accepted `SYNTHESIS.architecture_graph` is canonical under [the architecture contract](architecture-graph.md). Its three derived views reuse existing typed IDs, immutable acceptance and current-plan selection. The current logical S-000 selects the graph with `architecture_ref` containing exactly `synthesis_id`, `synthesis_sha256`, `graph_sha256`; all current stages and selected final audit must match it. The core manifest and final release bind that same exact ref. No standalone current-graph selector or duplicated graph is normative. New graph bytes require a new accepted SYNTHESIS and dependent plan revisions; hashes are recomputed using the controller's canonical YAML serialization.

Marked nonblind context reports `architecture_selection_state: CURRENT|UNSELECTED|NEEDS_RECHECK|INCOMPLETE`; `architecture_ref` appears only when selected. `INCOMPLETE` also exposes exact `architecture_errors`, allowing the next planner context while future S/T/C are still being committed; it does not qualify the partial graph. Resume/recover receipts also expose `LEGACY_QUALIFIED` for snapshots without the feature. Legacy context packets keep their original bytes and omit new architecture fields; blind framing receives neither graph nor ref. Before current S-000 selection the result remains UNSELECTED; selection never guesses among accepted synthesis results. Stale or incomplete selection blocks strict validation/render/seal until addressed.

Architecture render uses `--root`, `--run-id`, and `--architecture-view functional|data|implementation` against the selected current graph; generic `--source` render remains a separate interface. `--subsystem` and `--goal-path` select readable derived views; repeated `--changed-node` adds the structured downstream impact report. Markdown records the exact graph ref, current plan projection hash, filter metadata and derived Mermaid; functional view includes the mechanism ID/status summary and separately labelled alternatives. A run-local output must be inside `runs/<run_id>/derived/`; an external output is also allowed. Accepted result/state paths are not valid render targets. Rendering does not mutate or accept graph/plan objects and does not declare READY.

Pure validation distinguishes structural feasibility from semantics: `semantic_validity` and `implementation_readiness` remain `NOT_ASSESSED`. Future `PLANNED` DATA resolves to producer/output/C, without an invented result hash. EXISTING implementation mapping uses exact files and accepted integration E; imports/calls alone are `STATIC_ONLY`. Selective impact preserves independent applicable nodes/checks and requires recheck only for affected dependencies.

Marked final audit requires `architecture_review.structural_report_sha256` for the exact pure report and separate semantic outcome/procedure/observation/accepted E/findings/limits. A final READY verdict needs structural PASS, independent semantic PASS, accepted E and no open findings, together with all existing final-audit rules. Future checks do not imply implementation or effect readiness. Field shapes and the five code mapping states are centralized in [architecture-graph.md](architecture-graph.md).
