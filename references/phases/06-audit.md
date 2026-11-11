# Фаза 6. Независимый разрушительный аудит

`FINAL-AUDITOR` получает замороженный **pre-audit core** и внешний exact checksum manifest, цель/R, exact original+normalized bank, positions/answers/E, сильные возражения, spec и ограничения. Pre-audit core исключает `FINAL_AUDIT`, `GATE-6` и `release.yaml`; до аудита они не являются частью проверяемого manifest. Аудитор не писал план, последнюю нормативную правку или проверочную процедуру, на которой основан итог. Он создаёт только новую immutable revision `audit/final/<result_id>.yaml`, собственные E и accepted result `artifact_type: FINAL_AUDIT` с `supersedes`, того же role/agent/assignment/snapshot/core hash.

Аудитор пытается разрушить обоснованность решения: ищет конкретный сценарий недостижения G при формально выполненном плане, контрпример обязательному свойству, ошибку композиции и существенно более сильную реализуемую альтернативу. Он отдельно проверяет центральный механизм, выразительность/функциональную полноту, достижимость входов/алгоритмов/переходов и exact bytes после последней правки. Предыдущие голоса и expert conclusions — не E.

## Восемь обязательных областей

Для каждой сохрани отдельный `PASS|FAIL|NOT_VERIFIED` и адресуемый `area_details`: все применимые ID, exact paths/hashes, выполненную procedure, конкретные observations/findings, проверенный counterexample/alternative result, limitations, связанные defect/open-condition IDs и readiness impact. Метка области без содержательного finding недействительна:

1. **input_goal:** original bytes, normalized bank, G/NOT_G, все R, recipient/scope/horizon, две оси границ, authority и false success.
2. **bank_completeness:** текущий исход каждого original Q; merge/split/derived trace; counts open/critical; accepted answers и адресные OPEN пробелы сохраняют весь банк; 10 P сохраняют собственную работу и peer checks.
3. **grounds_causality:** knowledge/workflow разделены; primary reproducible E; mechanism/time/alternatives; data/exposure/circularity; MCP claims имеют фактические evidence.
4. **critique_operators:** self-refutation, cross reviews, subject/system coverage каждого critical D, шесть операторов, исход каждого critical objection.
5. **architecture:** preregistered A/B/C protocol, честная область вариантов, chosen mechanism/contracts/reverse и positive end-to-end path; поиск более сильной практичной альтернативы.
6. **plan_graph:** S-000, восемь секций всех S, local T DAG/branches/guards/writes/resources, полный граф, R/data/time/authority/errors/recovery.
7. **failure_acceptance:** каждый существенный NOT_G имеет outcome; C различает product failure/evidence gap/environment error; negative/boundary/sensitivity/recovery/regression и путь к G.
8. **version_readiness:** exact core/manifest, accepted audit provenance/independence, last fix rechecked; UNKNOWN/blockers согласованы с verdict; package/runtime/quality/plan/implementation/effect readiness разделены.

Проверяются все известные Q/R/D, не выборка. Каждый `FAIL/NOT_VERIFIED` порождает defect либо open condition с адресом и влиянием на verdict; каждый defect/open condition обратно связан хотя бы с одной областью. `NOT_VERIFIED` не является PASS и в обязательной/критической области запрещает READY. Нельзя утверждать покрытие неизвестных возможных дефектов.

Для blocker укажи нарушенное R/условие, counterexample или primary E, file/T, minimal repair, regression и G impact. Гипотеза дефекта требует различающего test; preference не blocker.

Для `ARCHITECTURE_GRAPH_V1` audit связывает тот же exact `architecture_ref`, который выбран текущим logical S-000. В областях 5–7 выполни [архитектурные destructive probes](../architecture-graph.md): независимо восстанови путь от реальных/планируемых источников до G и обязательные prerequisites назад, разорви переход между формально завершёнными модулями, проверь потерянные R, producer/contract/availability, execution/evidence cycles и законную research feedback. Для существующего проекта проверь normative↔code mapping по exact файлам, интерфейсам/config и integration E; каждый drift имеет ID, адрес, evidence и action. Формальный graph PASS не закрывает premises, центральный алгоритм или функциональный/научный/экономический эффект. Сохрани конкретный counterexample либо предел проверки в существующей подходящей area, без девятой области.

## Исправление и повторная проверка

Исправляет author соответствующего YAML, не auditor. Создай новую revision/core/manifest. Auditor проверяет exact changed bytes и транзитивные области; неизменённое E переиспользуется после проверки применимости. Если auditor изменил проверяемое решение/procedure, эту область принимает другой компетентный неавтор. Максимум два содержательных раунда: первичный и recheck. Последняя нормативная правка без nonauthor check не принята.

## Вердикты

Выбери старшую применимую причину и перечисли остальные. Каждое open condition использует exact typed поля из [контракта v4.1](../contracts-v4.1.md#условия-и-вердикт-аудита). Вердикт вычисляется согласованно с area statuses, defects, `unresolved_critical`, open conditions и readiness; текстовый `verdict` не может их переопределить:

- `BLOCKED`: отсутствует обязательный внешний вход/authority, действует непреодолимое противоречие/запрет или нет допустимого next path.
- `REPAIR_REQUIRED`: исправимый defect плана/контракта/check; last fix не проверен; нет обязательной независимости или обязательной/критической области.
- `RESEARCH_REQUIRED`: пакет структурно согласован, но central mechanism/critical premise UNKNOWN; задан finite early research path.
- `READY`: exact immutable plan независимо прошёл все восемь областей, defects и blocking open conditions пусты, central mechanism обоснован; `readiness.package` и `readiness.plan` равны `READY`. Допустимы только обоснованные `RESIDUAL_CONDITION|FUTURE_CHECK`, ограничивающие собственные dependents; обычные future C могут быть NOT_RUN при определённых mechanism/check.

Приоритет: `BLOCKED > REPAIR_REQUIRED > RESEARCH_REQUIRED > READY`. Неполное critical review даёт минимум REPAIR_REQUIRED; central UNKNOWN — RESEARCH_REQUIRED, если нет более сильной причины. Без независимого auditor итог не лучше REPAIR_REQUIRED/SELF_REVIEW_ONLY.

## GATE-6 и выпуск

`FINAL_AUDIT` — административный итоговый review выпуска, а не содержательное `D`: его нельзя включать в квоты P peer review, subject/system coverage критических D или выдавать за endpoint substantive D graph. Это разделение не отменяет review самого аудита. Перед GATE-6 другой компетентный неавтор с принятым pre-exposure framing принимает отдельный `REVIEW` exact `FINAL_AUDIT` result/path/hash, связывает blind input ID/hash и проверяет provenance, восемь областей, last-fix и согласованность verdict/readiness отдельной процедурой. Именно этот актуальный review является `content_review` GATE-6; автор audit, автор последней правки, ghost endpoint и stale review его не заменяют. Такая проверка GATE-6 не засчитывается как subject/system review какого-либо D.

Публичная последовательность строго нециклична:

1. `prepare` валидирует и фиксирует pre-audit core без `FINAL_AUDIT`, `GATE-6` и `release.yaml`.
2. `audit/final/<result_id>.yaml` проходит `schemas/audit.yaml`, принимается как exact `FINAL_AUDIT`, связывает этот core manifest hash и становится `current_final_audit` только после transactional selector update.
3. `gates/GATE-6/<audit-result-id>.yaml` принимается после audit и связывает exact selected audit ID/path/hash, восемь областей, remarks и last-fix check.
4. `finalize` повторно проверяет неизменность pre-audit core, accepted audit и GATE-6 и связывает все три в `release.yaml`; `verify` повторяет эту проверку, включая immutable input bytes.

GATE-6 означает полноту/принятие аудита, а не обязательно READY: проверенный выпуск может иметь другой честный verdict. Audit задаёт `readiness.package/runtime/quality/plan/implementation/effect`, каждую как `READY|NOT_READY|UNVERIFIED|BLOCKED`; coordinator переносит эту точную mapping в `release.yaml` после принятия аудита. Placeholder/stale audit, пустые findings, open critical condition или рассогласованный verdict отклоняются. Один result или structural PASS не заменяет аудит.

## Independent GitHub challenge

Для central claim с существенным техническим неизвестным, к которому применим GitHub-протокол, Architecture area 5 обязательно включает собственный finite [GitHub search](../github.md) FINAL-AUDITOR за пределами synthesis shortlist, если capability фактически доступна. Grounds/version areas проверяют origin/circularity, exact commit/path/tests/CI receipts, current version, license and transfer E. Более сильный feasible mechanism создаёт адресное finding и targeted recheck, а не автоматический полный restart. `GITHUB_UNAVAILABLE` фиксируется с exact capability/error и блокирует READY только там, где внешний challenge обязателен для central claim; отсутствие child-call evidence не может быть объявлено выполненным поиском.

## Double-Iceberg destructive probes

FINAL-AUDITOR binds exact current heads обоих views и в существующих восьми areas независимо ищет missing/shared assumption, false necessity or causality, impossible capability, uncovered failure combination и stronger bypass. Он проверяет origin seeds, exact E and causal confirmation, view lineage и plan trace. Finding создаёт scoped defect/open condition и targeted recheck; новая девятая область и автоматический полный restart не вводятся.

## Проверка действительного объёма

Для запуска с бюджетом Q перехэшируй prefix журнала из core manifest. Отдельно восстанови канонические начатые и закрытые Q, резервы, UNKNOWN, подтверждённую отмену до старта, abort после старта, повторные попытки, эквивалентные aliases и производные Q. Для факта начала нужна наблюдаемая содержательная работа. Для закрытия проверяй exact answer, контекст, применённую методологию, глубину и независимый review. Worked OPEN сохраняет адресный оставшийся пробел. Uninvestigated OPEN, alias, ACK и повторная попытка не добавляют единицу.

Проверь сохранность исходной цели и dispositions всех исходных и производных Q, включая явные нерасследованные пробелы. Разреши адресные зависимости советников и текущие независимые dispositions. Для сопоставимого FAST/DETAILED нужны одинаковые регламент, роли, глубина, источники и полномочия; отдельно укажи расхождения, недоступные наблюдения и влияние пробелов на verdict. Сопоставление счётчиков и локальные фикстуры подтверждают только проверенный структурный путь.
