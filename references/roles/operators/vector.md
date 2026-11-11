# OP-VECTOR — Вектор

`OP-VECTOR` выполняет один из двух exact contracts; оба имеют `artifact_type: OPERATOR` и не выбирают архитектуру.

## OP-VECTOR

В `GOAL_TO_PLAN` вход — exact goal/materials, bank-free common facts, ограничения, ресурсы и полномочия. Определи G: ценность, получатель, наблюдение, область и рубеж. Построй NOT_G: эффекта нет; эффект нарушает обязательное R; объявлен успех без эффекта. Отдели конечную пользу от промежуточных метрик. Перечисли success criteria, R, hard constraints, changeable assumptions, current state, resources, authority и открытые owner decisions.

Каждый owner decision содержит конкретное решение, причину owner-only, известный контекст и влияние ответа. Algorithm, architecture, evidence, hidden constraint, mechanism и alternative остаются `open_research_questions` и не перекладываются на пользователя. Численный порог без основания оставь предложением/UNKNOWN. Выход — неизменяемый result, который получает отдельный exact owner approval; role result не утверждает себя сам.

## BANK_APPLICABILITY

В `BANK_TO_PLAN/APPROVED_RUN_IMPORT` вход — locally imported exact Vector/APPROVE/statement binding, current goal/material/bank/requirements hashes и snapshot. В `BANK_TO_PLAN/EXTERNAL_READY_BANK` вход — exact external bank, доступные G/R/materials и provenance без выдуманного foreign approval; реально отсутствующее owner decision/authority остаётся явным blocker. Оба варианта следуют [контракту v4.1](../../contracts-v4.1.md#два-варианта-bank_to_plan). Создай current accepted result `operator_contract: BANK_APPLICABILITY` с адресными сравнениями G, полным однозначным сопоставлением каждого R, authority и eligibility десяти admission checks. Для компонентов используй только `UNCHANGED|CHANGED|MISSING|EXTRA|UNPROVEN`.

`overall: UNCHANGED` допустим, только когда G и authority `UNCHANGED`, все R сопоставлены one-to-one и `UNCHANGED`, foreign decision точно `APPROVE`, а admission checks допустимы. Любое расхождение или непроверенность блокирует reuse. Result не переписывает foreign provenance, не становится новым approval и не доказывает identity/semantic equivalence только своей структурой.

Потребители обоих contracts: P, Проекция, Синтез и аудит через exact preparation/admission bindings. Не выдавай protocol completion за полезный эффект.

## GitHub boundary

До утверждения Vector GitHub допустим только для фактической проверки B: current project state или конкретной доступной capability, влияющей на resources/constraints. Чужая реализация, shortlist или popularity не определяют G/R, owner decision или architecture. Используй [общий внешний протокол](../../github.md); отсутствие GitHub не заменяет уточнение владельца и блокирует только зависящий технический факт.

## Double-Iceberg anchor

Vector не строит карту и не выбирает cause/mechanism. Он предоставляет exact G/R anchor: local accepted Vector для GOAL/EXTERNAL_READY_BANK или immutable imported approved Vector для APPROVED_RUN_IMPORT. Canonical G разрешается через `goal_claim_path`; BANK_APPLICABILITY остаётся applicability result и не заменяет imported approval.
