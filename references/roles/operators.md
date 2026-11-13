# Маршрут шести операторов

Каждый оператор — отдельный реальный `agent_id`, не P и не другой оператор. Назначенная роль читает общие инварианты, текущую фазу и только собственный контракт:

- [OP-VECTOR](operators/vector.md)
- [OP-PROJECTION](operators/projection.md)
- [OP-DIVERGENCE](operators/divergence.md)
- [OP-REVERSE](operators/reverse.md)
- [OP-INDULGENCE](operators/indulgence.md)
- [OP-SYNTHESIS](operators/synthesis.md)

YAML-result `NOT_APPLICABLE` допустим только после фактического назначения и содержит предметное основание, проверенные входы и влияние на G. Координатор принимает results `VECTOR…INDULGENCE` с `artifact_type: OPERATOR`, result `SYNTHESIS` с `artifact_type: SYNTHESIS`; каждый связан со своей ролью/агентом/назначением/попыткой/snapshot. Методы из каталога 14 выполняются внутри назначенных ролей; отдельный агент появляется только для конкретной новой независимой проверки, не автоматически.

На подготовке GOAL эти же установленные роли выполняют распределённое расширение банка из [канонического протокола подготовки](../preparation.md): `OP-PROJECTION` строит проекции `NOT_G`, `OP-DIVERGENCE` ищет альтернативы и общие предпосылки, остальные роли подключаются по предметному триггеру. Это не создаёт новых ролей и не меняет фактический набор десяти `P`. В режиме BANK роли не генерируют новый банк: они проверяют применимость неизменяемого импорта и адресно фиксируют пробелы.

Для marked run шесть операторов применяют [Double-Iceberg contract](../double-iceberg.md): VECTOR закрепляет exact G/R anchor; PROJECTION владеет CURRENT_REALITY; DIVERGENCE связывает разногласия и shared assumptions; REVERSE владеет DESIRED_REALITY; INDULGENCE выбирает finite discriminating check; SYNTHESIS связывает оба exact heads и A/B/C. Это nested data существующих results и не добавляет OP, store или обязательный цикл.

## Советники PREP и CORE

Root может назначить советника в FAST и DETAILED на конкретный пробел, альтернативу, возражение или вопрос. Используй `role_id: ADVISER`, `artifact_type: EVIDENCE`, `selection_authority: ROOT`, отдельный реальный handle, `logical_occurrence_id` и точные `adviser.address`, `adviser.recipient_assignment_id` и `adviser.work_kind`. Совместно с советником работают все 18 штатных ролей со своими идентичностями. В PREP адрес указывает зависимый PREP-3 или PREP-4 и его допущенные связи с Вектором, E и D. В CORE адрес Q или R должен быть явно доступен назначению получателя и разрешаться в его текущем выбранном графе. Слепая FRAMING роль и закрытые материалы сохраняют исходный режим доступа.

Выполни `adviser-assign`, затем обычный `claim-effect` и `context`. Для routing-only контекст содержит адреса, связи и digests. Для substantive заранее свяжи советника через support с уже исследуемым Q и передай только предусмотренные поля точных доступных E/D. Проверка свежести проходит перед отправкой и повторяется при выдаче. Источник для советника — выданный `context`. Команды `graph-query` и `context --historical` для его назначения отклоняются.

Сохрани результат с SHA контекста и `advice_contribution`. Сверь реальное подтверждение получателя через `reconcile-effect`, затем зарегистрируй вклад через `adviser-contribute`. Получатель-перспектива записывает собственное решение в REVIEW со своим принятым blind FRAMING. Получатель-оператор, планировщик или аудитор записывает решение в собственном EVIDENCE с `advice_disposition` и полями `target_result_id`, `target_sha256`, `context_sha256`, `recipient_assignment_id`, `recipient_attempt_id`, `status`, `rationale`. Независимая перспектива проверяет точный EVIDENCE обычным REVIEW. Команда `adviser-dispose` использует REVIEW. `ACCEPTED`, `QUALIFIED` и обычный `REJECTED` требуют свежего контекста. При устаревании текущий получатель может сохранить `REJECTED` с точным `retired_context_sha256` и причиной без расширения прежнего E/D.

При изменении выбранного графа этап PLAN может сохранить принятое или квалифицированное замечание через `advice_consumption`. В строке укажи `advice_result_id`, `advice_sha256`, `disposition_result_id`, `disposition_sha256`, `context_sha256` и `scope`, действующий на этом этапе. Назначение PLAN связывает вклад и REVIEW решения по ID и SHA. Независимый REVIEW нового этапа указывает оба ID в `related_ids` и проверяет реальный эффект замечания и условия квалификации. Существенный pending или утрата применимости основания удерживают затронутый этап. Скрытое содержательное исследование учитывай через наблюдение `HIDDEN_WORK`.
