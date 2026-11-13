# Фаза 1. Core intake, Вектор и независимые framing

Новые `GOAL_TO_PLAN` и `BANK_TO_PLAN` входят в Core только после [четырёх стадий подготовки](../preparation.md) и exact `prep_admission_sha256`. Эта фаза потребляет admitted objects; она не согласует Вектор повторно, не генерирует новый банк и не просит пользователя запустить следующий этап. Положительно распознанный `LEGACY_CORE` сохраняет прежний входной путь без Scope05-полей.

## Exact intake

1. Перехэшируй exact snapshot, goal/materials, bank/original source и применимые import/admission bytes. Новый mode без точного admission не маскируй под legacy.
2. Разреши admitted G/NOT_G/R, authority, perspective map, десять source checks, bank/requirements hashes и uncovered areas. Структурный `ADMITTED` не доказывает semantic truth или качество банка.
3. Сформируй Core handoff: mode, snapshot/admission IDs/hashes, получатель, G/NOT_G/R, границы, исходное состояние, ресурсы, полномочия, source conflicts, bank count/size state и ограничения admission.
4. Определи отделённые приёмочные данные до исследования; не раскрывай их создателям гипотез. Зафиксируй provenance, время, exposure и попытки.
5. Создай working routing view без изменения admitted bank: каждый Q сохраняет lineage, G/R/NOT_G refs, unknown, applicability, changeable decision и evidence path.

## Foundational role proofs

В `GOAL_TO_PLAN` выбранный current accepted `OP-VECTOR` и десять admitted current `FRAMING` results являются foundational PREP results. Переиспользуй exact bytes и тот же `P-01…P-10 → agent_id`; не перезапускай Vector или initial questions. Working Q выдаются каждой P только после проверки её accepted blind framing/exposure.

В `BANK_TO_PLAN/APPROVED_RUN_IMPORT` foundational G/R/NOT_G разрешаются из exact immutable imported Vector через import/admission binding. В `BANK_TO_PLAN/EXTERNAL_READY_BANK` они разрешаются из local accepted Vector/approval and external binding. Текущий принятый `OP-VECTOR/BANK_APPLICABILITY` является current role proof, но не новым owner approval. Для каждой P из perspective map переиспользуй exact accepted blind PREP-2 FRAMING с тем же actor либо получи её первый blind Core FRAMING. В свежем пакете доступны applicable approved/local Vector, собственная perspective row и только exact immutable approved brief materials, явно выбранные существующим `assignment.allowed_reads`; default none. Context запрещает automatic all-materials injection, bank source bindings/hashes, foreign P results и preferred mechanism и не дублирует выбранный content. Эти framing results не создают `initial_questions`; после freeze той же P выдаются только назначенные Q готового банка.

В `LEGACY_CORE` используй прежний exact Vector/framing flow только после положительной проверки старого snapshot/bank/original-bank layout. Простое отсутствие `input_mode`, lifecycle или admission fields не создаёт legacy.

## Маршрутизация банка

Root назначает каждому исследуемому admitted Q ровно одного primary author по компетенции, зависимостям, риску ошибки, средствам и ресурсу. Неисследованные admitted Q сохраняют адресный пробел. Semantic merge не удаляет origins; разные applicability или evidence paths остаются отдельными Q. Expansion proposals и superseded PREP repairs не входят в Core model input.

Не дополняй Core bank до количества и не применяй снова goal-mode операции генерации. Новый Q после admission допустим только как адресный derived research obligation с причиной, lineage, изменяемым D, конечной проверкой и правилами revision/stale; он не запускает повторную подготовку автоматически. Изменение G/R/authority, imported approval или admitted bank требует нового run.

## GATE-1

PASS, только если exact inputs, bank, preparation/import/admission bindings и current state совпадают; Core handoff точен; G/NOT_G/R проверяемы; границы и полномочия видимы; все десять P имеют разные фактические `agent_id`, тот же admitted map и принятые protocol-blind framing; Q routing покрывает exact admitted bank без потерь; обязательные admission limitations перенесены. Для GOAL используй admitted PREP FRAMING. Для BANK используй принятый слепой PREP-2 или CORE FRAMING каждой P с точным context hash. Техническую изоляцию утверждай только при отдельном evidence; fresh context/readset/read log доказывают лишь protocol blindness.

GATE-1 не повторяет десять preparation checks и не переутверждает owner approval. Он проверяет точный переход admitted bank в Core. Сохрани outcome в `gates/GATE-1.yaml`; неизвестность блокирует только зависимый claim/path.

Для `DOUBLE_ICEBERG_V1` GATE-1 дополнительно разрешает exact local либо imported `normative_vector_binding` и требует десять accepted own-origin `iceberg_position`. Ни одна P до freeze не читает bank, foreign P results, preferred mechanism или merged view. Imported Vector является G/R anchor; local `BANK_APPLICABILITY` остаётся только проверкой применимости.

## Бюджет исследования вопросов

Для нового обычного запуска Root фиксирует `--effort FAST|DETAILED --half-budget m --question-procedure FILE` при `init` в обоих входных режимах. Файл задаёт сопоставимые `inventory_rule`, `evidence_cutoff`, `tools`, `routing_rules`, `methodology` и `review_and_audit`. Заморозь одно положительное m и одинаковый регламент до содержательной работы. В GOAL сопоставляемая пара также требует одинаковой политики initial Q из immutable snapshot. Исторический и новый контракты дают `UNPAIRED_PROCEDURE`. FAST исследует m канонических Q, DETAILED — 2m. Сохраняй исходные G/NOT_G/R, 18 ролей, обе головы Двойного айсберга и одинаковые глубину Q, инструменты, рецензирование и аудит. Выход из ресурса, нечётный обязательный охват и незавершённая попытка остаются адресными результатами.

После утверждённого Вектора и проверки нормализации зарегистрируй полную начальную опись через `question-register` с текущим независимым REVIEW над EVIDENCE `question_inventory`. В BANK сравни exact исходные байты и все исходные ID. В GOAL свяжи ту же опись с утверждённым Вектором и проверь соответствие всем нормализованным ID на `admit-bank`. Каждая строка содержит ID, область, изменяемое решение, различающий путь доказательства, приоритет и обязательность. Фиксируй отдельно подтверждённые эквивалентные alias и мотивированные исключения. Для найденного в ходе исследования Q перед работой зарегистрируй `derived_question` с исследованным родителем, текущим D и наблюдением, которое меняет решение.

До admission в BANK рецензент описи проходит PREP-2 blind framing с собственным профилем и утверждённым Vector. Для `APPROVED_RUN_IMPORT` укажи точный импортированный ID и digests Vector и approval. Слепой пакет содержит собственный профиль, границы цели и выбранные допустимые материалы. Для рецензии свяжи собственное принятое FRAMING и exact target, затем явно выдай полный target через `context --expand`. После admission используй исходный exact FRAMING этого же рецензента. Новое слепое назначение после выдачи ему исследовательских Q или результатов отклоняется.

Root задаёт `question_work.lead` для основного автора и `question_work.support` для других ролей, работающих по уже начатому Q. Оба списка входят в точное зарегистрированное назначение и ограничены `assigned_ids.questions`. Один основной автор соответствует текущей approved route; другие P сохраняют собственный полный PRIMARY и peer review на тех же вопросах. После `claim-effect` Q основного автора зарезервирован вместе с effect claim в одной записи. При внешнем dispatch `claim-effect --external-dispatch` разрешает одну отправку только при `dispatch_action=SEND_ONCE`. `HOLD_RECONCILE` требует сверки факта доставки, прежнего назначения и реального provider/recipient receipt.

Публичный `command-cut` возвращает cut для запроса и счётчики. Сверяй действительные Q и их исходы по точным журналу и принятым основаниям. `adviser-assign`, `adviser-contribute` и `adviser-dispose` используют тот же журнал в PREP и CORE. Требования к назначению и разрешённому контексту описаны в [контракте операторов](../roles/operators.md).
