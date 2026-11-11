# Фаза 5. Самодостаточный машинный план

`PLAN-AUTHOR` получает принятый синтез, R/D/E, NOT_G, открытые условия и snapshot. Он пишет только новые immutable PLAN files в `runs/<run_id>/plan/`; координатор принимает result и выпускает комплект выбранной версии. План не переоткрывает выбранную архитектуру и не оставляет центральный выбор исполнителю.

Если центральный путь ещё зависит от исследования, полностью определи ранний конечный research stage, независимые части и guarded branches. Неизвестная продуктовая ветвь остаётся `RESEARCH_REQUIRED`; не заполняй её предположением.

Для `ARCHITECTURE_GRAPH_V1` следуй [архитектурному контракту](../architecture-graph.md). Текущий logical S-000 выбирает canonical accepted SYNTHESIS через `architecture_ref` с exact `synthesis_id`, `synthesis_sha256`, `graph_sha256`; каждая текущая S повторяет эту binding. План не хранит вторую копию графа. Каждая необходимая capability/component/algorithm имеет определённые T, DATA contracts и C в выбранной версии; будущие S/T/C IDs синтеза разрешаются только в ней. Если это невозможно без изменения архитектуры, верни адресный defect автору SYNTHESIS и получи новую revision.

## Файлы

- logical `S-000` — реальный принимаемый этап допуска в одном YAML выбранного PLAN result;
- каждый logical `S-NNN` — один принимаемый этап в одном YAML той же выбранной версии;
- E и ответы остаются в `evidence/` и `answers/`, а план ссылается на их устойчивые ID/path/hash.

Один `plan_version` указан во всех S. Logical stage ID стабилен, но accepted repair получает новый result ID и отдельный immutable path; прежние bytes не перезаписываются. Только `current_plan_projection` выбирает одну текущую result/path/hash запись для каждого logical stage и один plan version; остальные accepted revisions остаются provenance. Общая нормативная инструкция определяется однажды и адресуется по ID. Не создавай плановый монолит, Markdown-норму или копии ответов/аудита. Derived Markdown строится только из YAML. При недоступной записи результат `NOT_SAVED` не проходит gate.

## S-000

`S-000` не фиктивная подготовка. Он проверяет применимость плана к фактическому состоянию, точные версии/полномочия/ресурсы, входы первого действия и актуальность E. Исходы: `ACCEPTED` с допустимыми первыми S; `BLOCKED` с отсутствующим условием; `NEEDS_RECHECK` при drift.

S-000 содержит цель/snapshot/plan_version/authority; карту `S→path→result→predecessors→entry→exit→next`; трассу `R→D→S/T или accepted reuse→C→E/NOT_RUN→condition`; граф данных/артефактов; правила запуска/resume/drift; открытые критические Q, ресурс и первый допустимый T.

## Восемь секций каждого S

1. **identity_result:** S-ID, plan_version, owner, связанные R/D, один принимаемый результат.
2. **local_execution_graph:** predecessors/entry/state/first T; все T и edges; branches/guards; parallel waves; writes/resources/conflicts; exit/next. Каждый edge ссылается на существующие локальные endpoints, каждый T принадлежит ровно одному S. Граф ацикличен; повторение моделируется отдельной bounded iteration с start/step/bound/stop/failure action, а не self-cycle.
3. **inputs_contracts:** exact typed `EXISTING|PLANNED|EXTERNAL` inputs и outputs по [контракту v4.1](../contracts-v4.1.md#входы-и-выходы-плана). Будущий result не получает выдуманный hash; task/check inputs разрешаются только через input/output contract.
4. **tasks:** полные T-контракты с алгоритмами/параметрами/ветвями, влияющими на архитектуру или приёмку.
5. **outputs_handoff:** artifacts/states, path/ID/contract, consumers, allowed next S и forbidden transitions.
6. **acceptance_exit:** C, наблюдаемые критерии, компетентный неавтор, positive/negative/boundary/sensitivity и защита от ложного успеха.
7. **resources_stop:** конечные time/compute/data/money, основание, monitoring, stop/failure action.
8. **errors_recovery_evidence:** retry/idempotency/compensation/checkpoint; product failure/evidence gap/environment error; existing E отдельно от future `NOT_RUN`.

Используй точные keys из `schemas/plan-stage.yaml`. Нормативный контракт требует восемь **именованных** непустых секций, общий S DAG и local T DAG, разрешённые endpoints, branches/guards, bounded iterations, ownership, writes/conflicts и resources. До независимой негативной пробы не приписывай локальному validator полное enforcement этого контракта; даже полный structural PASS не доказывает предметную достаточность.

## Контракт T

Каждая T создаёт один проверяемый результат и содержит: ID/result; вклад в R/D/G; predecessors/guards; точные inputs/producers/contracts; owner/write scope/authority; конечную procedure/algorithm/accepted parameters; outputs/consumer; C/checker; resource/stop; errors/recovery; E либо future protocol. Все R/D/E refs должны разрешаться в immutable requirements/accepted registry того же snapshot. Каждый output имеет одного writer либо явную сериализацию; параллельные T не пишут один path/resource без неё. Связанные действия одного атома можно объединить; независимо принимаемые результаты разделяй. Не прячь архитектуру в «реализовать систему».

Research T дополнительно задаёт Q, control, finite search bound и три outcomes (`positive/negative/uncertain`) с критерием и next action. Отрицательный опыт закрывает исследовательскую T, но не продуктовое R. «Выбрать лучший», «оптимизировать», «исследовать» неполны без метода, границ и различимого выхода.

## Контракт C

Каждая C задаёт уникальный ID, R, существующий T, owner S, exact inputs, preconditions, environment, получение checker, command/finite procedure, expected observation, decision rule, negative/boundary/sensitivity. C входит в accepted plan entity и адресуется typed graph; ссылка на отсутствующий C запрещена. Formal C указывает утверждение/предпосылки/proof check; expert C — заранее заданную rubric и компетентного неавтора. Различай product failure, insufficient E и environment error.

Выполненная C сохраняет run/correlation, при наличии workflow/node, plan/S/T/C, path+version+hash входов и средства, actual procedure, resource/duration, observation/code, E, limitations и checker. Будущая C — `NOT_RUN`, без выдуманного E.

## Граф и исполнение

Проверь полный S/T graph, данные, состояния, ресурсы, authority, checkers и integration: preconditions → accepted producer → operation → output contract → acceptance → consumer. Пройди получение всех условий до G и обратно. Номер файла не задаёт порядок. Parallel T имеют совместимый общий ресурс и disjoint writes либо явную сериализацию.

Повтори архитектурную достижимость на exact текущих plan entities: связность двух готовых модулей не закрывает отсутствующий DATA contract, producer, преобразование формата или принимающую C. До GATE-5 нет unresolved обязательных S/T/C refs, потерянного R или required capability без реализации. Отличай достижимый будущий путь от фактически выполненного: PLANNED T/C не становятся implementation/effect READY.

Поле `execution_status` для S/T использует `NOT_STARTED|RUNNING|ACCEPTED|FAILED|BLOCKED|NEEDS_RECHECK`; это не общее `workflow_status` YAML-result. `NOT_APPLICABLE` допустим только для невыбранной ветви по выполненному guard и не создаёт outputs. При drift останови consumers и перепроверь транзитивные зависимости. Для существующего проекта сопоставь normative graph с exact code/interfaces/config/contracts/integration E; imports/calls одни не подтверждают функциональный путь. При изменении выдели affected nodes/checks и их consumers, сохрани неизменённые применимые результаты; полный restart требует отдельного основания.

Для каждого существенного NOT_G задай outcome: опровергнут в области; устранён с E; закрывается T/C; остаётся ограниченным условием; принят владельцем как residual risk; блокирует результат. Одновременно проверь отрицательные случаи и положительный путь.

Frozen requirements из `input/snapshot.yaml` задают знаменатель покрытия плана. Для каждого R существует ровно одна текущая адресная строка ledger `R→D→S/T или accepted reuse→C→E|NOT_RUN→condition`; все endpoints разрешаются в exact snapshot/registry/plan. Общая метка `DONE`, один найденный дефект, покрытие соседнего R или агрегированный процент не закрывают отсутствующую строку. До GATE-5 перечисли exact missing/duplicate/stale R и не принимай план с потерянным обязательством.

## Будущее исполнение

1. Разрешить выбранный logical `S-000` через `current_plan_projection`, затем прочитать его exact path/hash, selected `current_final_audit` path/hash, `release.yaml` и фактические полномочия; READY плана не запускает внешнее действие.
2. Выбрать S только с accepted predecessors и выполненным entry.
3. Выполнить T, затем C/exit; отсутствие E, ошибка проверки или false success не дают ACCEPTED.
4. Сохранить run/correlation, plan/S/T/C, stage path/hash, snapshot, outputs, E, checker, resources/limits, outcome и allowed next S.
5. При resume читать фактическое execution state/E; accepted применимое не повторять, незавершённое продолжать по recovery; drift переводит consumers в NEEDS_RECHECK.

## GATE-5

PASS, если отдельный `PLAN-AUTHOR` создал и координатор принял result `artifact_type: PLAN`, а `current_plan_projection` выбирает exact logical S-000 и все S одной версии; восемь именованных секций каждого S содержательны; local и общий графы достижимы и свободны от необъявленных циклов; T ownership уникален; branches/guards/bounded iterations/writes/resources определены; write conflicts отсутствуют или сериализованы; все R/D/E/T/C refs разрешены; все R/NOT_G имеют путь; central choice закрыт; E отделено от NOT_RUN. Сохрани outcome в `gates/GATE-5.yaml` по gate contract. Structural PASS не доказывает реализацию или эффект.

## External reference in PLAN

Когда D использует C, план связывает exact `EXTERNAL_IMPLEMENTATION` E и различает: реализуемый mechanism, reference existence, official D contract, собственное target design и отдельную transfer/applicability C. T содержит конкретный algorithm/contracts, а не поручение заново читать repository. Version/license/current-limit неизвестность остаётся condition либо early research T; future transfer check — `NOT_RUN`, без фиктивного E. Детали — в [GitHub-протоколе](../github.md).

## Double-Iceberg trace

Каждый material stage содержит exact trace `G/R → capability или material unknown → Q/E → D → S/T/C → useful result` и binds те же два view heads, что accepted SYNTHESIS. Реализатор получает выбранный механизм и проверки, а не задание заново построить карты. Central unresolved member/edge создаёт раннюю research T/open condition и не допускает READY.
