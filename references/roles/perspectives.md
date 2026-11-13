# Контракт десяти профессиональных перспектив

Root выбирает `P-01…P-10` по утверждённому Вектору и материалам, а не из фиксированного списка. Все имеют разные реальные `agent_id`, competence, analysis scope, unique contribution, boundary, plausible blindspots и подтверждённую необходимость. Десять названий одной профессии, lexical score или необъяснённый material overlap не подходят. Exact map сохраняется от PREP до Core framing, PRIMARY и peer review.

## Два назначения каждой P

### 1. Blind framing и initial questions

Вход: самодостаточный слепой пакет без bank Q/чужих решений/preferred mechanism + accepted/imported Vector как bounded G/R/scope projection, собственная perspective row и только immutable approved brief material, явно выбранный Root в существующем `assignment.allowed_reads`; default none. Роль не получает all snapshot materials, bank binding/hash или duplicated selected content, пропускает workspace-memory/rollout lookup и читает только allowlist; автоматически внедрённый system/role context фиксируется отдельно и проверяется на bank/foreign results. Выход: неизменяемая собственная постановка `artifact_type: FRAMING`, причинная модель, существенные классы предпосылок/альтернатив/возможностей/отказов, нужные факты и граница компетенции.

В goal-mode PREP результат содержит явный список `initial_questions` по политике слепого пакета. Для `MATERIAL_UNKNOWNS_V1` фиксируй все материальные неизвестные своей области. Пустой список требует конкретного проверяемого `empty_initial_questions_rationale`. При отсутствии политики действует исторический контракт с десятью Q на профиль. [Контракт подготовки](../preparation.md) В bank mode Core framing не создаёт initial questions и не меняет supplied bank. Принятие связывает role/agent/assignment/attempt/snapshot, применимый approval/admission hash и фактический exposure/read record; чтение вне allowlist даёт `protocol_blindness: NOT_VERIFIED` и требует свежего reframing.

### 2. Primary и review

После freeze координатор выдаёт только назначенные Q и необходимые зависимости. P исследует фактические материалы до претензий, создаёт общий первичный YAML-result `artifact_type: PRIMARY` и адресные `ANSWER` results по фазе 2 и не читает первичные ответы других P до фиксации своей. Затем отдельным назначением создаёт принятые `REVIEW` results минимум для двух других P и проходит внешнее review.

## Обязательный результат P

- точные run/snapshot/role/assignment/attempt IDs и input hashes;
- hash blind framing и disclosure/exposure status;
- адресный ответ на каждый назначенный Q, knowledge status и E/пробел;
- механизм, альтернативы, цена ошибки/исследования и влияние на G;
- собственное самоопровержение и три сильнейших правдоподобных отказа либо честно меньше;
- отдельные содержательные reviews чужих утверждений;
- изменённые D, зависимости и следующий потребитель.

P не создаёт дочерних агентов, не переписывает чужой result, не голосует, не заполняет expansion до числа вопросами/дефектами и не выдаёт общую уверенность за E. Она не заменяет оператора, планировщика или аудитора.

## Внешние реализации после freeze

После принятия собственного первого FRAMING P может получить адресный [GitHub read assignment](../github.md) для key technical Q. Ищи способность через direct, functional, alternative и negative-experience routes; не принимай coordinator ranking, stars или favored stack за решение. Возвращай canonical `EXTERNAL_IMPLEMENTATION` E с exact commit/path/functions/bytes, conditions, limits, origin и transfer gap. Простой Q не требует GitHub. Недоступность read оставляет зависимый claim `UNKNOWN/NEEDS_RECHECK`, не отменяя независимый ответ в доступной области.

## Blind Double-Iceberg position

Каждая P добавляет к первому FRAMING собственный typed `iceberg_position` по [центральному контракту](../double-iceberg.md). Формулируй условия/способности/препятствия/предпосылки/механизмы/наблюдения/unknowns в своей области, их scope/materiality и finite check. Не читай bank, чужие positions, preferred mechanism или merged map до принятия result. Общая гипотеза позднее обязана сохранить exact accepted seed refs минимум двух разных P; совпадение формулировок не становится evidence.

В запуске FAST/DETAILED `question_work.lead` задаёт единственного текущего primary owner. `question_work.support` допускает содержательную независимую работу другой перспективы по уже подтверждённому началу того же канонического Q. При двух Q десять P могут распределиться между их двумя основными авторами и восемью дополнительными перспективами; каждая сохраняет отдельные FRAMING, полный PRIMARY, возражения и peer review. Новый вопрос, обнаруженный при support, требует отдельного допуска до содержательной проверки.
