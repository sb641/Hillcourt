# 0012. Review фаз A и B (Critic)

## Контекст

Ночь Tech Lead: фазы A (A1–A4) и B по `design/review/backlog_v0.md`. Проверка адверсариальная:
Critic не автор кода и код не правил. Ревизия `bdeb385`; рабочее дерево — незакоммиченные
`design/`, `sim/`, `docs/`. Зона Critic — только этот отчёт.

Метод: независимые harness-скрипты (`/tmp/opencode/check_*.py`), инструментовка monkeypatch
для точного момента ухода/прибытия, воспроизводимые inline-команды ниже. Тесты проекта:
`bash sim/run_tests.sh` → `Ran 133 tests in 17.433s ... OK`.

Чистота: `git status --short` до и после идентичен (` M README.md`; `?? AGENTS.md client/
design/ docs/ sim/ tools/`). Временные YAML `design/scenarios/_tmp_review*.yml` создавались и
удалены; ни один файл кода/тестов/каталогов не изменён. Старые ADR не редактировались.

## Что проверено и чем (команды, числа)

### A1. Соль едет

Команда 1 (4 сида + Ledger сида 1729):

```bash
PYTHONPATH=sim/src python3 - <<'PY'
from pathlib import Path
from collections import Counter
from hillcourt.runner import run
from hillcourt.scenario import load_scenario
from hillcourt.engine.tick import run_month
S = Path("design/scenarios/v0_hill_and_salt.yml")
for s in (1729, 42, 7, 99):
    r = run(S, 36, seed=s)
    print("seed", s, "castle_salt", round(r.castle_stores["salt"], 6), "delta", f"{r.matter_delta:.9f}")
w = load_scenario(S, seed=1729)
for _ in range(36):
    run_month(w)
c = Counter((e.kind, e.reason, e.src_id.split(":")[0] if e.src_id else "-",
             e.dst_id.split(":")[0] if e.dst_id else "-")
            for e in w.ledger.entries if e.good == "salt")
for k in sorted(c, key=str):
    print("salt", k, c[k])
print("direct household->castle",
      sum(1 for e in w.ledger.entries if e.good == "salt" and e.kind == "transfer"
          and e.src_id.startswith("household:") and e.dst_id == "settlement:hill_court"))
print("external_in",
      sum(1 for e in w.ledger.entries if e.good == "salt" and e.kind == "external_in"))
print("caravans", sorted((str(p.departed_date), len(p.route), p.status) for p in w.packs.values() if p.kind == "caravan"))
PY
```

Вывод: `castle_salt` **15.6 / 15.6 / 15.6 / 6.074074** (сиды 1729/42/7/99), `delta
-0.000000000` на каждом. Ledger сида 1729 по соли: `transfer caravan_load household→pack` —
**7**, `transfer caravan_unload pack→settlement` — **5**, `process boil_salt sink→household` —
11, `direct household->castle` — **0**, `external_in` — **0**. Сиды 42 и 7 дают те же 12
transfer; сид 99 — 2 transfer (1 load + 1 unload), `external_in` 0.

- Каденция: обозы Y1-M03/06/09/12, Y2-M03 (все `month % 3 == 0`); `len(route)=11`,
  `eta > departed` у всех, `lost=0`, в пути на конец прогона 0.
- Баланс: сид 1729 — `21.6 == 10.0 (initial) + 11.6 (process)`, diff `0.000000000`;
  сид 99 — `12.074074 == 10 + 2.074074`.
- Report обоза (независимый расчёт: живые дворы солеваров + склад деревни + возы
  `in_transit`, без вызова `_salt_in_settlement`): сид 1729 — 8 отчётов, 3 при фактической
  соли > 0.1, «0 при фактической соли > 0.1» — **0**, нарушений шума ±40% — **0**. Сид 42 —
  8/4/0/0; сид 7 — 11/4/0/0; сид 99 — 11/1/0/0. Нули честные: деревня в те месяцы реально
  пуста (соль ещё не выварена или уже увезена); замковая соль (baseline 4.0) > 0 — отчёт
  говорит о деревне, не о мире. Доставленные — подмножество проверенных (задержка 2 мес).
- Обман: `grep "external_in\|\.emit("` в `economy/caravan.py` и `info/briefing.py` — пусто.
  `briefing.py` не читает `Tile.state`; читает стоки, чтобы построить шумный (±40%) рассказ.
  Игрок получает только `Report` (`news/views.py:19`); `test_news_no_omniscience` зелёный.

### A2. seed

Команда 2:

```bash
PYTHONPATH=sim/src python3 - <<'PY'
from pathlib import Path
from hillcourt.runner import run
from hillcourt.scenario import load_scenario
S = Path("design/scenarios/v0_hill_and_salt.yml")
d, e = run(S, 6), run(S, 6, seed=1729)
a, b, c = run(S, 6, seed=42), run(S, 6, seed=42), run(S, 6, seed=1729)
print("default==1729", d.state_hash == e.state_hash, d.state_hash[:12])
print("42==42", a.state_hash == b.state_hash, "42!=1729", a.state_hash != c.state_hash, a.state_hash[:12])
print("load42 twice", load_scenario(S, seed=42).state_hash() == load_scenario(S, seed=42).state_hash())
PY
```

Вывод: `default==1729 True 0a91d3170414`, `42==42 True 42!=1729 True 6080b3483d4f`,
`load42 twice True`. Дополнительно: черты Person default vs seed=42 различаются 64/69;
replay `random.Random("42:persons:<hid>")` — **64/64** для дворов без явных YAML-черт (5
различий — hh_07, у него `curiosity/fear` заданы сценарием); поток `rng.economy` равен
`random.Random("42:economy")`, сдвинутому на k=1 (`plan_next_month`), `world`/`news`/`hazard` —
k=0; последовательности seed=42 совпадают между загрузками и отличаются от 1729. Гибрида нет:
`scenario.py:157` (`seed = effective_seed`), `:174` (`random.Random`), `:295`
(`RngStreams.from_seed`).

### A3. Уход

- Первый уход: hh_06 (`free_landless`), Y1-M10, Pack `move_0001_10_hh_06`, kind
  `household_move`, route `[t_02_02, t_02_01]` (len 2), status `in_transit`,
  `current_tile_id` не изменился в тик ухода, `left_at=Y1-M10`, `member_ids=[]`,
  `eta > departed`, дельта материи 0.
- Точный момент (инструментовка `tick._start_departure`/`tick.resolve_migrations`):
  stock_before 5.4 = cargo 5.4, stock_after 0, словари груза равны; прибытие: cargo_at_eta
  5.4 → сток двора 5.4 ровно, status `arrived`, люди на destination, delta 0.
- tied: hh_02 (villein), hh_03 (cotter), hh_05 (villein), hh_08 (villein) при `hunger=5` и
  `arrears=100×due`: `can_leave=False`, `left_at=None`, новых Pack 0.
- Гибель на hazard: cargo 43.0 → стоячий сток клетки 43.0 (не delete), status `lost`,
  health=0 у всех, silence `rep_0001`, delta 0.
- Телепорт-ветки нет: единственная запись `current_tile_id =` — `hazards/travel.py:127`
  (прибытие); удалений дворов/людей нет.
- Оговорка (не нарушение A3-критерия): дошедший двор остаётся `left_at=Y1-M10`,
  `settlement_id=None`, `intent=leave`, поэтому выключен из consume/manor/work: за 3 месяца
  hunger 3→3, labor 40→40, зерно убыло только порчей 3.0→2.91. Дыра для судьбы ушедших (E).

### A4. Действия

- Прямые вызовы `grant_tenure` / `add_obligation` / `send_party`: `player_actions` с датой
  Y1-M01; Report — `eye_from_hill` (право), `eye_from_hill` (повинность), `messenger`
  (посылка); дельта материи `0.000000000` на каждом.
- runner с script-фикстурой печатает: `[Y1-M02] grant_tenure ...`,
  `[Y1-M02] add_obligation ...`, `[Y1-M03] send_party ...` (CLI, дельта `0.000000`).
- Оговорка: `send_party` ставит `Person.location_tile_id = destination` уже при отправке
  (`legal/actions.py:118`), хотя Pack ещё `in_transit`; `_return_survivors` поправляет при
  прибытии. На acceptance A4 не влияет.

### B. Тэн

Команда 3 (grant на Y1-M06, прогон 18 мес):

```bash
PYTHONPATH=sim/src python3 - <<'PY'
import yaml
from pathlib import Path
from hillcourt.scenario import load_scenario
from hillcourt.engine.tick import run_month
from hillcourt.runner import _apply_script_entry
from hillcourt.engine.manor import nested_manors, grant_thegn, manor_depth
S = Path("design/scenarios/v0_hill_and_salt.yml")
data = yaml.safe_load(S.read_text(encoding="utf-8"))
data["script"] = [{"at_month": 6, "action": "grant_thegn", "person": "hh_retinue_p1",
                   "tiles": ["t_03_01", "t_05_02"], "households": ["hh_02"]}]
tmp = S.parent / "_tmp_review.yml"
tmp.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
try:
    w = load_scenario(tmp, seed=1729)
    base = load_scenario(S, seed=1729)
    for i in range(1, 19):
        for entry in w.script:
            if int(entry.get("at_month", 0)) == i:
                _apply_script_entry(w, entry)
        run_month(w); run_month(base)
finally:
    tmp.unlink(missing_ok=True)
thegn = nested_manors(w)[0]
corvee = lambda log: next(e["corvee_pool"] for e in log if e.get("date") == "Y1-M08")
print("corvee Y1-M08 granted/baseline", corvee(w.manor_log), corvee(base.manor_log))
print("gafol to root", sum(1 for e in w.ledger.entries if e.reason == "gafol"
      and e.src_id == "household:hh_02" and e.dst_id == "settlement:hill_court"
      and str(e.date) >= "Y1-M06"))
print("gafol to thegn", [(str(e.date), e.amount) for e in w.ledger.entries
      if e.reason == "gafol" and e.src_id == "household:hh_02" and e.dst_id == thegn.stock_id])
print("thegn grain", w.get_stock(thegn.stock_id).amounts.get("grain"))
print("hh_02 manor", w.households["hh_02"].manor_id, "hunger", w.households["hh_02"].hunger_days)
print("depth", manor_depth(w, thegn), "second", grant_thegn(w, "hh_retinue_p2", ["t_05_01"], ["hh_03"]))
print("matter delta", f"{w.ledger.delta(w.total_matter()):.9f}")
PY
```

Вывод: `corvee Y1-M08 granted/baseline 96.0 108.0`; `gafol to root 0`; `gafol to thegn
[('Y1-M11', 4.0)]`; `thegn grain 22.4`; `hh_02 manor manor_hh_retinue_p1 hunger 0`;
`depth 1 second None`; `matter delta 0.000000000`. Конец прогона — Y2-M07; `mustered=True`
в логе `manor_log` за Y2-M06.

- Баланс амбара тэна: приход `harvest_grain` 18.4 + `straw` 2.944 + `gafol` 4.0 = 25.344,
  расход 0, остаток 25.344, residual `0.000000000`.
- Нет двойного кормления: 36 назначений `board`, `double-fed=0`, `board` для hh_02 = 0.
- Нет двойного счёта дней: у hh_02 списано 12.0, корневой пул 0.0, пул тэна 12.0.
- Урожай после grant: t_05_02 → 26 `process` в амбар тэна; t_06_02 → 56 в замок;
  до grant t_05_02 → 18 в замок; нарушений 0 (потеря в `sink:waste` исключена).
- depth=1, parent=manor_hill, `grant_ids=[grant_001]`; второй grant → `None` +
  `grant_thegn_rejected`.
- **Дыра: mustered «по своему стоку, не по замку» не доказан.** Все 18 переводов `board`
  двору держателя hh_retinue идут из `settlement:hill_court`; `hh_retinue.manor_id=manor_hill`
  (держательский двор не переведён на книгу тэна при grant). Контрпример (команда 4):
  замок 0, амбар тэна 100 → mustered False, голод держателя 1. Репо-тест
  `test_manor_entity.test_mustered_by_own_stock_not_castle` доказывает лишь чтение
  `hunger_days` (правит поле напрямую), а не источник корма.

Команда 4 (контрпример mustered):

```bash
PYTHONPATH=sim/src python3 - <<'PY'
import yaml
from pathlib import Path
from hillcourt.scenario import load_scenario
from hillcourt.engine.tick import run_month
from hillcourt.runner import _apply_script_entry
from hillcourt.engine.manor import nested_manors, update_musters
S = Path("design/scenarios/v0_hill_and_salt.yml")
data = yaml.safe_load(S.read_text(encoding="utf-8"))
data["script"] = [{"at_month": 6, "action": "grant_thegn", "person": "hh_retinue_p1",
                   "tiles": ["t_03_01", "t_05_02"], "households": ["hh_02"]}]
tmp = S.parent / "_tmp_review2.yml"
tmp.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
try:
    w = load_scenario(tmp, seed=1729)
    for entry in w.script:
        _apply_script_entry(w, entry)
finally:
    tmp.unlink(missing_ok=True)
thegn = nested_manors(w)[0]
holder = w.households[w.persons["hh_retinue_p1"].household_id]
w.get_stock("settlement:hill_court").amounts["grain"] = 0.0
w.get_stock(holder.stock_id).amounts["grain"] = 0.0
w.get_stock(thegn.stock_id).amounts["grain"] = 100.0
holder.hunger_days = 0
update_musters(w); before = thegn.mustered
run_month(w); update_musters(w)
print("holder manor", holder.manor_id, "castle 0 / thegn 100 ->",
      "before", before, "after", thegn.mustered, "holder hunger", holder.hunger_days)
PY
```

Вывод: `holder manor manor_hill castle 0 / thegn 100 -> before True after False holder
hunger 1`.

## Вердикт: доказано / осталось ложью

- **A1 — «соль едет»**: **12 transfer соли** (7 `caravan_load` + 5 `caravan_unload`) на сиде
  1729, 0 телепортов household→castle, 0 `external_in`; замковая соль 15.6/15.6/15.6/6.074.
  Доказано.
- **A2 — доказано**: один seed собирает и черты, и потоки RNG; гибрида нет.
- **A3 — доказано** по критерию (Pack-путь, прибытие, tied, гибель без delete, delta 0);
  оговорка о дошедшем дворе с неснятым `left_at` — не ложь, но дыра.
- **A4 — доказано**: лог, Report, неизменность материи; runner печатает действия с датами.
- **B — доказано, кроме «mustered по своему стоку, не по замку»: осталось ложью приёмки**
  (числа и команда 4 выше). Остальные пункты B (амбар, гэфоль, барщина, урожай, depth≤1,
  второй тэн, материя) доказаны.
- Наблюдение вне A/B: `legal/manor.py` (`render_labor`/`demesne_labor_pool`) — мёртвая вторая
  правда манора, в тик не входит, используется только `test_manor_axes` (B2 backlog).

## Что запрещено следующим агентам (до закрытия найденного)

1. Ссылаться на `test_manor_entity.test_mustered_by_own_stock_not_castle` как на
   доказательство независимости `mustered` от замка: тест правит `hunger_days` напрямую и не
   проверяет источник корма.
2. Объявлять «mustered по своему стоку» выполненным, пока двор держателя (`hh_retinue`)
   остаётся на книге `manor_hill` при `grant_thegn`: его паёк (18 переводов) идёт из
   `settlement:hill_court`.
3. Править/ослаблять механизмы A1–A4 (Pack-соль, seed, household_move, player_actions) без
   нового ADR: проверки зелёные, числа воспроизводимы.
4. Использовать `legal/manor.py` в тике как вторую правду манора (B2) — код мёртв, но путает.
5. Коммитить фиксы по этому отчёту без отдельной задачи владельца (Implementer/Legal):
   Critic не правит код, отчёт — вход для решения.

## Проверка (команда)

```bash
bash sim/run_tests.sh
```

Ожидание: `Ran 133 tests ... OK`. Плюс команды 1–4 из разделов A1/A2/B — числа должны совпасть
с выписанными. Полные harness-скрипты: `/tmp/opencode/check_a1.py`, `check_a2.py`, `check_a3.py`,
`check_a3b.py`, `check_a4.py`, `check_b.py`, `check_b2.py` (эфемерны; воспроизводятся по
inline-командам отчёта).
