# Нормативные контракты v4.1

Этот файл — единый источник для исправленных контрактов жизненного цикла, A/B/C, графов, плана, гейтов и финального вердикта. Роли, фазы, шаблоны, схемы, контроллер и тесты ссылаются на него и не создают альтернативных определений. Контроллер проверяет структуру; содержательную достаточность принимает компетентный неавтор.

## Неизменяемая история и текущие версии

`SOURCE_OUTCOMES` и `FINAL_AUDIT` сохраняются как неизменяемые принятые results по адресам `answers/source-outcomes/<result_id>.yaml` и `audit/final/<result_id>.yaml`. Новый result указывает `supersedes: <prior_result_id>`. `state/current.yaml` выбирает текущий result объектом `current_source_outcomes` либо `current_final_audit` с точными полями `result_id`, `sha256`, `path`, `assignment_id`, `attempt_id`.

История каждого типа образует одну полную линейную цепочку. Branch, gap, stale selection, несовпадение path/hash/assignment/attempt и перезапись принятых bytes отклоняются. Legacy singleton `answers/source-outcomes.yaml` и `audit/final.yaml` читаются только совместимой миграцией; они не дают права переписать историю. Accept сначала фиксирует immutable revision, затем транзакционно и идемпотентно меняет selector. Все потребители, включая gates, context, model validation, seal, release и verify, используют только выбранный exact result.

Новое E может перевести затронутые consumers в `NEEDS_RECHECK`, после чего принимается новая revision `SOURCE_OUTCOMES`. Исправленный core получает новую independent `FINAL_AUDIT` revision. Прежняя revision остаётся доказательной историей и не переносит принятие на изменённые bytes.

## A/B/C: три направления поиска

| Направление | Обязательный смысл |
|---|---|
| A | Глубокое улучшение текущего подхода с сохранением его обоснованных сильных сторон. |
| B | Принципиальная переработка центрального механизма или ограничивающей предпосылки. |
| C | Конструкция от конечной цели без привязки к текущему устройству. |

Буквы не являются квотой и не означают классы полномочий A/B/C/D внешних источников. Три настройки одного механизма не считаются тремя архитектурами. До результатов замораживаются общие существенные inputs, conditions, criteria, сопоставимый budget и учёт attempts. Допустимы победа A, обоснованный hybrid с отдельной проверкой взаимодействий и честно меньшее число вариантов, если bounded search не нашёл три содержательно разные конструкции. Для каждого направления сохраняются фактический поиск, механизм и outcome; пустая буква не проходит.

## Три разных графа

- `semantic_edges` описывает гипотезы, альтернативы, противоречия и обратные связи. Typed endpoints обязательны; cycles допустимы и не задают порядок исполнения. Для marked `DOUBLE_ICEBERG_V1` feature endpoints дополнительно включают G/R/H по его центральному контракту.
- `evidence_provenance_edges` описывает происхождение производных E. Допустимы только exact `E -> E`, граф ацикличен, круговое самообоснование не подтверждает claim.
- `execution_edges` описывает доступность и потребление E/Q/D/REVIEW/T/C; для marked Double-Iceberg H дополнительно допустим как knowledge consumer. Граф ацикличен; G/R остаются immutable normative inputs, а `E -> E` происхождение сюда не помещается.

Legacy `dependency_edges` допускается только как однозначно мигрируемый compatibility input. Новый result пишет три явных поля. Изменение E проходит сначала по provenance descendants, затем только по фактическим execution consumers; затронутые узлы получают `NEEDS_RECHECK`, независимые ветви сохраняются.

## Входы и выходы плана

Каждый `inputs_contracts.inputs[]` имеет `id`, `input_kind`, `contract`, непустые `consumer_ids`, разрешающиеся только в exact T/C.

- `EXISTING`: `source_id`, `version`, `identity`, `accepted_hash`. Источник — immutable input либо exact accepted result; hash совпадает с сохранёнными bytes/registry.
- `PLANNED`: `producer_task_id`, `output_id`, `acceptance_check_id`; `accepted_hash` запрещён. Producer, output и его check разрешаются; producer предшествует consumer, а check принадлежит producer.
- `EXTERNAL`: `source`, `owner`, `availability_condition`, `availability_status`, `blocked_consumer_ids`. `AVAILABLE` требует exact accepted E в `availability_evidence_id` и пустой blocked list; `UNAVAILABLE` запрещает evidence binding и блокирует всех `consumer_ids`.

Каждый `inputs_contracts.outputs[]` имеет `id`, `path`, `producer_task_id`, `acceptance_check_id`, `consumer_ids`, `contract`, `availability`, `branch_guard`, `acceptance_status`. `acceptance_status` равен `PLANNED|ACCEPTED`: только `ACCEPTED` требует exact accepted E в `acceptance_evidence_id`. `availability` равен `UNCONDITIONAL|SELECTED_BRANCH|UNSELECTED_BRANCH`; `SELECTED_BRANCH` требует `branch_selection_status: SELECTED` и accepted E в `branch_selection_evidence_id`. Для `UNSELECTED_BRANCH` consumers пусты и T/C не могут ссылаться на output. Selected/unconditional output доступен только более поздним consumers после принятия producer check. Любой `tasks[].inputs[]` или `acceptance_exit[].inputs[]` разрешается в input-contract ID либо output ID/path; raw несуществующий ID запрещён.

## Минимум каждой фазы и гейта

`required_objects` может расширять минимум, но не заменять его:

1. GATE-1: immutable admitted bank/input и accepted FRAMING постановки/банка.
2. GATE-2: выбранный current `SOURCE_OUTCOMES`, exact answer targets и полное покрытие original Q.
3. GATE-3: содержательные REVIEW results и их exact reviewed targets.
4. GATE-4: accepted `SYNTHESIS` с содержательными `comparison_protocol`, `chosen_mechanism` и genuine alternatives по A/B/C contract.
5. GATE-5: каждый stage выбранной `current_plan_projection` с полными plan contracts.
6. GATE-6: выбранный current `FINAL_AUDIT`, связанный с exact current core.

Открытый Q может вести к ограниченному исследовательскому переходу. Он запрещает `READY`, только если центральный механизм или критическая предпосылка остаются неразрешёнными.

## Условия и вердикт аудита

Каждый `open_conditions[]` содержит `id`, `area_ids`, `criticality`, `condition`, `next_path`, `goal_requirement_impact`, `admissibility_basis`, `early_check_id`, непустые `gated_dependency_ids`, `condition_kind`, `affected_requirement_ids`, `affected_decision_ids`. `condition_kind` равен `CENTRAL_UNCERTAINTY|REPAIRABLE_DEFECT|REQUIRED_EXTERNAL_INPUT|RESIDUAL_CONDITION|FUTURE_CHECK`.

- `CENTRAL_UNCERTAINTY` требует `RESEARCH_REQUIRED`, если нет более сильной причины.
- `REPAIRABLE_DEFECT` требует `REPAIR_REQUIRED`, если нет более сильной причины.
- недоступный `REQUIRED_EXTERNAL_INPUT` требует `BLOCKED`.
- `NONCRITICAL` допустим только для `RESIDUAL_CONDITION|FUTURE_CHECK`, не затрагивает R или CRITICAL D, имеет конкретное residual/future impact, основание допустимости, разрешимый ранний check и только task dependents, строго следующие после него. Такая запись может сосуществовать с `READY`.

Переименование blocker в `NONCRITICAL` ничего не меняет. Приоритет остаётся `BLOCKED > REPAIR_REQUIRED > RESEARCH_REQUIRED > READY`.

## Два варианта BANK_TO_PLAN

Оба варианта сохраняют exact original bank/goal/material bytes, создают локальный normalized bank и не генерируют второй банк.

1. `APPROVED_RUN_IMPORT` принимает exact artifacts прежнего Question Space run и проверяет его Vector/approval/statement bindings и applicability без выдачи foreign object за current accepted result.
2. `EXTERNAL_READY_BANK` использует `qs.py init --mode BANK_TO_PLAN --goal G --bank NORMALIZED --external-bank ORIGINAL [--material M...]`; foreign import flags запрещены. Snapshot сохраняет `bank_entry_kind: EXTERNAL_READY_BANK` и exact `external_binding {schema_version,source_kind,current_run_id,original_path,original_sha256,normalized_path,normalized_sha256,external_payload_sha256}`. Затем public PREP создаёт local OP-VECTOR, genuine `approve-vector` record только там, где действительно требуется authority, perspective map, local `BANK_APPLICABILITY` и admission с `generation_performed: NO`; внутренние approval artifacts несуществующего run не выдумываются.

В обоих вариантах provenance каждого исходного Q сохраняется, admission проверяет контекст и полноту, а resume продолжает только непринятую работу.

## Квалификационная граница

Static/schema/unit/fixture evidence доказывает только соответствующую structural область. Текущий full exact-code 18-role behavioral qualification требует отдельного запуска после code freeze: 18 distinct native role/agent identities, реальные assignments/readsets/results, шесть Core phases, independent audit, actual child GitHub/Context7 receipts и exact package hashes. Старые отрицательные либо ограниченные receipts сохраняются как история и не становятся текущим PASS.

## Двойной айсберг

Ранее зафиксированный `NOT_IN_CURRENT_RELEASE` был историческим выводом до предоставления exact source. Текущий authorized source `INPUT/double-iceberg-task.txt` с SHA-256 `92c5c09c545d63e612adddb54c2d07c2d475f168250dad37675f71cffdaf439c` реализуется единым [контрактом](double-iceberg.md). Он использует существующие результаты, три разных graph classes и 18 ролей; новые OP, artifact type, store или второй полный цикл не вводятся. New marked run обязан выполнить phased minima; unmarked old run остаётся `LEGACY_QUALIFIED`.
