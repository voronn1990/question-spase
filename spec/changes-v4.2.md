# Изменения v4.2

Исходный архив v4.1.0 сохранён как источник; v4.2 включает завершённый ремонт кода и обязательную архитектурную модель новых запусков. Фактические результаты текущей квалификации связываются отдельными hash-bound receipts выпуска. Исторические сведения о чужой установке и утраченном прогоне не подтверждают состояние этого выпуска.

## Ремонт

- Сохраняется STALE между resume; provenance, execution и exact dependency hashes распространяют воздействие адресно. READY_TO_DISPATCH consumer устаревшего результата переходит в WAITING; остальные launch states сохраняют смысл.
- Prepare/finalize держат coordinator lock до проверки и публикации. Отклонённое approval не оставляет immutable файл, блокирующий правильную повторную попытку.
- Проверяются обязательные поля, answer_status и knowledge_state; GATE-2 допускает адресованный OPEN, финальный READY сохраняет запрет открытых обязательных вопросов. Audit/release templates и schemas согласованы с кодом.

## Архитектура

- Один typed graph в accepted SYNTHESIS, exact selection из current S-000; текущие PLAN/FINAL_AUDIT/release связывают ту же модель. Старые snapshots остаются совместимыми с ограниченной квалификацией.
- Функциональный, контрактный и реализационный views/Mermaid производятся автоматически, с фильтрами subsystem и path to G. Альтернативы отделены от выбранного конструктивного пути.
- Детерминированная AND достижимость использует существующие graph helpers, canonical YAML, approved G/R, registry и current S/T/C contracts. Различаются EXISTING, PLANNED и EXTERNAL; будущий output не требует hash.
- Проверяются missing producer/I/O, contracts, порядок, execution/proof cycles, lost R, dead outputs и отсутствие source→G. Research feedback не становится execution edge.
- Implementation map различает пять статусов, связывает target/file/evidence/action, требует integration E для VERIFIED и поддерживает выборочный impact с preserved IDs. Свежесть фактических файлов подтверждается только supplied observations.
- Независимый архитектурный review обязателен в FINAL_AUDIT. Связность сама не даёт semantic PASS или READY.

## Пределы

Тестовые fixtures и реальные ограниченные задания агентам дают разное свидетельство. Полный новый 18-role малый и крупный прогоны, подписанное организационное runtime attestation, quality calibration, сравнение с v3, экономия токенов и продуктовый эффект не следуют из этих изменений. Глобальная установка не выполняется сборкой ZIP.
