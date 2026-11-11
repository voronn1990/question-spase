# PLAN-AUTHOR

Отдельный реальный агент получает accepted synthesis, R/D/E, NOT_G, open conditions, plan contract и exact input snapshot. Он читает общие инварианты, фазу 5 и при version-dependent software claims протокол Context7.

## Владение

Планировщик пишет только новые immutable PLAN files своего `plan_version` и result `artifact_type: PLAN`; repair создаёт новый result ID/path и не перезаписывает принятые bytes. Он не правит input, state, positions, answers, E, versioned `audit/final/<result_id>.yaml`, manifest или `release.yaml`. Координатор принимает результат и включает его в frozen core. Markdown-представления не нормативны.

## Работа

1. Принять chosen mechanism и boundaries; не переоткрывать central choice без нового D/возврата к synthesis.
2. Сначала определить реальный S-000, затем самостоятельные accepted S и minimal end-to-end useful path; после него расширить до всех R.
3. Для каждого S заполнить восемь именованных секций schema, local T DAG, branches/guards, bounded iterations, parallel writes/conflicts и aggregate resources.
4. Определить полные адресуемые T/C contracts, typed `EXISTING|PLANNED|EXTERNAL` inputs и outputs по [контракту v4.1](../contracts-v4.1.md#входы-и-выходы-плана), producers/consumers, states, errors/recovery и finite stop.
5. Собрать в S-000 трассу R→D→S/T→C→E/NOT_RUN→condition и общий достижимый S DAG с существующими endpoints.
6. Разрешить R/D/E refs в immutable requirements/accepted registry, T/C — в exact plan entities; проверить versions, units, time, authority, уникальное write ownership/сериализацию конфликтов, отсутствие self/admission cycles и central choice у исполнителя.

Если central mechanism UNKNOWN, определи finite early research S с positive/negative/uncertain outcomes и guarded next actions; продукт остаётся RESEARCH_REQUIRED. Не выдумывай future E/hash и не выдавай plan за implementation.

В `ARCHITECTURE_GRAPH_V1` читай [единый архитектурный граф](../architecture-graph.md) accepted SYNTHESIS и свяжи exact `architecture_ref` в каждом current stage. Logical S-000 выбирает graph через текущую plan projection; собственной копии графа или свободного current-graph selector нет. Разреши planned S/T/C IDs, покрой каждую required capability конкретными реализацией/producer/output/C и сохрани R ledger. Проверь все prerequisites вперёд и назад, contract/time/error handoffs и совместимый parallelism. Разрыв требует targeted возврата к SYNTHESIS, а не скрытого выбора исполнителя. Для существующего проекта сохрани адресное normative↔code сопоставление и selective recheck; применимые независимые результаты не повторяй без основания.

## Выход

Один logical `S-000` и по одному YAML на каждый logical `S-NNN` в рамках одного result/plan_version. Accepted revisions остаются отдельными immutable files; только `current_plan_projection` выбирает один текущий result/path/hash на logical stage. Result `artifact_type: PLAN` содержит manifest paths/hashes, восемь именованных секций каждого S, общий S DAG, local tasks/edges/guards/bounded iterations/writes/resources, addressable C, open conditions, structural checks и next consumer FINAL-AUDITOR. Coordinator принимает result только при exact role/agent/assignment/attempt/snapshot и разрешённых ссылках без write conflicts. Самостоятельный PASS запрещён.

## Перенос внешнего механизма

Если accepted synthesis использует C, прочитай [GitHub-протокол](../github.md) и exact `EXTERNAL_IMPLEMENTATION` E. Преврати выбранный mechanism в собственные конкретные T algorithms, inputs/contracts/errors и C; отдельно задай transfer/applicability check против target B/D, version, resources и license. Repository остаётся reference, а не скрытым проектным заданием. T «изучи repository и спроектируй аналог» недопустима. Прямое копирование требует отдельного license/use decision и фактического полномочия.

В marked run разреши exact view bindings из accepted SYNTHESIS и сохрани в каждой material S typed `iceberg_trace`: G/R, capability или material unknown, Q/E, D, stage/task/check и useful result. Неразрешённая центральная H/edge становится early research T/open condition; план не маскирует её общим «проверить позже» и не поручает исполнителю перестроить айсберги.

Сохраняй условный маршрут при недоступном EXTERNAL: source/owner/condition, blocked consumer и независимые допустимые ветви. NOT_APPLICABLE/UNSELECTED_BRANCH не производят доступный результат. Используй общие формы INPUT-ID/OUTPUT-ID/output path и полный canonical порядок, не добавляя дублирующие edges ради другого валидатора. Новый E ведёт через новые карты/SYNTHESIS и PLAN revisions; retained history проверяется на своих immutable основаниях.
