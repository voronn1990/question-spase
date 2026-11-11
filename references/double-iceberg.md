# Двойной айсберг: нормативный интеллектуальный контракт

Источник расширения — `INPUT/double-iceberg-task.txt` с SHA-256 `92c5c09c545d63e612adddb54c2d07c2d475f168250dad37675f71cffdaf439c`. Этот контракт встраивается в существующие 10 перспектив, 6 операторов, планировщика и аудитора. Он не добавляет роль, artifact type, хранилище, обязательный проход или отдельный исследовательский цикл.

## Вершина и нормативный якорь

Утверждённый Vector остаётся единственным нормативным источником G, NOT_G и R. Каждый новый snapshot текущей версии в обоих режимах неизменно фиксирует `methodology_features: ["DOUBLE_ICEBERG_V1", "ARCHITECTURE_GRAPH_V1"]`. Отсутствие соответствующего marker допустимо только для уже допустимого legacy-run: он может продолжиться как `LEGACY_QUALIFIED` для этой функции, но не заявляет её выполнение. Marker нельзя снять при resume/release. Архитектурная функция описана в [отдельном контракте](architecture-graph.md); каждая карта Двойного айсберга хранит exact `normative_vector_binding`:

```yaml
normative_vector_binding:
  anchor_kind: "LOCAL_ACCEPTED_VECTOR" # или IMPORTED_APPROVED_VECTOR
  vector_id: "VECTOR-001"
  vector_sha256: "<sha256>"
  revision: "1"
  approval_binding_id: "APPROVAL-001"
  approval_sha256: "<sha256>"
  applicability_result_id: "RESULT-BANK-APPLICABILITY-001"
  goal_claim_path: "/goal"
```

`GOAL_TO_PLAN` и `BANK_TO_PLAN/EXTERNAL_READY_BANK` используют `LOCAL_ACCEPTED_VECTOR`. `BANK_TO_PLAN/APPROVED_RUN_IMPORT` использует exact immutable imported approved Vector и approval; текущий accepted `BANK_APPLICABILITY` подтверждает применимость, но не становится новым Vector или approval. Canonical G endpoint — typed pair `(G, vector_id)`: validator разрешает `goal_claim_path` внутри exact Vector bytes. Это машинный адрес существующего утверждения, а не придуманный `goal_id` или новая цель. R endpoint использует canonical requirement ID из тех же bytes. Айсберг не меняет G/R и не превращает архитектурный компонент в R.

## Два адресуемых представления

`OP-PROJECTION` выпускает `CURRENT_REALITY`: наблюдаемые состояния и подтверждённые ограничения, а для нового проекта — только подтверждённые исходные условия и явно маркированные гипотезы потенциальных препятствий. `OP-REVERSE` выпускает `DESIRED_REALITY`: необходимые способности, доступные входы, переходы, альтернативные механизмы и конечные проверки. Это nested projections внутри существующих accepted `OPERATOR` results, не новые artifacts. Exact address — `<accepted-result-id>#<view_id>`.

Каждый `iceberg_view` имеет:

```yaml
view_id: "ICE-CURRENT-001"
view_kind: "CURRENT_REALITY" # или DESIRED_REALITY
revision: "1"
input_snapshot: "<sha256>"
supersedes_view_id: ""
normative_vector_binding: {}
source_bindings: []
members: []
semantic_edge_ids: []
open_q_ids: []
coverage_limit: "defined G/R, known critical unknowns and declared research obligations"
knowledge_status: "DERIVED"
workflow_status: "ACCEPTED"
```

Initial view omits `supersedes_view_id`; successor names the exact prior view and lives in an accepted OPERATOR result whose result-level `supersedes` points to the prior result. For each `view_kind`, accepted history must be gapless and linear with one unsuperseded head. Commit and consumer selection reject branch, gap, stale head and hash/path drift. The general stale blocker exempts only an exact superseded OPERATOR ancestor in the selected view lineage when no current consumer selects it; an unrelated stale OPERATOR remains blocking. Downstream consumers bind the exact current heads as `{view_id, view_kind, result_id, result_sha256, revision}`. Legacy accepted results may omit iceberg fields for historical readability, but cannot satisfy a Double-Iceberg gate.

`source_bindings` use existing registries and immutable inputs:

```yaml
- source_kind: "ACCEPTED_RESULT" # или IMMUTABLE_INPUT
  source_id: "E-001"
  source_sha256: "<sha256>"
  source_path: "evidence/E-001.yaml"
```

G/R bind the exact Vector anchor; original Q may bind immutable bank bytes, derived Q bind current selected SOURCE_OUTCOMES, and E/D/H bind accepted registry results. A member is a projection, not a duplicate normative fact:

```yaml
- ref_type: "H"
  ref_id: "H-001"
  element_kind: "ASSUMPTION"
  canonical_claim_path: "/claim"
  source_binding: {source_kind: "ACCEPTED_RESULT", source_id: "H-001", source_sha256: "<sha256>", source_path: "hypotheses/H-001.yaml"}
  origin_position_refs:
    - {framing_result_id: "RESULT-P01-FRAMING-001", framing_result_sha256: "<sha256>", position_id: "ICEPOS-P01-001", seed_id: "IP-P01-001", origin_role: "P-01"}
    - {framing_result_id: "RESULT-P03-FRAMING-001", framing_result_sha256: "<sha256>", position_id: "ICEPOS-P03-001", seed_id: "IP-P03-004", origin_role: "P-03"}
  materiality: "changes D-002 and C-004"
  scope: "declared operating conditions"
```

Allowed `element_kind` values are `CONDITION|CAPABILITY|OBSTACLE|ASSUMPTION|MECHANISM|OBSERVATION|UNKNOWN`; allowed member ref types are `G|R|Q|E|D|H`. `canonical_claim_path` resolves the exact statement/unknown field inside the canonical bound bytes; the view must not copy that claim as another prose source. `origin_role` alone is not provenance: a common/shared H and its member retain at least two exact accepted seed refs with distinct P roles. A map row never upgrades the referenced object's knowledge status.

## Семантические отношения

Extend `semantic_edges` endpoints with `G`, `R` and `H`; existing types remain valid. G/R resolve through the exact normative anchor, H through an accepted `HYPOTHESIS`. Evidence provenance remains exact acyclic `E -> E`. Execution remains a separate DAG; H may be a knowledge consumer so an exact `E -> H -> Q|D` chain can invalidate affected conclusions. G/R are immutable inputs, not empirical execution claims.

Every new iceberg semantic edge uses:

```yaml
id: "EDGE-ICE-001"
relation: "CAUSES_HYPOTHESIS"
from_type: "H"
from_id: "H-001"
to_type: "D"
to_id: "D-001"
scope: "exact conditions and boundary"
combination_kind: "SINGLE"
combination_id: ""
sequence_index: ""
knowledge_status: "HYPOTHESIS"
evidence_ids: []
distinguishing_check_id: "Q-017"
```

Relations: `NECESSARY_FOR`, `SUFFICIENT_FOR_NOT_G`, `RISK_FOR`, `CAUSES_HYPOTHESIS`, `CAUSAL_INFLUENCE`, `CORRELATES_WITH`, `MECHANISM_DEPENDS_ON`, `ENABLES`, `ALTERNATIVE_TO`, `CONTRADICTS`, `BYPASSES`, `MITIGATES`, `FEEDBACK_WITH`. Combination kinds: `SINGLE`, `AND`, `OR`, `SEQUENCE`, `SHARED_FAILURE`, `FEEDBACK`. Non-`SINGLE` edges share a nonempty `combination_id`; `SEQUENCE` additionally uses unique positive decimal `sequence_index`. Empty fields above are omitted from actual YAML when inapplicable.

Exact meanings:

- `NECESSARY_FOR`: `G ⇒ N` only in the stated scope. Presence of all known necessary conditions does not prove G.
- `SUFFICIENT_FOR_NOT_G`: `F ⇒ NOT_G` in the stated model. Absence of F does not prove G.
- `RISK_FOR`: a scoped adverse likelihood claim with stated model; it is not deterministic failure.
- `CAUSES_HYPOTHESIS`: testable causal mechanism, never established merely by wording or consensus.
- `CAUSAL_INFLUENCE`: bounded confirmed causal claim with `knowledge_status: DERIVED` and a `causal_confirmation` object. Its nonempty `intervention_evidence_ids`, `temporal_order_evidence_ids`, `mechanism_evidence_ids` and `competing_explanation_evidence_ids` are exact accepted E identifiers and subsets of `evidence_ids`.
- `CORRELATES_WITH`: joint observation without causal promotion.
- `MECHANISM_DEPENDS_ON`: dependency of one chosen mechanism, not a normative requirement of G.

`CAUSES_HYPOTHESIS` uses only `HYPOTHESIS|UNKNOWN` and omits `causal_confirmation`; every other noncausal relation also omits that object. `CAUSAL_INFLUENCE` uses `DERIVED` and requires exact accepted evidence for bounded intervention, temporal ordering, mechanism and competing explanations. Correlation cannot be promoted by changing its label or status alone. `HYPOTHESIS`, risk and correlation edges name a finite distinguishing Q/C or exact reason a check is currently unavailable. A semantic path, cycle, ten-agent agreement or completed map is not empirical proof. Semantic feedback cycles are legal and never define execution order.

## Независимые позиции и общие предпосылки

Before seeing bank Q, foreign P results, preferred mechanism or a coordinator-built map, every P adds an `iceberg_position` to its immutable blind FRAMING result:

```yaml
iceberg_position:
  position_id: "ICEPOS-P01-001"
  origin_role: "P-01"
  origin_assignment_id: "ASG-P01-FRAMING-001"
  input_snapshot: "<sha256>"
  elements:
    - seed_id: "IP-P01-001"
      candidate_view: "CURRENT_REALITY"
      element_kind: "ASSUMPTION"
      statement: "exact canonical blind claim or unknown"
      scope: "declared scope"
      g_refs: ["VECTOR-001"]
      r_refs: ["R-001"]
      materiality: "decision or check that can change"
      knowledge_status: "HYPOTHESIS"
      source_refs: []
      finite_check:
        check_kind: "OBSERVATION"
        procedure: "finite distinguishing observation"
        discriminating_outcomes: ["supports", "refutes", "remains unknown"]
  relations: []
```

Each element is the canonical claim/unknown in that accepted FRAMING result; it does not need a premature accepted H or Q. A later H or view member retains exact `origin_position_refs` with `framing_result_id`, `framing_result_sha256`, `position_id`, `seed_id` and `origin_role`, then points to its exact field path rather than copying it. The coordinator merges only after all ten positions are accepted. A common assumption requires at least two accepted seed refs from distinct P roles; agreement raises review priority but remains `HYPOTHESIS` or `UNKNOWN` without evidence. OP-DIVERGENCE owns conflicts, common assumptions and competing explanations. The coordinator preserves provenance and routes work; it does not invent substantive map contents.

Blind material disclosure uses the existing `assignment.allowed_reads`, not a new field or method. Vector supplies a bounded approved projection of G/R/scope; it does not imply disclosure of every source material. Root may select exact immutable approved brief material, default none. The context builder verifies its source path/hash, rejects original or normalized bank bindings and foreign results, and emits selected content once. Unselected snapshot materials remain bound for later authorized nonblind work without entering the blind packet.

## Когда появляется новый Q

A map element becomes a candidate Q only when all are true:

1. a concrete unresolved unknown exists;
2. it can change G/R interpretation, a mechanism, D or a material C;
3. a finite discriminating source, observation or experiment is named;
4. no accepted equivalent Q covers the same unknown in the same scope;
5. exact originating view/member/edge refs and resource-cap impact are recorded.

`GOAL_TO_PLAN` applies this during the ten blind initial sets and the single bounded PREP expansion; there is no second generation pass. `BANK_TO_PLAN` preserves every original Q and the supplied count. A genuinely new critical unknown is a bounded `DERIVED_Q` under the existing global expansion cap, never an automatic second 300-Q bank. If the cap is absent/exhausted, the unknown remains an open condition and limits dependent readiness.

## Распределение по существующим операторам

- `OP-VECTOR`: freezes G/R/NOT_G and scope; does not choose a mechanism or map cause.
- `OP-PROJECTION`: CURRENT_REALITY, NOT_G paths, combinations, shared failures, intervention windows and recheck after material change.
- `OP-DIVERGENCE`: competing causes, common assumptions, contradictions, alternative framings and distinct evidence.
- `OP-REVERSE`: DESIRED_REALITY from G back to capabilities/resources and forward-checks alternative routes, including removal of a familiar component.
- `OP-SYNTHESIS`: binds exact two view heads, compares A/B/C and may select a stronger mechanism outside either initial view; each chosen/rejected mechanism traces to members/edges/E/D.
- `OP-INDULGENCE`: chooses a finite discriminating experiment by decision impact, cost, risk and available authority.

No operator repeats accepted work merely to fill a map. GitHub may discover other capability mechanisms or failure classes after blind freeze; Context7 checks exact versioned technical contracts. Neither source creates G/R.

## Последующие фазы

Evidence may confirm, narrow or refute exact members/edges. Peer reviews challenge shared assumptions, relation kind, missing capabilities and competing explanations. Synthesis binds both exact view heads and records how the chosen mechanism uses, bypasses or refutes their material members. PLAN-AUTHOR traces:

`G/R → capability or material unknown → Q/E → D → S → T → C → expected useful result`.

The plan implements the accepted mechanism; it does not delegate rebuilding the maps to an implementer. FINAL-AUDITOR receives both exact view bindings and source evidence within the existing eight areas, then independently searches for a missing assumption, false necessity/causality, impossible capability, uncovered failure combination and stronger bypass. A stronger feasible path becomes a defect/open condition and targeted recheck, not an automatic global restart or a ninth audit area.

## Версии, recheck и границы готовности

When accepted E changes, provenance descendants and actual execution consumers become `NEEDS_RECHECK`. A view with a stale exact source binding must be superseded; unchanged members and negative results remain reusable. The successor records affected members/edges/Q/D/checks and does not rebuild both maps by default. Recovery preserves accepted partial positions, view revisions, authorship and idempotency.

Both maps may be structurally complete while plan readiness remains `RESEARCH_REQUIRED`: unresolved central mechanism, critical premise, false necessity, or unproved positive path blocks READY. Measure discovered material Q, refuted assumptions, evaluated alternative routes, corrected relations, negative tests, repeated work and actual resource use where observable. Never claim coverage of all possible unknowns.

## Фазовые минимумы

For `DOUBLE_ICEBERG_V1`, early blind framing requires all ten accepted P results to contain a nonempty own-origin `iceberg_position` before foreign P results, bank Q or preferred mechanism exposure. GATE-2/3 close every concrete material question, evidence, hypothesis and H-target review obligation that actually arises from those positions and later accepted knowledge, without pretending that final views already exist. Zero new `DERIVED_Q`, zero accepted H and therefore zero H-target reviews are valid when original Q plus accepted E/R/G cover the bounded model and no qualifying unknown or material hypothesis arises. Every present new Q/H remains typed, material, finite, nonduplicate and addressed by its required review; this zero allowance never waives critical coverage, and a semantic audit finding of unjustified omission blocks readiness. GATE-4 and every later gate require exact current heads for both view kinds plus SYNTHESIS bindings to them. A new marked release cannot pass by omitting optional iceberg fields. An unmarked legacy run follows its historical contract and remains explicitly `LEGACY_QUALIFIED`, never silently upgraded.
