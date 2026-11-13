# YAML-состояние, хранение и выпуск

YAML — канонический формат структурированных результатов. Markdown создаётся только командой render из конкретного YAML и не редактируется как нормативный источник.

```text
runs/<run_id>/
  input/goal.<исходное расширение> | input/materials/
  input/imports/<import_id>/{vector.yaml,approval.yaml,approval-statement.<ext>,binding.yaml}
  input/original-bank.<исходное расширение> | input/original-message.txt
  input/bank.yaml
  input/prep-admission.yaml
  input/snapshot.yaml
  state/current.yaml
  state/approved-map.yaml
  state/approved-maps/<sha256>.yaml
  state/assignments.yaml
  state/events.yaml
  transactions/admissions/  transactions/current/
  positions/  answers/source-outcomes/<result_id>.yaml  hypotheses/  evidence/
  decisions/  reviews/  operators/  experiments/  synthesis/
  gates/GATE-1.yaml ... gates/GATE-5.yaml  gates/GATE-6/<audit-result-id>.yaml
  plan/S-000.yaml
  plan/S-NNN.yaml
  audit/final/<result_id>.yaml
  release.yaml
```

Не создавай центральный изменяемый `RESEARCH.md` или гигантский `research.yaml`. Разделяй:

1. immutable input — банк, требования, проект и полномочия;
2. immutable accepted knowledge — позиции, ответы, гипотезы, E, контрпримеры и reviews;
3. mutable operational state — назначения, попытки, зависимости и следующие действия;
4. frozen normative result — архитектура, план, C, аудит и выпуск.

## Общий YAML-контракт

Для применимых сущностей сохраняй `schema_version`, `id`, `run_id`, `input_snapshot`, `author_role`, `agent_id`, `knowledge_status`, `workflow_status`, `source_refs`, `related_ids`, `revision`, `created_at`, `updated_at`. Не добавляй бессмысленные заглушки для неприменимого поля. `knowledge_status` (`OBSERVED|DERIVED|HYPOTHESIS|REFUTED|UNKNOWN`) не смешивается с workflow (`NOT_RUN|RUNNING|ACCEPTED|BLOCKED|REJECTED|STALE|NEEDS_RECHECK`). Многострочные YAML-значения сохраняют явно сформулированные аргументы, формулы, алгоритмы, предпосылки и выводы; скрытые внутренние рассуждения не запрашиваются.

Используй схемы из `schemas/`. Разбирай YAML только safe loader с запретом повторных ключей, произвольных tags, anchors/aliases и неоднозначных plain scalars. ID, версии и timestamps — явно типизированные строки. Хэш означает SHA-256 фактически сохранённых байтов названного файла.

## Входной снимок

Exact source обязателен для каждого run. `original-bank.<ext>` сохраняет точные байты файла и исходное расширение; сообщение детерминированно сохраняется UTF-8-байтами как `original-message.txt`; `.md` допустим только для реального Markdown. `bank.yaml` — отдельная адресуемая проекция и не заменяет exact source. `snapshot.yaml` связывает paths, byte hashes, bank version, frozen requirements и остальные содержательные входы. Pre-audit manifest включает все immutable input paths независимо от расширения. Перед accept/validate/prepare/finalize/verify сверяй exact source, normalized bank и frozen baseline. Изменение требует нового snapshot/run; state не переписывает baseline и post-release `verify` всегда обнаруживает его drift.

Каждый original Q остаётся в неизменяемом `bank.yaml` дословно с источником/позицией. Ровно один выбранный текущий `SOURCE_OUTCOME` для каждого original Q хранится в versioned принятом `answers/source-outcomes/<result_id>.yaml` artifact type `SOURCE_OUTCOMES` и ссылается на Q из банка. Его `answer_result_id` разрешается через accepted registry в отдельный exact `ANSWER` или `PRIMARY` правильного assignment/role/agent/run/snapshot; запись не может ссылаться на собственный `SOURCE_OUTCOMES` result. `author_assignment_id` обязан совпасть с назначением найденного ответа. `DERIVED_Q` хранится там же с собственным ID и parent links, не подменяя исходник. `state/current.yaml.current_source_outcomes` выбирает exact immutable revision по [контракту v4.1](contracts-v4.1.md#неизменяемая-история-и-текущие-версии). Новое исследовательское состояние никогда не переписывает `bank.yaml`.

## Подготовка и admission

Новый snapshot явно задаёт `input_mode: GOAL_TO_PLAN|BANK_TO_PLAN`; отсутствие поля не означает legacy. `LEGACY_CORE` допустим только при положительно проверенных старой схеме, exact snapshot и паре bank/original-bank. Полный подготовительный протокол и десять checks определены только в [preparation.md](preparation.md).

В `BANK_TO_PLAN/APPROVED_RUN_IMPORT` до snapshot точные bytes иностранного Vector, approval и statement копируются в локальный `input/imports/<import_id>/`. `binding.yaml` связывает source run/snapshot/result/revision/actor, локальные safe-relative paths и hashes; он не содержит external path или current snapshot. Иностранные objects остаются immutable input и не регистрируются как принятые results текущего run. `BANK_TO_PLAN/EXTERNAL_READY_BANK` вместо import сохраняет exact `external_binding`, создаёт local Vector и получает реально требуемое authority без выдуманных foreign artifacts. В обеих ветвях текущий accepted `OP-VECTOR/BANK_APPLICABILITY` фиксирует полное сопоставление G, каждого R и authority; недоказанное обязательное условие блокирует admission.

Admission — один immutable `PREP_ADMISSION` transaction на run. Он связывает mode/snapshot, exact bank и admission replay blobs, expected current и `admission_next_current`; durable transaction является commit point, outputs устанавливаются или сверяются после него, а exact current записывается последним. В GOAL bank создаётся только если отсутствует; в BANK уже snapshot-bound bank только сверяется. Replay разрешён лишь поверх byte-identical expected current и никогда не откатывает более поздний valid descendant.

Resolver возвращает ровно `LEGACY_CORE|PREP_OPEN|ADMISSION_REPLAYABLE|CORE_ADMITTED|INVALID` и действие `NONE|VERIFY_EXISTING|REPLAY_ADMISSION|REPLAY_CURRENT_TRANSITIONS|FAIL`. Pointer без transaction, orphan/branch/conflict, изменённое stable binding или неизвестный descendant дают `INVALID` без overwrite. После admission изменение Vector, approval/import, bank или admission требует нового run.

## Принятие результата

Субагент пишет один новый YAML result назначения в своей области и не меняет общий индекс. Координатор:

1. проверяет schema/version, IDs, точный snapshot, роль/агента/assignment/attempt, разрешения, dependencies и полноту назначенного результата;
2. разрешает каждую ссылку в accepted registry и проверяет expected type, run, snapshot, path и byte hash;
3. под межпроцессной сериализацией сохраняет immutable result;
4. добавляет immutable accept event и provenance result↔assignment/attempt↔role/agent/artifact_type↔snapshot/dependencies/exposure;
5. атомарно публикует производные индексы; accepted result transaction сам по себе не переписывает `state/current.yaml`.

Одинаковые байты разных назначений не схлопывают provenance. Повторная доставка того же `(assignment_id, attempt_id, result hash)` идемпотентна. Иная доставка под тем же ключом — конфликт. Пересмотр создаёт новую revision с `supersedes`; accepted запись не уничтожается.

Публичная команда `commit` выполняет ту же строгую immutable/idempotent операцию, что совместимая команда `accept`, и возвращает только наблюдаемый `ACCEPTED` либо `IDEMPOTENT` с exact result hash. Она не означает содержательного PASS и не меняет маршрут сама.

`state/current.yaml` после admission сохраняет неизменными `schema_version/run_id/input_snapshot/input_mode/admission_transaction_id/bank_sha256/prep_admission_sha256`. Менять его можно только последовательными immutable `CURRENT_TRANSITION` revisions без branch, gap, duplicate revision или rollback. Разрешены ровно `APPROVED_MAP_SET`, `APPROVED_MAP_REVISE`, `STALE_SET_EXTEND`, `RELEASE_SEAL`; каждый transaction связывает exact before/after hashes, after bytes и immutable backing records, а current записывается последним. Неизвестное поле или mutation type даёт `INVALID`, не best-effort repair.

Текущие selectors `current_source_outcomes` и `current_final_audit` содержат exact `result_id/sha256/path/assignment_id/attempt_id` и выбирают одну immutable accepted revision; successor указывает `supersedes`, lineage линейна, а все consumers используют selected exact result. Текущий approved map публикует `current_plan_projection` и его hash из `approved_map.current_plan_graph`: один selected accepted PLAN result на logical stage, один plan version и точные result/hash/assignment/attempt bindings. Retained revisions остаются accepted provenance, но модель, Core manifest, context dependencies, release и verify используют только selected IDs. Stale selected result блокирует; stale superseded history вне projection не блокирует.

## Адресный context

После `assign` команда `context` читает зарегистрированный exact assignment и пишет детерминированный YAML packet. Для нового blind `FRAMING` mode/phase определяет readset: GOAL PREP получает approved Vector/G/R, нужные materials и собственную perspective row; BANK Core получает imported approved Vector/G/R через admission binding, нужные materials и собственную row. Оба исключают bank Q, foreign P results и preferred solution до freeze. Для legacy blind packet действует опубликованный data contract. Для рабочего назначения packet включает назначенные Q/R, role/phase instruction paths и только accepted dependencies, связанные assignment hashes/IDs. Каждый entry сохраняет result ID, artifact type, path, SHA-256 и document; полный run, state history и чужие результаты не подмешиваются.

`--expand RESULT_ID` повторяется для текущих accepted dependencies и contradiction records, уже связанных с assignment. Первая выдача может содержать полные документы по явному запросу. После первой выдачи запрос добавляет полные projections к предыдущему packet того же assignment/attempt, сохраняя его остальные поля. Ранее раскрытые источники остаются в каждом successor и проходят текущую проверку разрешений вместе с новыми. `FRAMING` и собственный `ADVISER_CONTEXT` ограничены базовым пакетом.

Базовые packet/proof сохраняют прежние пути. Для каждого successor сначала записывается immutable proof `transactions/contexts/revisions/<pair-sha256>/<ordinal>.yaml`, затем его точные packet bytes в `state/contexts/revisions/<pair-sha256>/<ordinal>.yaml`. Proof связывает run, snapshot, assignment bytes, оба пути и SHA-256 предшественника, собственные packet bytes и список полных source ID/hash. Нумерация начинается с `00000002.yaml`. Проверяется вся последовательность. Повреждение, пропуск или неполная пара блокируют чтение. Если commit либо effect claim уже связал последний packet, потеря обоих файлов этой ревизии также блокирует чтение. Точный повтор `context --expand` может дописать отсутствующий последний packet из проверенного proof при сохранении полномочий. Новый packet требует нового внешнего `--output`; точные повторные байты допустимы по тому же пути.

Расширение с новыми байтами допустимо у pending assignment до effect claim или `DISPATCH_UNKNOWN`. Готовность `READY_TO_DISPATCH` сама по себе не подтверждает отправку. Для отправки через `claim-effect --external-dispatch` после расширения сверь exact packet SHA-256 с `context_packet_sha256` в свежем `SEND_ONCE`; независимое evidence для `reconcile-effect` связывает тот же хеш с фактическим receipt. `HOLD_RECONCILE` требует наблюдаемой сверки. Выдача и историческая доставка пакета не отправляют его роли.

`CONTEXT_READY` сообщает path/hash/bytes и `blind`. `context --historical --packet-sha256 HASH` возвращает точные байты указанной ревизии. Без хеша историческое чтение выбирает последнюю проверенную ревизию. Свежий commit расширенного assignment обязан указать текущий `context_packet_sha256`; проверка и replay принятого результата используют его сохранённый хеш. Receipt имеет scope `STRUCTURAL_ONLY`; без telemetry `tokens` и `cost` равны `UNAVAILABLE`.

## Восстановление и stale

Checkpoint/attempt сохраняет launch state, последний завершённый атом, частичные E, использованный ресурс и безопасное продолжение. Принятие использует replayable intent/commit journal: сначала durable intent exact assignment/result/path/hash, затем immutable result, commit event и атомарно заменяемые производные индексы. Журнал не является необязательным логом; hash-chain/sequence и commit связываются с immutable record.

После прерывания `recover` проигрывает immutable records/journal и отдельно перечисляет `PENDING_ASSIGNMENT`, `SAVED_UNACCEPTED`, `ACCEPTED`, `ORPHAN_RESULT`, `EVENT_INDEX_DIVERGENCE` и `DISPATCH_UNKNOWN`, затем выдаёт только безопасные next assignments. Производные `current/assignments` восстанавливаются из immutable records, а расхождение/утрата события или orphan не скрываются статусом `RECOVERED`. Принятое применимое исследование не повторяется; orphan и результат без commit не становятся принятыми автоматически.

Для нового режима recovery сначала разрешает или воспроизводит admission, затем проверяет непрерывную цепочку `CURRENT_TRANSITION`, потом восстанавливает accepted results, launch journal и производные индексы. Valid поздний current сохраняется byte-for-byte; committed descendant может быть replayed только exact after bytes. Stable-field drift или invalid descendant никогда не затирается admission-next.

`resume` сначала выполняет recovery, затем детерминированно классифицирует зарегистрированные assignments как `ACCEPTED|READY|WAITING|DISPATCH_UNKNOWN|STALE` и возвращает максимум три явные `ready_groups`; он ничего не запускает. Повторяемый `--changed E_ID` принимает только accepted `EVIDENCE`, распространяет stale по `evidence_provenance_edges`, затем по `execution_edges` и не затрагивает независимые ветви. `DISPATCH_UNKNOWN` не попадает в ready group. Ответ `RESUMED` сохраняет accepted/pending IDs, task classifications, stale IDs и фактические elapsed/reuse metrics; tokens/cost остаются `UNAVAILABLE` без telemetry.

Три графа имеют разные обязанности по [контракту v4.1](contracts-v4.1.md#три-разных-графа): cyclic semantic map, acyclic E→E provenance и acyclic execution dependencies E/Q/D/REVIEW/T/C. Каждый endpoint разрешается в accepted registry или в адресуемый Q/T/C exact plan entity того же run/snapshot; объявленного `node_status` недостаточно. Изменение bytes/tool/policy/exposure/freshness распространяет `NEEDS_RECHECK` транзитивно только по реально разрешённым consumers; READY и принятие stale-потребителя запрещены. Независимая ветвь сохраняется.

## Гейты

Каждый `gates/GATE-N.yaml` содержит `gate_id`, `input_snapshot`, `required_objects`, `completed_checks`, `content_review`, `unresolved_critical`, `decision`, `allowed_next`, `blocked_dependencies`, `evidence_refs` и применимые общие поля. Required object, check evidence, reviewer и target hash разрешаются в accepted registry либо exact immutable input; checks непусты и используют строгий outcome enum. `content_review` — адресуемая принятая review-проверка компетентного неавтора, а не строковая заглушка. Минимум фазы из [контракта v4.1](contracts-v4.1.md#минимум-каждой-фазы-и-гейта) обязателен независимо от заявленного агентом `required_objects`. `PASS` запрещён при непринятом review, пустом/неуспешном check или непустом `unresolved_critical`. Conditional gate не создаёт READY.

## Миграция v3

До миграции составь manifest путей и hashes v3. Копируй исходные E без изменения байтов либо сохраняй внешний адрес; регистрируй `legacy_path/hash`, `canonical_path/hash`, причину и применимость. Markdown v3 остаётся legacy source, а не параллельной нормативной копией. Старый аудит и малый прогон относятся только к прежним байтам/snapshot и не доказывают v4 или крупный банк.

При ужесточении v4-контракта не переписывай уже принятые результаты малого прогона. Сохрани их exact bytes и accept provenance; новый обязательный field оформляй новой revision/assignment либо отдельной migration binding, которая указывает старый result ID/hash, добавленное основание и проверяющего неавтора. Старый result остаётся интерпретируемым, но не закрывает новый gate, пока migration binding не принят. Непринятый отрицательный probe сохраняй как E, а не повышай до accepted result.

## Замороженный выпуск

`prepare` валидирует и замораживает **pre-audit core** с exact immutable inputs, admission, accepted knowledge, synthesis, выбранной `current_plan_projection` и GATE-1…5; `FINAL_AUDIT`, `GATE-6` и `release.yaml` из этого manifest исключены. `FINAL-AUDITOR` проверяет эти exact bytes и создаёт versioned accepted `audit/final/<result_id>.yaml`, связанный с pre-audit core hash; `current_final_audit` выбирает exact revision. Затем coordinator принимает `GATE-6`, который связывает exact selected audit path/hash и закрывает его восемь областей/last-fix check. `finalize` повторно хэширует и связывает три неизменные части: pre-audit core, selected accepted audit и GATE-6; только после этого атомарно публикует `release.yaml`/current release. Placeholder audit, stale audit и внешний расчёт будущего manifest недопустимы.

После ремонта автор создаёт новую core version, а не аудитор; последнюю нормативную правку проверяет неавтор. `release.yaml` связывает exact core, accepted audit result, exact GATE-6, выбранные PLAN IDs/projection hash, verdict, readiness и hashes. Audit и release содержат `readiness` с точными keys `package/runtime/quality/plan/implementation/effect`; значение каждой оси — `READY|NOT_READY|UNVERIFIED|BLOCKED`. Manifest хранится вне проверяемого core; self-hash и hash будущего файла запрещены. Финальная публикация не превращает неполный аудит в READY. Проверенный выпуск может честно иметь `REPAIR_REQUIRED`, `RESEARCH_REQUIRED` или `BLOCKED`.

## Helper

После чтения [контракта данных](data-contract.md) используй literal CLI:

```bash
python3 scripts/qs.py init --root WORKSPACE --run-id RUN-1 --bank bank.yaml --original-bank original-bank.md
python3 scripts/qs.py assign --root WORKSPACE --run-id RUN-1 --assignment assignment.yaml
python3 scripts/qs.py launch-intent --root WORKSPACE --run-id RUN-1 --assignment-id A-1 --attempt-id ATT-1 --state READY_TO_DISPATCH
python3 scripts/qs.py context --root WORKSPACE --run-id RUN-1 --assignment assignment.yaml --output role-context.yaml
python3 scripts/qs.py commit --root WORKSPACE --run-id RUN-1 --assignment assignment.yaml --result result.yaml
python3 scripts/qs.py validate --root WORKSPACE --run-id RUN-1 --phase core
python3 scripts/qs.py recover --root WORKSPACE --run-id RUN-1
python3 scripts/qs.py resume --root WORKSPACE --run-id RUN-1 --approved-map root-map.yaml
python3 scripts/qs.py seal --root WORKSPACE --run-id RUN-1 --release-id REL-1 --phase prepare
python3 scripts/qs.py seal --root WORKSPACE --run-id RUN-1 --release-id REL-1 --phase finalize
python3 scripts/qs.py verify --root WORKSPACE --run-id RUN-1
python3 scripts/qs.py render --source runs/RUN-1/audit/final/RESULT-ID.yaml --output final.md
```

`--original-bank` передавай, когда есть отдельный exact source; для сообщения используй детерминированно сохранённый `original-message.txt`. `accept` остаётся совместимым именем той же операции, что `commit`. Для адресного расширения повторяй `context --expand RESULT_ID`; для evidence drift повторяй `resume --changed E_ID`. Не переименовывай произвольный формат в Markdown. `render` создаёт derived view. Helper имеет scope `STRUCTURAL_ONLY` и зависит от `PyYAML>=6.0`; его PASS не доказывает предметную истинность, причинность, реальную независимость, runtime-доступ к MCP или G.

Этот literal блок относится к опубликованному helper contract. Двухрежимные Scope05 entry points и admission guards опубликованы в `scripts/qs.py` и [data-contract](data-contract.md); их наличие подтверждает реализацию маршрута, но не полную поведенческую квалификацию. Не имитируй lifecycle ручной подменой файлов. Исторические отрицательные и ограниченные receipts сохраняются отдельно и не повышаются до current PASS.

## Внешние immutable материалы

`EXTERNAL_IMPLEMENTATION` хранится в существующем `evidence/`, без новой базы или result type. Ключ exact материала: `repository.full_name + repository.commit_sha + path + content_sha256`; для excerpt сохраняется адресуемый bounded chunk/range и hash. Full README/history/code не дублируются в карточках и prompts: consumer получает conclusion, exact refs и необходимый excerpt, raw bytes остаются optional addressable material.

Одинаковые commit/path/bytes загружаются один раз и связываются со всеми Q/D consumers; отдельные роли сохраняют независимые interpretations. Branch head не перезаписывает commit-bound E. Новая accepted версия immutable, а drift/staleness распространяется только на фактических transitive consumers; независимые ветви и прежние raw bytes сохраняются. `UNKNOWN`, unavailable tool и неполный candidate не кэшируются как подтверждение свойства. Подробный контракт — [GitHub](github.md).

`iceberg_position` хранится внутри immutable accepted P FRAMING, а `iceberg_view` — внутри immutable accepted OP-PROJECTION/OP-REVERSE result. Это не новые artifact types или registry. Current head каждого view kind выводится из linear `supersedes`/`supersedes_view_id` lineage и exact hashes; branch/gap/stale/hash drift запрещены. Changed E инвалидирует только фактические H/Q/D/execution consumers и view с exact affected source binding; successor переиспользует unaffected members. Полный контракт — [Double Iceberg](double-iceberg.md).
