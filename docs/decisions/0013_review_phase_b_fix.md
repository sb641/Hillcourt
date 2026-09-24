# 0013. Review фикса B: амбар тэна и mustered (Critic)

## Контекст

Critic перепроверяет фикс ManorClerk по лжи из 0012: `grant_thegn` теперь переводит двор
держателя (`person.household_id`) в книгу тэна сверх лимита тяглых дворов, поэтому
board/hunger/mustered идут по `manor:<id>`. Код не правился; 0012 не редактировался.

Хэши проверенной ревизии (sha256):
`engine/manor.py` `ec054238…239b2b`; `test_manor_entity.py` `3209dd42…f23a78`;
`test_manor_economy.py` `499dc0a1…e2c356b`. Если хэши изменились — ревью недействительно.

**Воркспейс не был заморожен.** Пока шло ревью, параллельный агент дописывал
`sim/tests/test_tenement_labor.py` (23:55), `design/scenarios/v0_two_settlements.yml` (00:03),
`sim/tests/test_two_settlements_world.py` (00:07) и удалил `design/scenarios/_tmp_search.yml`.
Поэтому число тестов на старте ревью было 135, к концу — 146. Метод: свои harness-скрипты
(`/tmp/opencode/check_fix_b.py`, `check_counterexample.py`, `check_revoke.py`,
`check_b_long.py`, `check_b_trace.py`, `check_emigration.py`, `check_relief.py`,
`check_regression.py`) и инструментовка `tick.PHASES`; `git status --short` до/после идентичен.

## Что проверено и чем (команды, числа)

### 1. Тесты

`bash sim/run_tests.sh`: на старте ревью — `Ran 135 tests … OK`; после дописывания
параллельным агентом — `Ran 146 tests … OK` два прогона подряд (77.4 с и 93.8 с). Один
промежуточный discover-прогон в момент параллельной записи дал `FAILED (failures=1)`; имя
теста не зафиксировано, в двух последующих прогонах не воспроизвелось. Вывод: 146 — текущее
число, но пока воркспейс пишется, число и зелёность нестабильны.

### 2. Книга, board, лимит, revoke (команда 1)

```bash
PYTHONPATH=sim/src python3 - <<'PY'
import yaml
from pathlib import Path
from collections import Counter
from hillcourt.scenario import load_scenario
from hillcourt.engine.tick import run_month
from hillcourt.runner import _apply_script_entry
from hillcourt.engine.manor import nested_manors, root_manor, revoke_thegn, grant_thegn
S = Path("design/scenarios/v0_hill_and_salt.yml")
data = yaml.safe_load(S.read_text(encoding="utf-8"))
data["script"] = [{"at_month": 6, "action": "grant_thegn", "person": "hh_retinue_p1",
                   "tiles": ["t_03_01", "t_05_02"], "households": ["hh_02"]}]
tmp = S.parent / "_tmp_0013.yml"
tmp.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
try:
    w = load_scenario(tmp, seed=1729)
    grant = None
    for i in range(1, 19):
        for e in w.script:
            if int(e.get("at_month", 0)) == i:
                _apply_script_entry(w, e)
                grant = (w.clock.year, w.clock.month)
        run_month(w)
    try:
        grant_thegn(load_scenario(tmp, seed=1729), "hh_retinue_p1",
                    ["t_03_01", "t_05_02"], ["hh_02", "hh_03", "hh_05", "hh_08"])
        limit = "NOT REJECTED"
    except ValueError as exc:
        limit = f"rejected: {exc}"
finally:
    tmp.unlink(missing_ok=True)
thegn = nested_manors(w)[0]
root = root_manor(w)
holder = w.households["hh_retinue"]
board = [e for e in w.ledger.entries
         if e.reason == "board" and e.dst_id == holder.stock_id
         and (e.date.year, e.date.month) >= grant]
print("holder in thegn/root:", holder.id in thegn.household_ids, holder.id in root.household_ids)
print("board after grant sources:", dict(Counter(e.src_id for e in board)))
print("limit 4 tenants:", limit)
print("M18 hunger/mustered/barn:", holder.hunger_days, thegn.mustered,
      w.get_stock(thegn.stock_id).amounts.get("grain", 0.0))
print("revoke:", revoke_thegn(w, thegn.id), holder.manor_id, holder.id in root.household_ids)
PY
```

Вывод: `holder in thegn/root: True False`; `board after grant sources:
{'manor:manor_hh_retinue_p1': 13}` (0 из `settlement:hill_court`);
`limit 4 tenants: rejected: Пожалование тэна превышает лимит сценария`;
`M18 hunger/mustered/barn: 0 True 0.0`; `revoke: True manor_hill True`.

- Книга: `hh_retinue` в `thegn.household_ids` ровно один раз, не в `root.household_ids`;
  `grant_thegn` логирует `households=['hh_02', 'hh_retinue']`.
- Лимит: 3 тяглых + держатель (книга 4) — ок; 4 тяглых — `ValueError`; держатель, переданный
  явно, лимит не расходует.
- Амбар M18: приход `harvest_grain` 18.4 + `straw` 2.944 + `gafol` 4.0 = 25.344, расход
  `board` 22.4; остаток 2.944 (несъедобная солома); зерно вошло = зерно вышло. Буквальный
  старый критерий «амбар>0 ИЛИ голод» на M18 даёт 0 и 0, но это не ложь: паёк выбрал амбар
  (13 переводов), поэтому остаток 0; критерий читать как «источник пайка — амбар».
- revoke (второй прогон, `hh_01_p1` с пресетом sokeman): после grant `thegn`, после revoke —
  `sokeman` с восстановленными `personal/land/bundle`, `manor_id=manor_hill`, в корне; зерно
  амбара слилось в корень ровно, материя до=после; board после revoke снова из
  `settlement:hill_court` (1 перевод).

### 3. Контрпример 0012 (команда 2): причина — spoil→consume, не замок

```bash
PYTHONPATH=sim/src python3 - <<'PY'
import yaml
from pathlib import Path
from hillcourt.scenario import load_scenario
from hillcourt.engine.tick import run_month
from hillcourt.runner import _apply_script_entry
from hillcourt.engine.manor import nested_manors
S = Path("design/scenarios/v0_hill_and_salt.yml")

def probe(castle_grain):
    data = yaml.safe_load(S.read_text(encoding="utf-8"))
    data["script"] = [{"at_month": 6, "action": "grant_thegn", "person": "hh_retinue_p1",
                       "tiles": ["t_03_01", "t_05_02"], "households": ["hh_02"]}]
    tmp = S.parent / "_tmp_0013b.yml"
    tmp.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    try:
        w = load_scenario(tmp, seed=1729)
    finally:
        tmp.unlink(missing_ok=True)
    for e in w.script:
        _apply_script_entry(w, e)
    thegn = nested_manors(w)[0]
    holder = w.households["hh_retinue"]
    w.get_stock("settlement:hill_court").amounts["grain"] = castle_grain
    w.get_stock(holder.stock_id).amounts["grain"] = 0.0
    w.get_stock(thegn.stock_id).amounts["grain"] = 100.0
    holder.hunger_days = 0
    n0 = len(w.ledger.entries)
    run_month(w)
    touched = [(e.reason, e.src_id, e.dst_id, round(e.amount, 4))
               for e in w.ledger.entries[n0:]
               if holder.stock_id in (e.src_id, e.dst_id)]
    print(f"castle={castle_grain}: {touched} hunger={holder.hunger_days} "
          f"mustered={thegn.mustered}")

probe(0.0)
probe(999.0)
PY
```

Вывод: `castle=0.0: [('board', 'manor:manor_hh_retinue_p1', 'household:hh_retinue', 3.0),
('spoil', …, 0.03), ('eat', …, 2.97), ('burn', …, 1.8)] hunger=1 mustered=False`;
`castle=999.0` — те же числа. Инструментовка по фазам: `phase_manor` +3.0 (0→3.0),
`phase_spoil` −0.03 (3.0→2.97), `phase_consume` съедает 2.97 при нужде 3.0 → `hunger=1`.
Причина ровно та, что назвал ManorClerk; замок ни при чём. Других причин нет: у держателя
после grant нет кормящих клеток, `phase_labor` зерна не добавляет.

### 4. Длинный горизонт: остаточные дыры (команда 3)

```bash
PYTHONPATH=sim/src python3 - <<'PY'
import yaml
from pathlib import Path
from collections import Counter
from hillcourt.scenario import load_scenario
from hillcourt.engine.tick import run_month
from hillcourt.runner import _apply_script_entry
from hillcourt.engine.manor import nested_manors
S = Path("design/scenarios/v0_hill_and_salt.yml")
data = yaml.safe_load(S.read_text(encoding="utf-8"))
data["script"] = [{"at_month": 6, "action": "grant_thegn", "person": "hh_retinue_p1",
                   "tiles": ["t_03_01", "t_05_02"], "households": ["hh_02"]}]
tmp = S.parent / "_tmp_0013c.yml"
tmp.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
try:
    w = load_scenario(tmp, seed=1729)
    grant = None
    hungry = 0
    false_muster = 0
    for i in range(1, 37):
        for e in w.script:
            if int(e.get("at_month", 0)) == i:
                _apply_script_entry(w, e)
                grant = (w.clock.year, w.clock.month)
        run_month(w)
        if grant and (w.clock.year, w.clock.month) > grant:
            holder = w.households["hh_retinue"]
            thegn = nested_manors(w)[0]
            hungry += holder.hunger_days > 0
            false_muster += not thegn.mustered
finally:
    tmp.unlink(missing_ok=True)
holder = w.households["hh_retinue"]
thegn = nested_manors(w)[0]
board = [e for e in w.ledger.entries
         if e.reason == "board" and e.dst_id == holder.stock_id
         and (e.date.year, e.date.month) >= grant]
relief = [e for e in w.ledger.entries if e.reason == "relief" and e.dst_id == holder.stock_id]
print("hungry months / mustered-false months:", hungry, false_muster, "of 30")
print("holder left_at:", holder.left_at, "tile:", holder.current_tile_id,
      "hunger:", holder.hunger_days, "mustered:", thegn.mustered)
print("barn grain:", w.get_stock(thegn.stock_id).amounts.get("grain", 0.0),
      "board by src:", dict(Counter(e.src_id for e in board)))
print("relief to holder:", [(str(e.date), e.src_id, e.amount) for e in relief])
PY
```

Вывод: `hungry months / mustered-false months: 18 18 of 30`; `holder left_at: Y2-M09
tile: t_00_01 hunger: 3 mustered: False`; `barn grain: 30.0 board by src:
{'manor:manor_hh_retinue_p1': 16}`; `relief to holder: [('Y2-M09', 'settlement:hill_court', 3.0)]`.

- Хронология (трасса по месяцам): амбар даёт ~1.72 зерна/мес (18.4 harvest + 4.0 gafol за
  13 мес), нужда держателя 3/мес; собственный запас 20 тает и кончается к Y2-M06; с Y2-M07
  голод; на `hunger_days=3` (Y2-M09) `phase_migrate` уводит двор держателя
  (`thegn.can_leave=true`) — `move_0002_09_hh_retinue`, прибытие Y2-M10 на `t_00_01`.
- После прибытия `left_at` не снимается (зомби-состояние из 0012 A3): board и consume двор
  пропускают, голод заморожен на 3, mustered навсегда False, а амбар растёт до 30.0 — на
  книге тэна не осталось едоков (пожалованный `hh_02` — виллан, пайка не получает).
- Канал замка: `apply_relief` (`economy/exchange.py:34-58`) платит из `settlement:hill_court`
  без учёта `manor_id`; в прогоне 3.0 ушли держателю в Y2-M09. Контрольный прогон с пустым
  амбаром и замком 999: relief 3.0/мес из замка, но держатель всё равно голодает (1→2→3,
  тот же спойл 0.03) и уходит — **замок не может «купить» mustered и через relief**.

### 5. Регрессия A1–A4

```bash
PYTHONPATH=sim/src python3 - <<'PY'
from pathlib import Path
from hillcourt.runner import run
S = Path("design/scenarios/v0_hill_and_salt.yml")
for s in (1729, 42, 7, 99):
    r = run(S, 36, seed=s)
    print("A1", s, round(r.castle_stores["salt"], 6), f"{r.matter_delta:.9f}")
d, e = run(S, 6), run(S, 6, seed=1729)
a, b = run(S, 6, seed=42), run(S, 6, seed=42)
print("A2", d.state_hash == e.state_hash, a.state_hash == b.state_hash, a.state_hash != e.state_hash)
PY
```

Вывод: `A1 1729/42/7 15.6`, `A1 99 6.074074`, `delta -0.000000000` на всех; `A2 True True True`.
Дополнительно: Ledger seed 1729 — 7 `caravan_load`, 5 `caravan_unload`, 0 телепортов
household→castle, 0 `external_in`; A3 — первый уход hh_06 Y1-M10: `household_move`, route 2,
`current_tile_id` не меняется, cargo 5.4, прибытие — сток 5.4, delta 0; A4 — runner CLI
печатает `[Y1-M02] grant_tenure`, `[Y1-M03] add_obligation`, `[Y1-M03] send_party`, прямая
материя delta `0.000000000`. Фикс B A1–A4 не сломал.

## Вердикт: B закрыт (исходная ложь снята), остаточные дыры открыты

**B закрыт** по предмету фикса и по всем четырём пунктам задания: board держателя 13/13 из
`manor:manor_hh_retinue_p1` и 0 из замка; двор держателя в книге тэна, не в корне; лимит
тяглых ≤3 держится (держатель сверх лимита); revoke возвращает держателя, пресет и амбар;
`mustered=False` в команде 4 объясняется порядком spoil→consume (0.03), а не замком
(castle 0 и castle 999 дают идентичные числа). Исходная ложь 0012 «mustered зависел от
замка» снята.

**Но B нельзя объявлять самодостаточным** — открытые остаточные дыры (не отменяют фикс,
требуют отдельного решения владельцев):

1. **mustered заморожен после ухода держателя** (Y2-M09): 18/30 месяцев False при амбаре
   30.0; `thegn_hungry` читает голод ушедшего двора, а `left_at` выключает его из
   board/consume. Это зомби-дыра A3, теперь бьющая по тэну (Implementer/Info).
2. **Амбар не кормит 3 взрослых**: производство 1.72 зерна/мес < нужды 3/мес, собственный
   запас кончается на 13-м месяце — экономика тэна, не фикс (Economist).
3. **У замка остался канал к держателю**: `apply_relief` платит из замка без учёта
   `manor_id` (3.0 в Y2-M09). Канал не спасает mustered (спойл), но противоречит духу
   «стол держателя — амбар тэна»; решить, платить ли relief через манор (Legal/Info).

Процессное: воркспейс не был заморожен во время приёмки (135→146 тестов, один прогон
`FAILED` в момент записи, удалён `_tmp_search.yml`). Приёмку повторять на замороженной
ревизии.

## Что запрещено следующим агентам (до закрытия найденного)

1. Объявлять B полностью закрытым («тэн сам себя кормит»): дыры 1–3 открыты, числа выше.
2. Ссылаться на M18-критерий буквально («амбар>0 или голод»): на M18 амбар 0 (паёк его
   выбрал) и голод 0; истина — board 13/13 из амбара; читать критерий как «источник пайка».
3. Считать `request_relief` «столом держателя» или каналом mustered: это отдельный канал
   замка; решать отдельным ADR.
4. Править/ослаблять проверки книги, board, лимита и revoke без нового ADR.
5. Удалять или переписывать 0012/0013: пересмотр — новым ADR со ссылкой.
6. Вести приёмку на незамороженном воркспейсе: число тестов и результат менялись в момент
   параллельной записи.

## Проверка (команда)

```bash
bash sim/run_tests.sh
```

Ожидание на момент отчёта: `Ran 146 tests … OK` (135 было до параллельной дописки).
Хэши файлов фикса: `engine/manor.py ec054238…239b2b`, `test_manor_entity.py 3209dd42…f23a78`,
`test_manor_economy.py 499dc0a1…e2c356b`; при расхождении ревью недействительно. Числа команд
1–3 должны совпасть с выписанными.
