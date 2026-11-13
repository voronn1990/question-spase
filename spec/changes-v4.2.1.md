# Изменения v4.2.1

Адресный ремонт семи замечаний критического аудита v4.2.0. Интеллектуальная методология, роли, YAML и существующий runtime сохраняются. Исходные выпуски не изменяются.

- R42-01: canonical task/branch/output states управляют конструктивным производством. NOT_APPLICABLE и UNSELECTED_BRANCH не поддерживают active G или consumers; selected output требует exact accepted E.
- R42-02: единый вычисляемый active/historical срез для maps/SYNTHESIS/semantic/execution/gates и текущего плана. Exact S-000 selection и законные assignments/supersedes выбирают текущий результат; история сохраняет собственные bindings и проверяется на tamper. Review старой цели сохраняется в active срезе, если его потребляет текущий execution graph; ghost endpoints не разрешаются.
- R42-03: model integrity отделена от reachability/blockers/readiness. Корректный условный SYNTHESIS можно принять; READY с внешним блокером запрещён, независимая работа сохраняется.
- R42-04/05: один normalizer порядка и resolver INPUT-ID/OUTPUT-ID/output path для основного и архитектурного валидаторов. Неизвестные, неоднозначные, несовместимые и преждевременные ссылки отклоняются.
- QG-01: subsystem views показывают boundary edges, направления и внешние canonical endpoints.
- QG-02: компактный functional overview с проверяемой provenance, полная detail projection и отдельные карточки контрактов; повторение полного текста в Mermaid устранено.

При подготовке нового core сохранённый FINAL_AUDIT считается историей только при точной проверяемой привязке к прежнему SYNTHESIS и его собственным maps (для iceberg-only режима — к сохранённым предкам maps). Его байты и собственные связи продолжают проверяться; неизвестные или смешанные ссылки не дают права на такой перенос. В фазе final выбранный audit остаётся текущим и обязан соответствовать новому core: прежний verdict не разрешает новый выпуск.

Результаты отрицательных/положительных примеров, нового сквозного прогона, независимого exact-byte review, полной регрессии и установки связываются receipts выпуска. Эти инструкции сами не утверждают их выполнение. Fixtures не означают native 18-role qualification, implementation/effect READY или экономическую состоятельность.
