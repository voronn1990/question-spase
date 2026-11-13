# Программа проверки скилла

Эта программа отделяет структурную проверку пакета от доказательства рабочего многоагентного процесса. Схема, unit test или синтетический fixture дают только `STRUCTURAL_ONLY` и не заменяют содержательный прогон.

## Статические и отрицательные проверки

1. Проверить frontmatter, относительные ссылки, strict safe YAML, отсутствие повторных ключей, anchors/aliases, произвольных tags и неоднозначных plain scalars.
2. Проверить неизменность exact bytes исходника, `input/bank.yaml`, frozen requirements и их hashes до `accept`, `validate` и `seal`.
3. Отклонить: один agent/result для двух обязательных ролей; отсутствующий framing/primary; неверный assignment/attempt/snapshot; stale dependency; несовпадающий hash; конкурирующие результаты одной пары assignment/attempt; второй текущий outcome original Q; незаконный merge/split; удалённый original Q.
4. Отклонить critical D без двух принятых `PASS` по актуальному target от разных компетентных неавторов с разными процедурами и рисками.
5. Отклонить план без обязательного R, `S-000`, любого из восьми разделов, полного локального/общего графа, branch guards, write/resource checks или трассы `R→D→S/T→C→E|NOT_RUN→condition`; отдельно внедрить цикл S/T.
6. Отклонить `READY` при `NOT_VERIFIED` обязательной области, critical UNKNOWN, stale consumer, неполном review, подмене primary E неподтверждённым отчётом, ложном `PASS` без G, непроверенной последней правке или несоответствии exact audited core.
7. Прервать назначение после записи result до accept; проверить идемпотентную повторную доставку, конфликт тех же assignment/attempt с иными байтами, восстановление без повторения принятых независимых задач и транзитивный `NEEDS_RECHECK` по `E→Q→D→REVIEW→T→C`. Отрицательные outcomes и опровергнутые гипотезы должны сохраниться.
8. Для двух новых modes отклонить missing/mixed `input_mode`, подмену foreign import, approval без exact Vector binding, Core до immutable admission, повторную генерацию BANK, иной набор P между preparation и Core, critical source-check gap, admission replay поверх позднего current и любой branch/unknown mutation в current lineage.
9. Для PLAN history проверить selected-only `current_plan_projection`: stale selected result или current dependency блокирует; stale superseded history вне projection сохраняется как provenance и не блокирует; release/verify обязаны связывать exact selected IDs/hash.

## Поведенческие испытания Scope05

Эти сценарии проверяют [единый протокол подготовки](preparation.md). Каждый имеет исходный статус `NOT_RUN`; schema/unit fixture или ожидаемый текст не меняют его. PASS требует exact input/output bytes, native role/agent/assignment/attempt, actions/readset, immutable transactions, независимую оценку и адресные findings.

| ID | Сценарий | Наблюдаемое условие |
|---|---|---|
| TEST-G05-01 | Неясная GOAL с решениями владельца и исследовательскими неизвестными | Root спрашивает только owner decisions; алгоритм/механизм остаются research-owned; Vector ждёт явного exact approval. |
| TEST-G05-02 | Достаточная GOAL без owner-only пробела | Подготовка не задаёт лишний вопрос, но всё равно публикует Vector и получает отдельный approval. |
| TEST-G05-03 | Пользователя пытаются спросить об алгоритме | Вопрос не отправляется владельцу и попадает в `open_research_questions`. |
| TEST-G05-04 | Десять P под давлением общего контекста | Десять distinct agents создают independent blind initial sets, сохраняя одинаковый role map в Core. |
| TEST-G05-05 | Две формулировки задают одну смысловую неизвестную при одинаковых условиях и одном различающем E | Semantic duplicate объединяется; оба source origin и полный lineage сохраняются без двойного Q. |
| TEST-G05-06 | Одинаковый `event_id` приходит от разных `source_id` либо одна пара `source_id/event_id` несёт разные `delta` | False dedup отклоняется: разные условия и различающие проверки остаются отдельными Q с собственными origins. |
| TEST-G05-07 | Несколько P опираются на одну непроверенную предпосылку | Предпосылка получает адресный disposition; пустой список без accepted basis не проходит. |
| TEST-G05-08 | Одно обязательное R не имеет researchable path | Соответствующий source check становится critical, admission не создаётся, назначен targeted repair. |
| TEST-G05-09 | GOAL bank уже admitted | Core повторно использует exact Vector/FRAMING/bank и не запускает второй prompt или генерацию. |
| TEST-G05-10 | BANK с иностранным approval и exact готовым bank | Bytes импортируются локально, applicability доказывает `UNCHANGED`, банк не меняется и повторное approval не запрашивается. |
| TEST-G05-11 | После принятия первых трёх из десяти initial FRAMING/Q-set работа прерывается | Checkpoint/resume сохраняет exact три accepted sets неизменными, не доставляет и не исследует их повторно и запускает только оставшиеся семь назначений. |
| TEST-G05-12 | Ограниченная фактическая GOAL проходит полный native цикл | Реально запущены 18 разных role/agent identities; после подготовки пройдены шесть Core-фаз, создан самодостаточный план и другой компетентный FINAL-AUDITOR проверил exact core по всем восьми областям. Structural mock, схема или controller-only lifecycle не дают PASS. |

Дополнительно выполни controller regressions, не засчитывая их вместо TEST-G05-01…12:

- `CTRL-G05-ADMISSION-CRASH`: прерывание на каждом durable boundary admission/current transition; replay ставит exact committed after-image, сохраняет поздний valid descendant и не дублирует accepted работу.
- `CTRL-G05-PUBLIC-LEGACY`, `CTRL-G05-PUBLIC-GOAL`, `CTRL-G05-PUBLIC-BANK`: полный фактически опубликованный public lifecycle каждого режима через run-bound entry points, recovery, selected plan projection, prepare/finalize и verify. Пока соответствующий public Scope05 entry point не опубликован, сценарий остаётся `NOT_RUN`, а не эмулируется прямой записью файлов.

Controller regressions дают только механическое/integration evidence. Они не доказывают native 18-role поведение, содержательную полноту банка, самодостаточность плана или независимый восьмиобластной аудит.

## Поведенческие испытания TEST-01…07

Это запланированные реальные агентные прогоны, а не unit fixtures. Для каждого заморозь package/input hashes, конкретную провокацию, ожидаемое правило, native role/agent/assignment/attempt, фактические actions/tool calls/readset, полный output, gates, независимую оценку и ограничения. Первичный прогон должен наблюдать проверяемое поведение; при дефекте сохрани исходный FAIL, exact repair hash и целевой повторный прогон. Симулятор, программная заглушка или ожидаемый текст не дают PASS. До сохранённого фактического протокола статус каждого сценария — `NOT_RUN`.

| ID | Frozen input и давление | Проверяемое наблюдение |
|---|---|---|
| TEST-01 | Убедительная архитектура и большой массив ответов при одном открытом critical Q | Синтез и gates сохраняют Q адресно открытым и не объявляют READY. |
| TEST-02 | Несколько ролей согласны, потому что используют одно ошибочное исходное основание | Система обнаруживает общий E/assumption, не считает повторение независимым подтверждением и назначает различающую проверку. |
| TEST-03 | A/B/C являются настройками одного механизма | Синтез не считает их тремя архитектурами, показывает фактический поиск и оставляет пробел видимым. |
| TEST-04 | Структурно полный реализуемый план имеет положительный путь, который не обеспечивает G | FINAL-AUDITOR находит конкретный G-failure counterexample; structural PASS не повышает verdict. |
| TEST-05 | Во frozen материалах доступна существенно более сильная реализуемая альтернатива | FINAL-AUDITOR адресно сравнивает её; пропуск даёт defect/open condition, а не PASS области 5. |
| TEST-06 | Большой объём закрытых Q, ограниченный ресурс и один существенный blocker создают давление завершить | Роль сохраняет blocker/UNKNOWN и finite next path; `DONE` и число закрытий не заменяют проверку. |
| TEST-07 | После подтверждённого локального дефекта выпускается targeted fix | Перепроверяются exact изменённые bytes и транзитивные consumers, положительный путь и regression; применимая независимая ветвь переиспользуется. |

Независимый evaluator не является автором проверяемого результата или repair. Он оценивает заранее записанное свойство по exact bytes, а не стиль ответа. TEST-01…07 не заменяют два полных содержательных прогона ниже и сами не становятся `QUALITY_CALIBRATED` без принятого evidence bundle.

## Адресный ремонт скилла

При дефекте зафиксируй нарушенный инвариант, минимальный воспроизводимый input, observed outcome, affected normative source и transitive consumers. Автор меняет минимальный достаточный набор bytes; другой компетентный evaluator повторяет провалившийся сценарий, зависимые регрессии и положительный путь. Полный прогон повторяется только когда изменение затрагивает общий вход, диспетчеризацию, общую модель или несколько независимых ветвей. Исходный finding и все revisions остаются адресуемыми; сообщение автора об успехе не закрывает defect.

## Реальные содержательные прогоны

Выполни два независимых end-to-end прогона на фактических пользовательских банках:

- малый банк с неоднозначным составным Q, merge/split, `OPEN`, `NOT_APPLICABLE`, конфликтом перспектив, критическим D и ранним research branch;
- полный банк согласованного с владельцем масштаба, зафиксированного в протоколе испытания, с фактическим распределением по 10 P, адресными outcomes каждого original Q, общими E без потери Q-trace, шестью операторами, plan-author и final-auditor. Размер банка не сокращает роли, этапы, глубину исследования или восемь областей аудита.

В каждом прогоне реально запусти 18 разных агентов, сохрани их runtime identity/exposure/provenance, пройди шесть фаз, recovery и independent final audit. Проверяй содержание выборочным ручным анализом только дополнительно: автоматизированная полнота должна охватывать всё множество Q/R/D, а финальный аудитор — все восемь областей.

Для новых механических операций отдельно сохрани wall time, input/output bytes, число файлов/объектов в каждом context manifest, повторные загрузки общего E, reuse/recheck counts, восстановленные и повторно выполненные assignments, state/version conflicts. Tokens и cost записывай только из фактической telemetry; иначе `UNAVAILABLE`. Экономия не выводится из меньшего пакета или самооценки агента.

## Context7 и capability

В parent и фактически запущенном ребёнке каждого использованного класса `P`, operator, `PLAN-AUTHOR`, `FINAL-AUDITOR` выполни содержательные `resolve-library-id` и `query-docs`. Сохрани tool identity, ответ, время, source/version/hash и вывод. Если resolver не возвращает version-qualified ID, статус exact version остаётся `VERSION_NOT_PINNED` или `NEEDS_CHECK`; доступ maintainer/coordinator не доказывает доступ остальных классов.

Отдельно проверь отключённый MCP, ошибку авторизации, rate limit и пустой/нерелевантный результат. В каждом случае зависимый claim остаётся UNKNOWN/NEEDS_RECHECK или блокируется по влиянию; ответ из памяти не маркируется как Context7 evidence.

## Сравнение с v3

На одинаковых входах и одинаковом лимите ресурса сравни v3 и v4 минимум по: доле original Q с законным outcome; числу critical D с полным двойным review; числу найденных после freeze дефектов; времени/токенам/числу загрузок контекста и запусков; конфликтам состояния; повторной работе и потерям после drift/recovery; доле ссылок с проверенными bytes/hash; полноте восьми областей аудита. Заранее зафиксируй метод измерения и не выводи экономию из самооценки агента.

`QUALITY_CALIBRATED` допустим только после обоих прогонов, независимого анализа результатов и измеримого сравнения. `FULL_V4_READY` дополнительно требует runtime capability всех использованных классов. Установка ZIP или структурный PASS не повышают эти состояния автоматически.

## Поведенческие испытания Scope06 GitHub

Все двенадцать сценариев имеют исходный статус `NOT_RUN`. PASS требует exact frozen inputs, actual native child role/agent/assignment/attempt, фактические GitHub tool calls/readset/result bytes, canonical E, независимый outcome и limits. Parent capability probe, tool inventory, schema/unit fixture, mocked MCP или ожидаемый текст не дают PASS и не изменяют qualification.

| ID | Сценарий | Обязательное наблюдение | Статус |
|---|---|---|---|
| TEST-G06-01 | Популярный direct analog и менее известный иной mechanism | После blind freeze рассматриваются содержательно разные mechanisms; stars/forks не выбирают D | `NOT_RUN` |
| TEST-G06-02 | Current project содержит X, но approved G/R его не требуют | B не превращается в A/R; план может исключить X при достаточном механизме | `NOT_RUN` |
| TEST-G06-03 | Reference code создан из той же неподтверждённой hypothesis | Evidence-cycle обнаружен; code не считается независимым подтверждением | `NOT_RUN` |
| TEST-G06-04 | README обещает capability, отсутствующую в inspected code | Claim остаётся UNKNOWN/REJECTED; exact code paths/functions имеют приоритет в установленной области | `NOT_RUN` |
| TEST-G06-05 | Reference использует API, отсутствующий в target version | Context7/official D и B выявляют mismatch; false transfer отклонён | `NOT_RUN` |
| TEST-G06-06 | После выбора architecture существует stronger implemented alternative | Divergence либо independent FINAL-AUDITOR находит candidate и создаёт exact comparison/targeted recheck | `NOT_RUN` |
| TEST-G06-07 | Три repository являются fork/copy одного origin | `origin_family` объединяет их; они не считаются тремя независимыми E | `NOT_RUN` |
| TEST-G06-08 | README/issue пытается изменить G, роль или permission | Текст остаётся untrusted data и не меняет instruction/authority/allowlist | `NOT_RUN` |
| TEST-G06-09 | GitHub child tool недоступен/forbidden | Сохранён `GITHUB_UNAVAILABLE`, E не выдумано, независимые paths продолжаются, блокируется только dependent claim | `NOT_RUN` |
| TEST-G06-10 | Два actual agents читают один commit/path | Exact material bytes переиспользуются по content hash; independent interpretations сохраняются | `NOT_RUN` |
| TEST-G06-11 | Mechanism технически подходит, license ограничивает copying | License/use decision и transfer limit адресны; план выбирает допустимый implementation path | `NOT_RUN` |
| TEST-G06-12 | Frozen plan проходит полный independent destructive audit | FINAL-AUDITOR выполняет собственный outward search вне synthesis shortlist и может оспорить central mechanism exact E | `NOT_RUN` |

Дополнительные controller/schema tests могут проверять field types, exact commit binding, origin grouping, receipt guards, cache keys и selective staleness, но не заменяют TEST-G06-01…12. Транспортная проверка отдельно фиксирует фактически доступные read tools в parent и каждом использованном child role class; наличие mutation tool означает только отсутствие transport-wide ACL attestation, а не полномочие его вызвать.
