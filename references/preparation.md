# Подготовка цели и исследовательского банка

Подготовка предшествует шести основным фазам и не создаёт седьмую фазу. Она переводит один из двух точных входных режимов в неизменяемый допуск банка. После допуска основной цикл использует принятые G/R, десять P, банк и связи без повторной генерации.

## Режимы

`GOAL_TO_PLAN` получает сформулированную цель, материалы и ограничения. Банк до допуска отсутствует. `BANK_TO_PLAN` получает точный готовый банк, цель, требования и материалы: `APPROVED_RUN_IMPORT` также получает ранее утверждённый Вектор/approval, а `EXTERNAL_READY_BANK` создаёт local Vector и получает только требуемое authority без foreign fabrication. Режим и bank entry kind задаются явно. Совместимый запуск без `mode` допустим только для положительно распознанного прежнего layout с точными bank/original-bank; отсутствие новых полей само по себе не превращает новый run в legacy.

Оба режима используют те же 18 ролей, шесть основных фаз, 14 приёмов, восемь областей аудита и `S-000`. Подготовка не добавляет роль, runtime, registry, базу или сервис.

Каждый новый current-release snapshot обоих режимов pins `methodology_features: ["DOUBLE_ICEBERG_V1", "ARCHITECTURE_GRAPH_V1"]`. Уже допустимый snapshot без соответствующего marker может продолжиться только как `LEGACY_QUALIFIED` для этой функции. Markers требуют [typed Double-Iceberg contract](double-iceberg.md) и [архитектурный граф](architecture-graph.md); опциональное отсутствие обязательного position/view/graph в marked run не позволяет пройти соответствующий gate.

## PREP-1 — Вектор

В `GOAL_TO_PLAN` `OP-VECTOR` читает точную цель, материалы, текущее состояние и полномочия. Он адаптивно выделяет только решения владельца: обязательный полезный результат и получателя, false success, запреты, жёсткие ограничения, полномочия и допустимые компромиссы. Не спрашивай владельца, какой алгоритм, архитектуру, свидетельство, скрытое ограничение, механизм или альтернативу выбрать: это `open_research_questions`.

Принятый result `OP-VECTOR/OPERATOR` содержит `G`, `NOT_G`, получателя, область, критерии успеха, R, hard constraints, изменяемые предпосылки, текущее состояние, ресурсы, authority, `owner_decisions` и исследовательские неизвестные. Покажи Вектор понятным языком и получи отдельное явное решение по его exact result ID/hash/revision. Молчание не означает `APPROVE`. `ACTUAL_OWNER` связывает адресуемое наблюдение пользователя, но структурная проверка не аутентифицирует человека. `SIMULATED_TEST` допустим только для fixture и не даёт readiness.

В `BANK_TO_PLAN/APPROVED_RUN_IMPORT` до построения snapshot скопируй иностранные Vector, `APPROVE` и approval statement точными байтами в `input/imports/<import_id>/`. Локальный `binding.yaml` сохраняет исходные run/snapshot, actor/provenance, относительные paths и hashes, запрещает внешний path и `current_input_snapshot`; затем snapshot однонаправленно связывает завершённый import. Иностранные объекты не переписываются и не входят в current accepted registry. В `BANK_TO_PLAN/EXTERNAL_READY_BANK` используй public `--external-bank ORIGINAL`; сохрани exact `external_binding`, local OP-VECTOR/approval/applicability и не создавай foreign artifacts.

Текущий `OP-VECTOR` создаёт принятый `BANK_APPLICABILITY`: полные сравнения G, каждого R и authority с outcomes `UNCHANGED|CHANGED|MISSING|EXTRA|UNPROVEN`. Только `UNCHANGED` для G/authority, полное однозначное `UNCHANGED`-сопоставление R и допустимые десять checks разрешают переиспользовать exact иностранный `APPROVE` без повторного согласования. Любой другой исход блокирует admission. Этот result не является новым утверждением Вектора и не доказывает личность владельца или семантическое равенство сам по себе.

## PREP-2 — десять перспектив и первичные вопросы

Root семантически выбирает ровно десять необходимых профессиональных профилей `P-01…P-10` по принятому Вектору и фактическим материалам. Для каждого зафиксируй `title`, `competence`, `analysis_scope`, `unique_contribution`, `boundary`, `plausible_blindspots` и необходимость. Названия, lexical score или косметическое переименование не доказывают различие. Десять `agent_id` различны; тот же role-to-agent map используется в дальнейшем framing, PRIMARY и peer review.

В `GOAL_TO_PLAN` каждая P в свежем blind context получает утверждённый Vector как bounded projection G/R/scope, собственную строку perspective map и свои инструкции. Исходные materials не раскрываются автоматически: Root выбирает только необходимый immutable approved brief существующим `assignment.allowed_reads`, default none. Context проверяет exact path/hash, отклоняет original/normalized bank binding и foreign result, включает выбранный material один раз; unselected materials остаются snapshot-bound для последующей nonblind работы. Пакет не содержит bank, foreign P results или preferred solution. Каждая P возвращает один принятый `FRAMING` по [typed PREP-2 template](../templates/prep-framing.yaml) и ровно десять `initial_questions`. В каждой строке Q поля `id`, `unknown`, `why_material`, `changeable_decision`, `applicability`, `evidence_needed` и `origin_role` — непустые quoted strings; `g_refs`, `r_refs` и `not_g_refs` — YAML lists только из strings, причём `g_refs` непуст. ID десяти вопросов одной P различны и не сталкиваются с другими initial Q. Не подставляй list вместо prose string и не сворачивай refs в одну строку. Это единственная квота вопросов; десять initial sets сохраняются неизменяемо. Hashes и IDs из шаблона являются placeholders, пока не заменены exact runtime bindings, и сами по себе не дают evidence, approval или acceptance.

В result `id` равен `assignment.result_id`, `author_role/agent_id/run_id/input_snapshot/assignment_id/attempt_id/artifact_type` совпадают с зарегистрированным assignment/run, `prep_stage` равен `PREP-2_PERSPECTIVES`, а `vector_result_id` равен assignment `goal_result_id`. `vector_sha256`, `approval_sha256`, полный `perspective_profile` и его canonical hash совпадают с immutable Vector/approval/map bindings; `context_packet_sha256` хэширует exact сохранённый context данного assignment/attempt. Эти равенства обязательны вместе с `blind_input`, а не заменяются одинаковыми placeholder strings.

В `BANK_TO_PLAN` perspective map выбирает десять исполнителей Core. Подготовка сохраняет готовый банк и пустые initial Q. В FAST/DETAILED рецензент описи сначала проходит слепой PREP-2 с точными Vector/approval и собственным профилем. Для него и любой P с уже принятым слепым PREP-2 используй точный FRAMING и тот же handle в Core. Остальные P проходят Core Phase 1 со скрытыми банком и чужими результатами до своего freeze.

Каждый blind FRAMING обоих режимов также содержит непустой собственный `iceberg_position` по `schemas/iceberg-view.yaml`: проверяемые условия, способности, препятствия, предпосылки, механизмы, наблюдения и неизвестные для CURRENT/DESIRED/CROSS_VIEW. Он создаётся до доступа к bank Q, чужим P results, preferred mechanism и merged map. Это не premature H/Q: поздняя канонизация сохраняет exact `origin_position_refs`.

## PREP-3 — расширение и нормализация

В `GOAL_TO_PLAN` Root распределяет расширение между существующими P и операторами, прежде всего `OP-PROJECTION` и `OP-DIVERGENCE`; координатор не генерирует весь банк один. Применяй восемь содержательных операций: semantic deduplication, hidden assumptions, derived unknowns, cross-discipline contradictions, NOT_G projection, alternative framing, functional completeness и role blindspots. Концептуальная cross-perspective dependency сохраняется в lineage существующим enum `CROSS_DISCIPLINE_CONTRADICTION`, а концептуальная alternative — enum `ALTERNATIVE_FRAMING`; это каноническое кодирование, а не смена методологического смысла или новая операция.

После freeze десяти initial FRAMING PREP-3 может адресно использовать [GitHub-протокол](github.md) как один из способов расширения: искать требуемую способность прямым, функциональным, альтернативным и negative-experience маршрутами. Coordinator shortlist/ranking не попадает в PREP-2. Новый Q из внешнего C проходит те же проверки G/R, unknown, applicability, changeable decision, evidence path и lineage; repository, README или популярность сами не создают вопрос либо R. В BANK режиме поиск не переписывает supplied bank: finding применимости остаётся отдельным E/gap.

Каждый нормализованный Q сохраняет text/source offset, unknown key, mechanism, applicability, changeable decision, evidence needed, G/R/NOT_G refs и lineage: operation, source Q/result/role IDs и причину. `SEMANTIC_MERGE` допустим только для одного неизвестного, механизма, условий, изменяемого решения и различающего E; разные условия или проверки остаются разными Q. Все origins сохраняются.

Цель — 200–300 содержательных уникальных Q, а не quota completion. Ни одному expansion assignment, роли или исходному Q не назначай числовую квоту. Если после доказательной нормализации Q меньше 200, сохрани фактический объём, конкретное основание и карту uncovered areas; padding запрещён. Если критических Q больше 300, сохрани их и приложи resource plan. Новый существенный Q всегда имеет lineage и проверяемую неизвестную.

В `BANK_TO_PLAN` `generation_performed: NO`: exact supplied bank не заменяется, не дополняется и не переписывается. PREP-3 только строит адресуемые проверки применимости/покрытия для admission. Критический пробел блокирует этот run или требует нового владельцем принятого входа; он не разрешает тихую регенерацию банка.

Double-Iceberg неизвестная становится Q только при материальном влиянии, finite distinguishing route, отсутствии accepted equivalent, exact origin view/member/edge и учёте global cap. В GOAL это часть десяти initial sets и единственного bounded expansion. В BANK supplied count сохраняется; после admission допустим только bounded `DERIVED_Q`, а не второй банк.

## PREP-4 — десять checks и admission

`input/prep-admission.yaml` содержит ровно десять строк `source_checks` в следующем порядке. Каждая строка имеет `check_id`, `outcome`, конкретный `basis`, разрешимые `evidence_refs` и `targeted_repair_assignments`. Outcomes: `PASS|NONCRITICAL_GAP_WITH_RATIONALE|CRITICAL_GAP`. Root поставляет предметный outcome и inventories; helper проверяет только поля, hashes, ссылки, полноту и согласованность.

1. `VECTOR_NO_DRIFT` — G, NOT_G, R, scope, authority и ограничения совпадают с утверждённым Вектором.
2. `PERSPECTIVES_DISTINCT_NECESSARY` — все десять profiles адресно сопоставлены по competence, analysis scope, unique contribution, boundary и necessity; косметическое различие или необъяснённое material overlap является critical gap.
3. `EACH_R` — каждое обязательное R имеет исследуемый Q/evidence path.
4. `EACH_MATERIAL_NOT_G` — представлен каждый существенный путь недостижения G.
5. `ALTERNATIVES` — пространство сохраняет принципиально различные достижимые механизмы, а не варианты настройки одного решения.
6. `COMMON_UNPROVED_ASSUMPTIONS` — каждая обнаруженная общая предпосылка связана с admitted researchable Q, resolved accepted E либо critical-gap repair. Если их не найдено, требуется `NONE_WITH_BASIS` с принятыми basis refs; пустой список без основания не проходит.
7. `INTERACTIONS` — представлены существенные межкомпонентные и междисциплинарные взаимодействия/противоречия.
8. `RESEARCHABILITY` — каждый Q задаёт разрешимую неизвестную и различимый evidence/check path.
9. `CHANGEABLE_DECISIONS` — каждый Q может изменить адресуемое архитектурное или исследовательское решение.
10. `CENTRAL_MECHANISM_SUFFICIENCY` — ответы на весь банк дадут необходимые основания выбрать достижимый центральный механизм и выпустить самодостаточный план; оставшиеся неизвестные перечислены.

Consensus, lexical similarity и количество Q не доказывают ни один check. `CRITICAL_GAP` запрещает `ADMITTED` и требует точных непустых repair assignments компетентным существующим ролям. `NONCRITICAL_GAP_WITH_RATIONALE` требует конкретное основание и не может скрывать mandatory critical omission. После targeted repair перепроверь затронутые checks и transitive consumers, не перезапуская весь банк автоматически.

Admission связывает mode, snapshot, goal, Vector/approval или import/applicability, perspective map, initial/expansion results, normalization decisions, exact bank/requirements hashes, question count, `size_assessment`, десять checks, uncovered areas и `generation_performed`. Допустимые size states: `BELOW_TARGET_WITH_RATIONALE|WITHIN_TARGET|ABOVE_TARGET_WITH_RESOURCE_PLAN`.

Один immutable `PREP_ADMISSION` transaction сохраняет exact replay bytes bank/admission, expected current и `admission_next_current`; commit point предшествует установке outputs, current записывается последним. `GOAL_TO_PLAN` создаёт отсутствующий bank. `BANK_TO_PLAN` выбирает `APPROVED_RUN_IMPORT` либо `EXTERNAL_READY_BANK` по [контракту v4.1](contracts-v4.1.md#два-варианта-bank_to_plan), локально нормализует переданный bank и никогда не генерирует второй. После commit существенная смена Vector, approval/import, bank или admission требует нового run.

## Переход в Core

Новый Core assignment, launch record, context, result, registry entry и result transaction несут `lifecycle_scope: CORE` и exact `prep_admission_sha256`. PREP assignment использует `lifecycle_scope: PREP` и один из `PREP-1_VECTOR|PREP-2_PERSPECTIVES|PREP-3_EXPAND_NORMALIZE|PREP-4_ADMIT`. Пропуск новых полей не даёт legacy fallback.

Core получает exact admitted bank и preparation bindings. В `GOAL_TO_PLAN` он переиспользует выбранный Vector и десять принятых blind FRAMING results. В `BANK_TO_PLAN/APPROVED_RUN_IMPORT` он разрешает G/R/NOT_G через immutable foreign import/admission. В `BANK_TO_PLAN/EXTERNAL_READY_BANK` он связывает доступные G/R/materials и сохраняет реально отсутствующее owner decision/authority открытым, не выдумывая foreign approval. Оба варианта используют current `BANK_APPLICABILITY` как доказательство роли `OP-VECTOR`. Core использует точные принятые blind PREP-2 FRAMING и получает blind CORE FRAMING для остальных P, без генерации Q; blind material readset следует тому же exact `assignment.allowed_reads` contract, default none. Ни один режим не просит второй prompt, не повторяет принятую работу и не создаёт новый обязательный банк.

Подготовительная структурная проверка не доказывает качество банка, истинность semantic outcomes, владельца approval или работу реальных агентов. Поведенческие сценарии остаются `NOT_RUN`, пока exact outputs/readsets/actions не оценены независимым проверяющим.
