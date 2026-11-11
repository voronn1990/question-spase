# Перенос инженерных практик: карта изменений

Входы OWNER-SCOPE-03/04 меняют исполнение, сохраняя 10 перспектив, 6 операторов, отдельные планировщик и аудитор, шесть этапов, 14 приёмов и восемь областей аудита. Исходное обследование использовало snapshot scope02 из 52 файлов. Ниже отражены фактически проверенные изменения; новая интеграция scope05 и scope06 требует отдельных проверок. Итоговый статус выпуска находится в `tests/evidence/qualification.yaml`, точные хэши прочитанных первоисточников — в `tests/evidence/source-inventory.yaml`.

| Требование | Механизм и изменение | Фактическая проверка | Предел вывода |
|---|---|---|---|
| Обязательства независимо от DONE | Snapshot Q/R задаёт знаменатель; addressable outcomes, reviews, typed graph и точные accepted endpoints | scope03: 63/63 независимых регрессий, положительный выпуск и отрицательные мутации | Полнота ссылок не доказывает предметную истинность ответов |
| Нециклический выпуск | prepare фиксирует core до независимого audit/GATE6; finalize повторно сверяет core | Независимые публичные положительные lifecycle и post-audit tamper probes | Текущая интеграция новых режимов отдельно не квалифицирована |
| Исходные обязательства | Обязательный exact original и все immutable input bytes входят в snapshot | Изменённый источник после фиксации отвергается | Совпадение байтов не удостоверяет полномочия автора |
| Постоянное состояние и recovery | Immutable intents/transactions, восстановление индекса, сохранение retry history и точной выбранной версии плана | Потеря индекса, повтор доставки, конфликтующие bytes, retained v1/v2 и stale selection | Unknown dispatch не повторяется без выяснения фактического исхода |
| Граф и переиспользование E | Адресный cache с input/tool/version/policy/freshness; транзитивная инвалидизация реальных consumers | Независимая ветвь сохраняется, цикл и stale-selected endpoint блокируются | Один общий источник не превращается в независимые свидетельства |
| Роутинг и независимость | Root выбирает компетенции; скрипт проверяет карту, зависимости, два разных nonauthor checks и ready work при лимите трёх групп | Независимые competence/DAG/resource-conflict/critical-review пробы | Скрипт не назначает смысловую компетенцию и не вызывает модели |
| Адресный контекст | context собирает role/phase/Q/R/E/dependencies; blind FRAMING исключает банк, чужие PRIMARY и расширения | Исходная утечка readset воспроизведена, исправлена и независимо перепроверена | Blindness подтверждена протоколом чтения; технический ACL не аттестован |
| Поведенческие испытания | Семь реальных агентных TEST01..07 с сохранёнными input/actions/output и независимой оценкой | 7/7 bounded PASS: false READY, ложная предпосылка, псевдо A/B/C, слабый план, сильная альтернатива, давление, адресный ремонт | Это не полный 18-role run и не содержательное исследование банка согласованного владельцем размера |
| Разделение мышления и механики | Существующий qs.py выполняет commit/context/resume и связи; LLM исследует и оценивает содержание | Scope03 принят независимо; 75 протокольных чтений/287944 байта в прежнем малом прогоне | Сопоставимые v3/v4 tokens/cost отсутствуют; экономия не заявляется |

Принятый scope03 gate `eea43e3a797f6e3c77f9f9876fcc9c8255cae6f9e7c6ab3ec0856e728563fbef` связан с отдельным неизменяемым snapshot, а не с произвольной будущей версией файлов. Scope04 gate `4365404f696351e52a847c47f1074551e4062d463faef1ea0c7e908b246af361` связан с точными семью сценариями. Последующие изменения проходят адресную проверку применимости; старый PASS автоматически не переносится.

## Основания из первоисточников

Источники проверены 2026-10-21. Ссылки ведут в официальные проекты/публикации; имена веток main не являются фиксацией версии. Внешние реализации не становятся нормативными инструкциями Question Space автоматически.

- [Trail of Bits c-review](https://github.com/trailofbits/skills/blob/main/plugins/c-review/skills/c-review/SKILL.md): перечень единиц строится до обзора, а сборщик сверяет адресный ledger. Перенос: исходный Q/R и обязательства задают знаменатель покрытия; один найденный дефект не закрывает остальные объекты. Ограничения применимости сохраняются.
- [Superpowers: testing skills with subagents](https://github.com/obra/superpowers/blob/main/skills/writing-skills/testing-skills-with-subagents.md): реальные сценарии давления, наблюдаемый отрицательный исход, исправление и повторная проба. Перенос: точные протоколы TEST01..07, без обещания универсальной устойчивости по единичному PASS.
- [Beads](https://github.com/gastownhall/beads): постоянный граф и ready work без открытых блокирующих зависимостей. Перенос: YAML-граф и recovery существующего qs.py. Dolt, сервис и внешняя платформа не добавляются. Старый адрес steveyegge/beads перенаправляет в gastownhall/beads.
- [Geoffrey Huntley: Ralph](https://ghuntley.com/ralph/): узкие задания, свежий контекст, координатор как диспетчер и проверка наличия реализации до нового кода. Перенос: адресный пакет и readset; бесконечный цикл Синтеза и неограниченный параллелизм не переносятся.
- [Vercel Agent Skills](https://github.com/vercel-labs/agent-skills/blob/main/AGENTS.md): краткая точка входа, supporting references по необходимости, программные операции в скриптах. Перенос: единый источник каждого правила и загрузка по фазе/роли.
- [Simon Willison: Red/green TDD](https://simonwillison.net/guides/agentic-engineering-patterns/red-green-tdd/): сначала наблюдать отказ проверяемого свойства, затем исправление и регрессию. Перенос: вычислимые свойства проверяются программой, предметные выводы — независимой содержательной проверкой.

Это собственная инженерная адаптация перечисленных практик к согласованной методологии; она не подтверждает качество или поддержку авторами внешних проектов. Не копировать их число агентов, авторизационные потоки, ограничения поиска или инфраструктуру.

## Scope06 external implementation research

Scope06 uses native GitHub reads as addressable research inputs under [one lazy protocol](../references/github.md). The adaptation preserves existing roles, YAML registry, content-addressed E reuse, typed consumers and independent final audit. Repositories do not become normative authorities or product dependencies by citation. Direct, functional, alternative and negative-experience search widens mechanism space; exact commit/code/tests/receipts and target applicability bound conclusions. No popularity score, repository quota, vector store, new runtime, mandatory role or domain-specific architecture is imported.
