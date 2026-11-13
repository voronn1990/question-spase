# OP-PROJECTION — Проекция

Вход: принятые G/R, конкретный путь/кандидат и E. Для каждого NOT_G укажи условия с `И/ИЛИ/порядком`, механизм, область, основания, alternative path, action и negative/positive control.

Строго различай:

- `G ⇒ N`: N необходимо в указанной области;
- `F ⇒ NOT_G`: F достаточно для провала в модели;
- risk factor;
- causal hypothesis.

Не выводи `NOT_F ⇒ G`. При необходимости найди минимальную достаточную комбинацию отказа и общую точку отказа резервов.

Временная семантика: ранний наблюдаемый признак → ограниченное окно полезного действия → действие → проверка сохранённого G. Постфактум-обнаружение или восстановление после окна не доказывает своевременный успех. Различай устранение причины, разрыв перехода, обход, обнаружение и восстановление; журналирование не исправляет причину.

Выполни первичную проекцию G и повторную для затронутых путей после синтеза/существенной правки. Выход: пути NOT_G, статусы, controls, остаточные условия и адресные rechecks.

На `PREP-3` режима GOAL выпусти адресные `NOT_G_PROJECTION` и `FUNCTIONAL_GAP` с lineage к точному `G/NOT_G/R`, перспективе и породившему вопросу. Расширяй только пока появляется содержательно новая проверка механизма; числовой квоты после десяти начальных вопросов каждой `P` нет. В режиме BANK не добавляй и не переписывай вопросы импортированного банка: оформи обнаруженный пробел как finding применимости с влиянием на admission.

## CURRENT_REALITY

Для marked run выпусти nested current `iceberg_view` kind `CURRENT_REALITY` по [contract](../../double-iceberg.md). У нового проекта observed failures не изобретай: используй sourced initial conditions и scoped H/UNKNOWN potential obstacles. Разделяй necessity, sufficient failure, risk, correlation, causal hypothesis и bounded confirmed causal influence; сохраняй combinations, shared failures, intervention windows и exact seed/E origins.
