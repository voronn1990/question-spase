# Единый архитектурный граф

`ARCHITECTURE_GRAPH_V1` требует проверяемую конструкцию: каким образом источники, операции, данные, контракты, задачи и проверки совместно обеспечивают путь до G. Граф не подтверждает научную, экономическую или функциональную правильность центрального алгоритма. Эти утверждения остаются предметом исследователей и независимого аудита.

## Канонический объект и выбор revision

Единственная нормативная модель — `architecture_graph` внутри immutable accepted result `artifact_type: SYNTHESIS`, `author_role: OP-SYNTHESIS`. Это reserved поле нельзя выдавать в result другой роли. В marked SYNTHESIS обязательный `chosen_mechanism_id` равен `architecture_graph.selected_mechanism_id`; существующий `chosen_mechanism` сохраняет содержательное обоснование. Не создавай три копии графа, отдельный runtime, базу данных или новую роль. Переиспользуй существующие G/R/S/T/C IDs, accepted registry, версии, E и механизм приёмки.

Текущий logical S-000, выбранный `current_plan_projection`, содержит `architecture_ref` ровно с тремя полями:

```yaml
architecture_ref:
  synthesis_id: "SYNTHESIS-001"
  synthesis_sha256: "<SHA-256 exact accepted SYNTHESIS bytes>"
  graph_sha256: "<canonical architecture_graph SHA-256>"
```

Это иллюстрация формы; placeholders не являются evidence. В role results reserved поле `architecture_ref` допустимо только у PLAN/FINAL_AUDIT; все текущие stages одной версии и current FINAL_AUDIT обязаны связывать тот же exact ref. Сам граф хранится в SYNTHESIS, а не в stage/audit/view. Произвольный первый или последний SYNTHESIS не выбирается автоматически. До current S-000 selection контекст может честно сообщить отсутствие выбора; render/seal требуют выбранный непросроченный graph. Drift, stale result, чужой snapshot и несовпадение любого hash прекращают принятие зависимого результата.

Изменение архитектуры создаёт новый immutable SYNTHESIS result и новые зависимые PLAN revisions/refs; прежние bytes остаются историей. Единый вычисляемый active/historical срез используется проверками карт, SYNTHESIS, gates, semantic/execution edges и текущего плана. Exact S-000 selection имеет приоритет; до S-000 исследовательский срез определяется существующими current assignments и законной цепочкой supersedes. Отсутствие однозначного выбора, fork/cycle или повреждение binding — ошибка, а не выбор «последнего файла». Исторический потребитель проверяется по собственным exact картам и основаниям, не по будущим картам; immutable bytes, hashes, ownership и lineage всей истории продолжают проверяться. Review исторической цели остаётся активным, если его exact result явно потребляется текущим execution graph; один возраст target не отменяет законное повторное использование. Ghost endpoints и неподтверждённая применимость продолжают отклоняться. Нет свободного current-graph pointer, который мог бы обойти текущий план. Новые snapshots обоих режимов pin `DOUBLE_ICEBERG_V1` и `ARCHITECTURE_GRAPH_V1`; snapshot без архитектурного marker остаётся совместимым legacy, но не может заявлять эту функцию.

При последовательном приёме новых PLAN revisions полная утверждённая карта может ещё отсутствовать. В этом промежутке context явно возвращает `architecture_selection_state: INCOMPLETE` и адресную ошибку выбора, без выбранного графа или `architecture_ref`. Если прежняя карта уже выбрана, промежуточный статус допустим лишь после проверки её точных хешей и привязок, линейной истории supersedes и зарегистрированной цепочки текущей повторной попытки. Это позволяет планировщику завершить комплект revisions. Строгие render, validate и seal требуют однозначной текущей карты; повреждение хешей, привязок, реестра или истории не превращается в допустимый промежуточный статус.

## Одна модель, три представления

| Представление | Что показывает |
|---|---|
| `functional` | G/R, необходимые CAPABILITY, реализующие COMPONENT/ALGORITHM, зависимости и механизмы SELECTED/ALTERNATIVE. |
| `data_contracts` (CLI: `data`) | Producer→DATA→contract→availability→consumer, версии/форматы/смысл, внешний вход, ошибки и принимающие C. |
| `implementation` | Компоненты/алгоритмы→S/T/C, producer/consumer tasks, порядок, проверки и условия завершения. |

Представления и Mermaid выводятся из выбранного YAML и его exact plan refs. Их нельзя редактировать как параллельную норму. Существующие `semantic_edges`, `evidence_provenance_edges`, `execution_edges` сохраняют разные контракты [v4.1](contracts-v4.1.md#три-разных-графа); три архитектурных представления не переопределяют их.

Functional view сохраняет summary механизмов и отдельно помеченные `ALTERNATIVE` subgraphs. Их узлы/edges не добавляют источники, prerequisites или evidence в конструктивный путь выбранного решения; `goal_path` и selective impact относятся к выбранному механизму. Фильтр `subsystem_id` ограничивает соответствующие части обоих видов и сохраняет все пересекающие границу edges вместе с ближайшими внешними canonical nodes. Машинные `boundary_edges`, `internal_node_ids`, `boundary_node_ids` показывают направление и оба конца; внешние порты не создают новых нормативных узлов. Functional overview сворачивает технические пути в явно производные `VIEW_FLOW` с `source_edge_ids` и replayable `paths`; полная детализация остаётся в `detail`. Mermaid подписывает контракты ID/version и выводит legend один раз. Полные карточки доступны в `detail.contracts` и отдельной секции Markdown CLI render.

## Typed модель

Точные поля собраны в [schema](../schemas/architecture-graph.yaml), иллюстративная заготовка — в [template](../templates/architecture-graph.yaml); она встраивается в SYNTHESIS, а не становится отдельным нормативным файлом. Обязательные верхние keys: `schema_version: "question-space-architecture-v1"`, `id`, `revision`, `project_kind: NEW|EXISTING`, `selected_mechanism_id`, `mechanisms`, `nodes`, `edges`, `contracts`, `implementation_map`. `mechanisms[]` задаёт устойчивый `id` и `status: SELECTED|ALTERNATIVE`; selected ID однозначно совпадает с выбранным механизмом SYNTHESIS. Альтернативы сохраняют реальный найденный путь и outcome сравнения, не подменяя центральный выбор.

Каждый node содержит `id`, `kind`, `label`, `subsystem_id`, `mechanism_id`, `required`. `kind` — `G|R|CAPABILITY|COMPONENT|ALGORITHM|DATA|S|T|C`; `required` — строка `"true"|"false"`, не YAML boolean. `mechanism_id` обязателен и для G/R/S/T/C. G связывает утверждённую цель через `existing_id`; R и S/T/C переиспользуют exact IDs immutable requirements и текущих plan entities. Нельзя сделать новый R через graph или выдать существование ID за исполнение T/C.

DATA имеет `contract_id` и `availability`:

- `EXISTING`: `source_id`, `accepted_hash`, `version`; источник разрешается в exact immutable input/accepted result, hash соответствует байтам.
- `PLANNED`: определённый producer и accepting C; hash будущего result не требуется и не выдумывается. Это будущая возможность получить output после входов/операции/приёмки, а не уже имеющееся E.
- `EXTERNAL`: `source`, `owner`, `availability_condition`, `availability_status: AVAILABLE|UNAVAILABLE`; AVAILABLE требует accepted `availability_evidence_id`, а недоступный обязательный input остаётся явным ограничением зависимого пути.

Каждое edge имеет `id`, `kind`, `from_id`, `to_id`, `meaning`, `scope`, `conditions`, `check_ids`. `scope` — `EXECUTION|RESEARCH|EVIDENCE`; `conditions` и `check_ids` — списки строк. Пустой список означает отсутствие условий/проверок, а не скрытое неизвестное. Существенный переход должен иметь содержательный смысл, область и проверяемые условия.

T/C nodes имеют `stage_id`, C дополнительно `task_id`; они совпадают с canonical планом. S — grouping node, порядок S задаётся существующим stage DAG, а не новыми execution edges с S endpoints. PLANNED DATA связывает exact output через обязательный `plan_ref: {stage_id, output_id}` и совпадающий OUTPUT ref в своём `contracts[].plan_refs`.

| Edge kind | Направление и значение |
|---|---|
| `REQUIRES`, `DEPENDS_ON` | Prerequisite→dependent; все обязательные prerequisites необходимы совместно. |
| `REALIZES` | COMPONENT/ALGORITHM→CAPABILITY. |
| `IMPLEMENTS` | T→COMPONENT/ALGORITHM; exact T принадлежит текущему S. |
| `PRODUCES` | T/COMPONENT/ALGORITHM→DATA. |
| `CONSUMES` | DATA→COMPONENT/ALGORITHM/T/C с contract/input binding. |
| `CONTRIBUTES_TO` | CAPABILITY/DATA/C→G/R; вклад в требуемый результат. |
| `VERIFIES` | C→проверяемый subject; это отношение проверки, а не обратная execution dependency. |
| `EVIDENCE_SUPPORT` | Доказательная поддержка; отдельный ациклический слой, без кругового самообоснования. |
| `RESEARCH_FEEDBACK` | Исследовательская обратная связь только со `scope: RESEARCH`; не задаёт порядок исполнения и не создаёт E. |

`VERIFIES` и `EVIDENCE_SUPPORT` имеют `scope: EVIDENCE`; остальные execution kinds — `EXECUTION`. Execution edge между разными механизмами не позволяет незаметно подключить альтернативу к выбранному решению.

`conditions` — описательные строки, не исполняемый код и не booleans. Детерминированный проход считает все selected EXECUTION prerequisites обязательными AND-входами; применимость условий, guards и правильность предпосылок проверяются через canonical план, E и независимый semantic review. Условная ветвь не получает доступный output от произвольной строки условия.

## Контракт перехода и план

Каждый `contracts[]` содержит `id`, `version`, `format`, `semantics`, `availability`, `external_conditions`, `error_behavior`, `acceptance_check_ids`, `plan_refs`. `external_conditions` и `acceptance_check_ids` — списки; accepting C list непуст и сохраняет canonical accepting check входа/выхода. Каждая `plan_refs[]` связывает exact `stage_id`, `kind: INPUT|OUTPUT`, `contract_id` и, когда применимо, `path` с canonical `inputs_contracts` выбранного плана. Смысл совпадает с плановым contract; format/version тоже совпадают там, где они объявлены. Совпадение имени файла или типа node не доказывает совместимость значений, units, version, timing или ошибок.

`DATA.contract_id` и `CONSUMES.contract_id` адресуют архитектурный `contracts[].id`; вложенный `plan_refs[].contract_id` адресует canonical ID входа/выхода stage. Для OUTPUT ref требуется exact `path`. Эти разные bindings не заменяются похожими строками или новым параллельным контрактом.

CONSUMES edge задаёт consumer `contract_id`, а для T — exact `input_id`; C сверяется с canonical `check.inputs`. Единый resolver принимает разрешённые формы INPUT-ID, OUTPUT-ID и exact output path, сохраняя canonical ID, ownership и контракт без выдуманного INPUT wrapper. Неоднозначные aliases и неизвестные ссылки отклоняются. Порядок обе проверки получают из одного normalizer: task dependencies и task-to-task `local_execution_graph.edges`, плюс canonical stage DAG; entry/exit не становятся задачами. Дублировать законное local edge в dependencies не требуется. Producer и consumer обязаны согласовать contract. Если IDs контрактов различны, edge связывает `compatibility_check_id` с отдельной C: exact пара `compatibility_contract_ids`, содержательные `procedure` и `expected_observation` в canonical C, а не декларация «совместимы». До плана будущий C node может нести эти поля как проектную спецификацию; к GATE-5 они разрешаются в current plan. Все task inputs/outputs/acceptance checks разрешаются в actual current S/T/C и [typed plan contracts](contracts-v4.1.md#входы-и-выходы-плана). Финальный DATA producer совпадает с единственной определённой implementing T и `producer_task_id` canonical output. Canonical `NOT_APPLICABLE` task и `UNSELECTED_BRANCH` output исключены из конструктивного производства. Они могут сохраняться в истории модели, но не поддерживают active G/R или active consumers; искусственный CONTRIBUTES_TO не возвращает им доступность. Выбранный `SELECTED_BRANCH` output требует принятого непросроченного selection E и exact producer/output binding. Невыбранная ветвь не создаёт доступный output. Параллелизм задаётся планом с совместимыми ресурсами и disjoint writes либо явной сериализацией; исследовательский feedback не разрешает execution cycle.

### Согласование с каноническим планом

Для каждого TASK и CHECK применяй один resolver допустимых INPUT-ID, OUTPUT-ID и exact output path. Проверяй точный источник либо producer/output, формат, версию, доступность и принадлежность потребителя. Совпадение имени входа не допускает замену недоступного внешнего источника другим DATA. Разрешённые формы одной ссылки должны давать одинаковый результат.

Все зависимости из `normalize_plan_order`, включая `local_execution_graph.edges` и выведенные `stage_task_edges` и `stage_check_edges` действующего межэтапного порядка и приёмки, обязаны покрываться EXECUTION-порядком архитектурного графа. Пропуск даёт адресный structural error. Локальное ребро не требуется повторять в `task.dependencies`; принимаемый канонический граф должен сохранять этот порядок для достижимости, impact и представлений.

Обязательные приёмки выводятся из выбранного нормативного плана, `task.checks`, `task.acceptance_criteria`, применимых `edge.check_ids` и `contract.acceptance_check_ids`. Локальный `required: false` не отменяет эти обязательства или их входы. Различай производство выхода и его приёмку: проверка может читать произведённый выход, а последующий потребитель получает результат с достижимой приёмкой. EXECUTION-порядок графа сохраняет путь от точной `output.acceptance_check_id` к последующему потребителю; собственные проверки создаваемого выхода не требуют приёмки до его производства. Будущая `C=NOT_RUN` допустима; недоступный обязательный вход C остаётся блокером. Не создавай цикл допуска производителя через проверку ещё не созданного им выхода.

## Два прохода достижимости

От G назад получи closure всех обязательных результатов/R и prerequisites выбранного механизма. Для каждого required CAPABILITY/COMPONENT/ALGORITHM/DATA/T/C нужен определённый достижимый путь получения и использования. Выбранный производимый output, не участвующий в достижении G/R, требует устранения или обоснованного изменения модели; альтернативная исследовательская ветвь не становится обязательной операцией выбранного решения.

Вперёд вычисли наименьшую неподвижную точку: начни с допустимых исходных ресурсов, добавляй результат операции только после доступности **всех** её обязательных входов, условий, контрактов и принимающих проверок; повторяй до отсутствия новых результатов. Простой обход «достаточно одного входящего edge» не проверяет композицию. Обязательная CAPABILITY требует typed REALIZES от конструктивного COMPONENT/ALGORITHM с implementing T; G получает typed CONTRIBUTES_TO от такой способности, R — от конструктивного допустимого satisfier. Generic REQUIRES не заменяет producer или удовлетворение требования. PLANNED producer может дать будущий результат в исполнимом плане без hash, а недоступный external input не становится источником от записи `AVAILABLE` без E.

Проверяй missing producer/input/output, недостижимый компонент/task, contract mismatch, преждевременное потребление, недостижимый execution cycle, потерянный R, неиспользуемый выбранный output и отсутствие положительного source→G пути. Существование двух формально законченных модулей не закрывает отсутствующий переход между ними. Законная RESEARCH_FEEDBACK может быть циклической; она не участвует в execution order. Evidence support/provenance не может ссылаться по кругу для подтверждения самого себя.

`model_integrity_status` и `structural_status: PASS` означают согласованность модели. Достижимость возвращается отдельно: `reachability_status: COMPLETE|BLOCKED`, `blockers`, `blocked_nodes`, `unselected_nodes`. Честный EXTERNAL/UNAVAILABLE с источником, владельцем, условием получения и блокированным потребителем принимается как условный SYNTHESIS, сохраняет независимую работу и даёт `readiness_status: BLOCKED`, а не READY. Необъяснимая недостижимость, неверные типы, ссылки, контракты или циклы остаются errors даже при внешнем блокере. `COMPLETE` означает конструктивный проектный путь, не исполнение этого пути. Он не утверждает фактическое выполнение будущих T/C, существование future artifacts, качество данных, научную состоятельность предпосылки, прибыль или observed G.

## Сопоставление с кодом и selective recheck

Для `project_kind: EXISTING` сравни normative graph с фактической реализацией. Используй exact файлы и интерфейсы, конфигурацию, contract definitions и integration checks; imports/calls дают только часть структурных сведений. Каждая строка `implementation_map` связывает `target_id`, `status`, `files`, `evidence_ids`, `evidence_scope`, `required_action`, `verification_basis`. Каждый file имеет `path` и `sha256`; область и необходимое действие обязательны для честного объяснения состояния.

| `status` | Значение |
|---|---|
| `IMPLEMENTED_VERIFIED` | Реализовано и квалифицировано применимой integration E для exact target/files. |
| `IMPLEMENTED_UNQUALIFIED` | Реализация обнаружена, подтверждения обязательных свойств недостаточно. |
| `PLANNED_ABSENT` | Предусмотрено планом, фактической реализации ещё нет. |
| `CONTRACT_DIVERGENCE` | Фактическая реализация расходится с нормативным контрактом; требуется адресный repair/recheck. |
| `EXTRA_UNJUSTIFIED` | Обнаружен код/переход, не обоснованный нормативной архитектурой. |

EXISTING mapping покрывает каждый selected COMPONENT/ALGORITHM и каждый selected EXECUTION edge; code additions перечисляются явно. Для `project_kind: NEW` пустой map допустим и не утверждает существование реализации.

`verification_basis` — `INTEGRATION_CHECK|STATIC_ONLY|NONE`. Verified implementation требует exact accepted E с содержательными `procedure`/`observation` и `integration_check`: `check_id`, `status: PASS`, `target_ids`, `file_hashes` (`path→sha256`), связывающего именно этот target и файлы. Статусы IMPLEMENTED_UNQUALIFIED, CONTRACT_DIVERGENCE и EXTRA_UNJUSTIFIED тоже требуют непустых exact files и текущих accepted E наблюдения. PLANNED_ABSENT может описывать будущее действие без выдуманного существующего файла. Static imports/calls, существование файла и совпавшая строка status не заменяют integration E. Несовпадение текущих bytes с recorded hash — drift, а не прежний PASS.

При изменении узла вычисли affected downstream nodes/tasks/checks по реальным production/consumption/execution dependencies, включая owning T и проверяющие C. Ownership/VERIFIES создают обязательство recheck и не добавляют обратные edges в исполнимый DAG. RESEARCH_FEEDBACK не расширяет execution invalidation автоматически. Сохрани отдельно affected и preserved IDs; затронутые consumers требуют `NEEDS_RECHECK` и адресного action. Неизменённые результаты переиспользуются только после проверки применимости exact dependencies; affected/preserved отчёт не выполняет задачи и не принимает их вместо координатора. Смена evidence дополнительно использует существующий provenance→execution механизм. Повтор всего проекта допустим при доказанном общем воздействии, а не по умолчанию.

## Машинная проверка и производные views

Pure API в [architecture_validation.py](../scripts/architecture_validation.py) `validate_architecture_graph(graph, context)` возвращает `errors`, `model_integrity_status: PASS|FAIL`, `structural_status: PASS|FAIL`, `reachability_status: COMPLETE|BLOCKED`, `blockers`, `blocked_nodes`, `unselected_nodes`, `readiness_status: BLOCKED|NOT_ASSESSED`, `constructive_reachable`, `required_nodes`, `unused_outputs`, `semantic_validity: NOT_ASSESSED`, `implementation_readiness: NOT_ASSESSED`. Context связывает approved goal/R, accepted exact sources/E и current S/T/C/contracts; Python option `plan_required=False` допускает проектные S/T/C при SYNTHESIS, финальная проверка требует разрешения всех IDs. Callbacks `acyclic` и `reachable` переиспользуют существующий validator. `architecture_graph_sha256` вычисляет hash graph через `prep_validation.canonical_yaml_bytes`, совпадающий с `qs.canon_yaml`; не подменяй его hash Markdown или произвольного textual YAML.

`derive_architecture_views` выводит `functional`, `data_contracts`, `implementation`; `render_architecture_mermaid` создаёт Mermaid. `subsystem_id` выделяет подсистему, `goal_path` — путь к G, поэтому пользователь не обязан читать единственную диаграмму всего проекта. `architecture_impact(graph, changed_node_ids)` возвращает `affected_node_ids`, `affected_task_ids`, `affected_check_ids`, `preserved_node_ids`, `preserved_task_ids`, `preserved_check_ids`. `compare_architecture_implementation` возвращает `coverage_missing`, `discrepancies`, `errors` и `implementation_readiness: NOT_ASSESSED`, не verdict READY. Текущие file hashes проверяются только при supplied `context.implementation_files`; отсутствие наблюдения нельзя называть fresh code recheck. Public CLI и правила безопасной записи views находятся в [YAML-контракте](data-contract.md).

## Независимый аудит

Для marked run FINAL_AUDIT содержит `architecture_ref` и обязательный `architecture_review`:

- `structural_report_sha256` exact canonical YAML отчёта pure validator для выбранных graph/plan/evidence bytes;
- `semantic_outcome: PASS|FAIL|NOT_VERIFIED`, `procedure`, `observation`;
- `evidence_ids` exact current nonstale accepted E;
- `findings` — строки с `id`, `target_id`, `evidence_ids`, `required_action`;
- `limits` — список строк с областью и пределами проверки.

READY требует structural/model integrity PASS, `reachability_status: COMPLETE`, пустых `blockers`, независимого semantic PASS, непустого accepted E и отсутствия открытых architecture findings; остаются обязательными все остальные восемь audit areas, independence и правила verdict/readiness. Structural connectivity сама не заполняет semantic PASS. Честный non-READY audit может указать NOT_VERIFIED, сохраняя все обязательные поля, conditions и next path. Future checks не дают implementation/effect READY.

Аудитор независимо пытается разорвать source→G путь, убрать обязательный producer/contract, потерять R, заменить positive composition суммой локальных PASS, создать execution/evidence cycle, скрыть недоступный external input или unmodeled code addition. Finding возвращается соответствующему автору; recheck ограничивается изменёнными exact bytes и затронутыми областями. Аудитор не правит проверяемую модель.

## Испытания и пределы утверждений

Обязательные положительные/отрицательные случаи: missing producer; законный future task; недостижимый execution cycle; законная research feedback; потерянный R; разрыв между формально готовыми модулями; отсутствие source→G; normative/code drift; selective recheck с preserved независимой ветвью; запрет READY из connectivity. Проверяются как детерминированные контракты/публичная механика, так и содержательные destructive задания настоящим отдельным агентам. Детерминированная fixture не считается native agent execution, а одно независимое архитектурное задание — полным новым 18-role прогоном. Результаты и ограничения конкретного выпуска публикуются с exact subject hashes; инструкция сама не утверждает выполнение этих испытаний.

Детерминированные примеры находятся в [test_architecture.py](../tests/test_architecture.py), интеграционные сценарии — в [test_architecture_integration.py](../tests/test_architecture_integration.py). Это адреса проверок, а не заявление об их выполнении в данном run.
