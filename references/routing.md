# Маршрутизация и граф назначений

Координатор — единственный диспетчер. Роль, назначение и попытка различны: `role_id` задаёт функцию, `assignment_id` — пакет работы, `agent_id` — фактического субагента, `attempt_id` — конкретный запуск, `result_id` — принятый неизменяемый результат. Registry связывает result с точными `content_hash/artifact_type/role_id/agent_id/assignment_id/attempt_id/input_snapshot/dependency_hashes/exposure_record/event_hash/path`; ссылка на hash или объявленный `ACCEPTED` без этой связи ничего не закрывает. Используй один `run_id`, отдельные `input_snapshot_id`, `index_revision` и `release_id`.

## Граф допуска

Подготовка следует [единственному нормативному протоколу](preparation.md) и предшествует основному графу:

- `GOAL_TO_PLAN`: exact goal/materials → текущий `OP-VECTOR` → явный exact approval → семантически выбранная карта тех же десяти `P` → десять независимых blind FRAMING по десять initial Q → распределённое расширение существующими P/операторами → нормализация → десять source checks → immutable admission;
- `BANK_TO_PLAN/APPROVED_RUN_IMPORT`: локальный immutable import foreign Vector/APPROVE/statement + exact готовый bank → current `OP-VECTOR/BANK_APPLICABILITY` → карта тех же десяти исполнителей → проверки покрытия без генерации Q → десять source checks → immutable admission. `BANK_TO_PLAN/EXTERNAL_READY_BANK`: exact original+normalized bank → local OP-VECTOR и реально необходимое approval/authority → та же applicability/admission цепочка без foreign fabrication и генерации Q.

Критический prep gap блокирует Core. Ни координатор, ни один оператор не генерирует весь банк единолично. В BANK импорт не дополняется и не переписывается. Admission фиксирует фактический набор тех же десяти `P`; Core не выбирает новый набор и не повторяет подготовку.

После admission минимальные зависимости Core:

1. admitted input → слепой пакет;
2. в GOAL повторно используются выбранный принятый `OP-VECTOR` и десять admitted blind FRAMING; в BANK immutable import и текущий `BANK_APPLICABILITY` задают G/R/NOT_G, а десять P выполняют Core framing без генерации Q;
3. замороженный framing каждой `P` → выдача ей назначенных `Q`;
4. первичные ответы → рецензии; принятые `G/R` также допускают раннюю `OP-PROJECTION`;
5. критические `D` и противоречия → `OP-DIVERGENCE`, `OP-REVERSE`, `OP-INDULGENCE` по готовности их входов;
6. замороженный общий протокол сравнения + варианты → `OP-SYNTHESIS` → повторная проекция затронутых путей;
7. принятые исследовательские результаты → `PLAN-AUTHOR`;
8. замороженное нормативное ядро → `FINAL-AUDITOR`;
9. правка автором → неавторская перепроверка точных изменённых байтов → новый выпуск.

Это граф зависимостей, а не девять последовательных волн. Независимые узлы можно выполнять параллельно в лимите. Производитель должен иметь принятый результат до старта потребителя.

## Детерминированное предложение и семантический выбор

Механический helper строит **предложение**, а не назначение: из exact `input_snapshot`, accepted registry, dependency edges, assignment state, leases, write/resource conflicts и текущего `NEEDS_RECHECK` он исключает заблокированные, stale, уже принятые и неизвестно запущенные узлы. Оставшиеся proposal entries сортируются детерминированно по стабильному ID и содержат причины готовности, blockers, dependency hashes, write scope и требуемую роль. Одинаковое состояние даёт одинаковое предложение.

Корневой координатор семантически утверждает автора и группу по компетенции, последствиям ошибки, допустимым средствам и ресурсу из следующего раздела. Он сохраняет `selection_trace`: рассмотренные proposal IDs, выбранные assignment/role, основание, отклонённые кандидаты и snapshot/revision предложения. Лексическое сходство, score или порядок ID не являются решением о компетенции. Helper не вызывает модели, не спавнит роли и не принимает gate.

Группа содержит только assignments без блокирующих рёбер между собой и без конфликтующих writes/resources. Одновременно активны не более трёх групп. Перед внешней отправкой используй `claim-effect --external-dispatch` и отправляй только при `SEND_ONCE`. В пределах одного run claim атомарно проверяет `resource_keys` и префиксное пересечение `sole_writer_paths` с уже удерживаемыми ресурсами. `effect_resource_occupied` указывает удерживающий occurrence, claim и статус. Для непересекающегося назначения можно получить свой claim.

## Выбор автора Q

Назначь каждому исходному `Q` ровно одного primary author. Оцени по порядку:

1. предметная компетенция и способность проверить механизм;
2. зависимости `Q` от уже назначенных `Q/E` и возможность общего пакета;
3. последствия ошибки и нужная глубина;
4. доступ к допустимым источникам/инструментам;
5. текущая нагрузка и конечный бюджет.

Простой проверяемый факт не требует тяжёлого опыта. Центральный механизм, критическая граница или решение с дорогой ошибкой требуют конкурирующих объяснений и различающего `E`. Один автор может получить связанный пакет, но каждое `Q` сохраняет собственный адресный исход.

После primary routing назначь рецензии так, чтобы каждая `P` содержательно проверила минимум две другие перспективы и каждая `P` получила внешнюю рецензию. Для критического `D` назначь предметного и системного неавторов с различными check_id, процедурами/рисками, provenance и принятыми pre-exposure framing result IDs/hashes. `target_result_id/target_hash` и `blind_input_result_id/blind_input_hash` должны разрешаться в accepted registry того же run/snapshot. Общий предмет или первичный source не обнуляет независимость различимых проверок. Опубликуй таблицы `Q → author → reviewers`, `D → subject/system checks` до рецензирования.

## Пакет назначения

Каждый рабочий пакет содержит:

```text
run_id / input_snapshot_id / role_id / assignment_id / attempt_id
actual agent_id после запуска
точные назначенные R/Q/D/E и ожидаемый потребитель
доступные пути и хэши входов; зависимости и их принятый статус
разрешения чтения/записи и единственного автора пути
ожидаемый формат, критерии принятия, конечный бюджет и stop
checkpoint path, deadline/heartbeat и recovery rule
```

В новом режиме пакет, launch record, context, result, registry entry и result transaction дополнительно несут точные `input_mode`, `lifecycle_scope` и binding snapshot. Для PREP обязательны `lifecycle_scope: PREP` и один точный `prep_stage`; для Core — `lifecycle_scope: CORE` и `prep_admission_sha256`. Пропуск этих полей не включает legacy: legacy допустим только при положительно проверенном старом snapshot и exact bank/original-bank layout.

Слепой пакет объявляет себя самодостаточным и зависит от режима. GOAL PREP FRAMING получает exact approved Vector/G/R, нужные facts/materials и собственную perspective row, но не получает bank, foreign P results или preferred solution. BANK Core FRAMING получает imported approved Vector/G/R через admission binding, нужные facts/materials и собственную row, но не supplied bank, foreign P results или preferred solution. Approved R labels являются входом framing, а не раскрытием готового банка. Запускай framing в реально свежем контексте без наследования истории чата (`fork_turns="none"` либо эквивалент, когда доступно), передавая только bank-free packet и выбранные инструкции; явно прикажи пропустить workspace-memory/rollout lookup и запрети чтение полного run-каталога, bank path и иных файлов вне allowlist. Сохрани фактический launch mode, allowed paths и disclosure/read log. Автоматически внедрённый system/role context регистрируй отдельным exposure class от добровольных file reads; проверь его на наличие bank/foreign results. Различай `protocol_blindness` и `technical_isolation`. Fresh context без банка, точный allowed-path readset и наблюдаемый read log позволяют поставить `protocol_blindness=VERIFIED` в обычном несекретном исследовании даже при общем filesystem; это не утверждение об OS ACL. Если контекст унаследовал банк, read log неизвестен, роль добровольно прочитала memory/workspace вне allowlist или был иной запрещённый disclosure, `protocol_blindness=NOT_VERIFIED` до свежего reframing. Для настоящего held-out/секретного acceptance set отдельно требуется подходящая техническая изоляция; без неё `technical_isolation=NOT_VERIFIED` блокирует только зависящий от секрета claim/path. Текстовая метка «blind» не создаёт ни одного доказательства.

Рабочий пакет содержит только назначенные `Q` и нужные зависимости; полный архив не передаётся без необходимости. Роль возвращает те же ID, состояние знания, основания/E, изменения D, открытые условия, влияние на G и следующего потребителя.

Адресная сборка рабочего контекста начинается с exact assignment и добавляет только: общие обязательные ограничения; инструкции роли и текущей фазы; назначенные Q/R/D; принятые прямые и требуемые транзитивные зависимости; применимые противоречия/E; критерии приёмки; tool/authority/resource limits. Manifest пакета перечисляет для каждого файла или объекта `path/result_id/content_hash`, основание включения и dependency edge. Одно общее E включается один раз и связывается со всеми назначенными consumers; копии его текста не становятся новыми E.

Если роли не хватает входа, она возвращает запрос расширения с exact ID/типом, причиной и затронутым check. Координатор разрешает ссылку и полномочие, затем выпускает новую revision пакета/readset с новым hash; прямое чтение всего run не заменяет это действие. История superseded пакетов и фактический read log сохраняются, чтобы свежий контекст не скрывал пересмотр или первичное E.

Role slots ссылаются на `result_id` правильного `artifact_type` и той же роли/агента: P — `FRAMING` и `PRIMARY`; `OP-VECTOR…OP-INDULGENCE` — `OPERATOR`; `OP-SYNTHESIS` — `SYNTHESIS`; `PLAN-AUTHOR` — `PLAN`; `FINAL-AUDITOR` — `FINAL_AUDIT`. Для P framing дополнительно обязательны pre-bank blindness/exposure record. Каждый assignment с `ACCEPTED` ссылается на фактически принятое событие/result, совпадающее по assignment/attempt; один result не заполняет несколько назначений.

Partition сохраняет подготовительные и основные результаты раздельно. В GOAL foundational PREP — выбранный текущий accepted `OP-VECTOR` и десять admitted текущих FRAMING. В BANK foundational PREP — импортированный Vector как immutable input и текущий accepted `BANK_APPLICABILITY`; иностранный result не становится записью текущего registry. Expansion, невыбранные и superseded prep revisions остаются provenance. В Core и выпуск попадают только PLAN IDs из текущего `current_plan_projection`; сохранённая PLAN history не становится второй текущей моделью.

## Параллелизм и зависания

Держи не более трёх активных групп и одного тяжёлого вычисления; учитывай более строгий лимит среды. Один изменяемый путь имеет одного автора. Перед запуском проверь свободный slot/lease, готовность exact dependencies и отсутствие конфликтующей записи. Не добавляй в группу узел только ради заполнения лимита.

Для каждого назначения заранее установи разумный deadline и checkpoint interval в единицах среды, а не универсальные минуты. По heartbeat:

- `RUNNING` с прогрессом — продолжить до бюджета;
- deadline с checkpoint — остановить/прервать, сохранить попытку, создать новую попытку от checkpoint;
- при неизвестном исходе claim удерживает свои ресурсы. `RECIPIENT_CONFIRMED` подтверждает доставку и сохраняет удержание. Освобождение требует `NOT_SENT` либо независимого принятого OBSERVED E с `effect_terminal`, точным назначением, claim, получателем, хешем выданного контекста при его наличии и фактическим terminal receipt. При раннем ACK передай terminal E отдельным `reconcile-effect`. При одновременном подтверждении доставки и завершения оба факта могут быть в одном E. Повтор claim сохраняет `HOLD_RECONCILE` и после освобождения;
- два содержательных провала после первичной попытки — эскалировать как открытый блокер/перепланирование, не повторять бесконечно.

Не смешивай format repair с содержательной попыткой. Одна исправимая ошибка формата допускает ремонт без изменения содержания. Все попытки и причины остановки остаются в журнале.

## Кэш общих свидетельств

Кэшируй только воспроизводимые `E` по ключу:

Ключ задавай структурированными полями [контракта данных](data-contract.md): точные `input_hash`, `tool_version`, `policy_hash`, `exposure_key`, `freshness`. Если нужен единый digest, сохрани эти поля в schema-valid YAML с однозначно типизированными значениями и хэшируй точные байты этого файла; не склеивай неразмеченные строки или байты.

Запись хранит producer, procedure, created_at, ограничения, consumers и хэш результата. Не переиспользуй при изменении входа, версии средства/источника, политики/параметров, полномочий, временной экспозиции или области. `evidence_provenance_edges` и `execution_edges` следуют [контракту v4.1](contracts-v4.1.md#три-разных-графа); инвалидация распространяется транзитивно только по фактическим consumers, а повторяются только затронутые узлы. `UNKNOWN`, ошибка среды и неполный E не кэшируются как подтверждение свойства.

## Routing внешнего GitHub-чтения

Root семантически решает, какому существующему P/operator/planner/auditor нужен внешний read, и динамически инвентаризирует фактически доступные GitHub read tools перед assignment. Packet сохраняет required capability, exact approved tool names/operations, repository/commit/path scope, finite budget/stop, expected `EXTERNAL_IMPLEMENTATION` fields и запрет remote mutation/foreign execution. Server namespace и fixed toolset не предполагаются.

Blind PREP-2/Core framing не получает coordinator shortlist, rankings или preferred mechanism. После freeze GitHub assignment идёт обычным DAG и ≤3-group limit. `GITHUB_UNAVAILABLE` сохраняет exact missing capability/tool/error/attempt/time; Root продолжает готовые независимые nodes и блокирует только зависимый claim. Parent success, permission metadata или видимый tool не доказывают child availability либо transport-wide ACL.

Marked Double-Iceberg routing сохраняет те же nodes: десять blind positions → addressable Q/E/H/D/reviews → OP-PROJECTION and OP-REVERSE current heads → SYNTHESIS → PLAN → FINAL_AUDIT. Coordinator не пишет substantive maps. View selection требует одну linear non-stale head каждого kind; historical exemption применяется только к exact superseded OPERATOR ancestors этой lineage без current consumer.
