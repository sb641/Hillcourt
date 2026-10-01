# 06. LOD (уровень детализации)

## Назначение

Что считается подробно, что агрегатом, а что не считается вовсе. LOD защищает от «миллионов агентов».

## Правила

### Подробно (месяц)
В месячном контуре **каждый двор мира считается поштучно, а не только двор игрока** —
это правда о коде, и она хуже задуманного, но закон, расходящийся с кодом, хуже
отсутствующего закона. `phase_labor` (`engine/tick.py`) зовёт `economy/labor.py::work_month`,
а тот обходит `for hid in sorted(world.households)` и отрабатывает решения каждого двора
индивидуально: свои рецепты, свой `labor_days`, свой `Stock`, свои потери. На баронстве
это **150 дворов каждый месяц**, а не «двор игрока плюс агрегат».

Поштучно, раз в месяц:

- **все `Household` мира** — `main_action`/`minor_action`, труд, стоки, повинности;
- `Stock`, `Obligation` каждого двора, а не только стола: амбар лорда — отдельная строка
  ниже, потому что у него и читатель особый (подача и оброк);
- амбар и книга корневого манора — отсюда берутся `grant_grain` и `relief`;
- `Report` игрока.

### Подробно (день, только в пути/инциденте)
- `Pack.status = in_transit`: перемещение обоза/посылки по маршруту.
- Активный `Hazard` в клетке, где кто-то есть.

### Большая карта
100×100 `Tile` (10 000 гексов), реки, поселения, дворы, леса/луга и редкие маркеры
хранятся как сценарные данные, а не как 10 000 агентов. Подробный контур ленивый: только
поселения и `Settlement.works_tiles`.

**Сколько это гексов — измерено 29.09.2026, а не обещано.** Прежнее число «52 гекса» в
этом документе было **не измерением**: см. §Проверка, откуда оно взялось. На
`v0_barony_100` объединение `works_tiles` четырёх поселений — **89 гексов из 10 000,
то есть 0.89 %**:

| поселение | `works_tiles` |
|---|---|
| `hill_court` | 23 |
| `village_forest` | 30 |
| `salt_village` | 20 |
| `native_village` | 16 |
| **объединение (рельефы не пересекаются)** | **89** |

Про остальные гексы тик не шарит поштучно, но они и не «агрегат»: они просто не входят
в подробный контур и ждут, пока до них дойдёт рецепт на конкретной клетке. Оговорка честная:
за первый месяц тик на баронстве меняет стоячий сток на **106 гексах** — то есть 89
`works_tiles` плюс домен и гексы дворов; назвать «подробным контуром» только `works_tiles`
можно, назвать «всем, к чему тик притрагивается» — нельзя. Подключение всей карты к тику
помечено «в работе» (ADR 0073).

### Агрегат
- Дальние дворы: **месячный итог поштучно, а не агрегатом** — см. §«Подробно (месяц)».
  Слово «агрегат» в этом документе означает только то, чего в коде нет.
- Дальняя соляная деревня: **агрегат-LOD не реализован**. В v0 она создаётся обычными
  `Household` (`hh_salt_01`, `hh_salt_02` в `v0_hill_and_salt`; `hh_salt_001…018` на
  баронстве), проходит все фазы поштучно, как дворы у холма,
  и её соль едет в замок настоящим обозом-`Pack` (правило `caravan_visit`), а не телепортом
  и не только `Report` (см. `docs/02`, `docs/09_economy.md` §6). Замер 29.09.2026 на
  `v0_barony_100`, 24 месяца: `caravan_load` соли **115**, `caravan_unload` **7**,
  `external_in` **0**, телепорт `household→castle` **0**. Игрок по-прежнему видит
  обоз лишь через `Report`; агрегат для дальних дворов остаётся отложенным.

### Не считается никогда
- Pathfinding всех людей по всей карте каждый день.
- RTS-туман по клеткам.
- Поведение отдельного человека как управляемой единицы.

### Разведка
`Pack.purpose=scout` наблюдает только в момент `phase_travel`: свой гекс и маршрут дают
факт, кольцо 1 — физический бросок, кольцо 2 — слух; дальше 2 гексов нет. Это не обход
карты и не новый агент. `tower`, `Scout` и отдельный наблюдатель в v1 не существуют.
Без `scout`-Pack в гексе наблюдений нет. Формулировки вести (`rng_news`) не двигают физический
бросок (`rng_world`).

### Правило переключения
LOD повышается до дневного, **только** когда сущность входит в `in_transit` или в активный инцидент,
и понижается обратно по завершении. Число дневных сущностей ограничено размером `Pack`/`Hazard`.

## Проверка

```bash
# 1. Объём подробного контура: гексы мира и объединение works_tiles поселений
PYTHONPATH=sim/src python3 -c "
from pathlib import Path
from hillcourt.scenario import load_scenario
w = load_scenario(Path('design/scenarios/v0_barony_100.yml'), seed=1729)
per = {s.id: len(s.works_tiles) for s in w.settlements.values()}
det = set()
for s in w.settlements.values():
    det |= set(s.works_tiles)
print('гексов в мире', len(w.tiles), '| по поселениям', per)
print('объединение works_tiles', len(det), f'({100*len(det)/len(w.tiles):.2f} %)')"
# → гексов в мире 10000 | по поселениям {'hill_court': 23, 'native_village': 16,
#   'salt_village': 20, 'village_forest': 30}
#   объединение works_tiles 89 (0.89 %)

# 2. Месячный контур считает КАЖДЫЙ двор поштучно, а не только двор игрока
grep -n "for hid in sorted(world.households)" sim/src/hillcourt/economy/labor.py
PYTHONPATH=sim/src python3 -c "
from pathlib import Path
from hillcourt.scenario import load_scenario
from hillcourt.engine import tick
w = load_scenario(Path('design/scenarios/v0_barony_100.yml'), seed=1729)
import inspect
src = inspect.getsource(tick.phase_labor)
print('phase_labor зовёт work_month:', 'work_month' in src)
print('дворов в мире:', len(w.households), '— столько же строк в work_month')"
# → True; 150 дворов

# 3. Сколько гексов тик трогает на самом деле (первый месяц, сид 1729)
PYTHONPATH=sim/src python3 -c "
from pathlib import Path
from hillcourt.scenario import load_scenario
from hillcourt.engine.tick import run_month
w = load_scenario(Path('design/scenarios/v0_barony_100.yml'), seed=1729)
before = {t.id: dict(w.stocks[t.standing_stock_id].amounts) for t in w.tiles.values()}
run_month(w)
touched = [t.id for t in w.tiles.values()
           if dict(w.stocks[t.standing_stock_id].amounts) != before[t.id]]
print('гексов, у которых стоячий сток изменился за месяц:', len(touched))"
# → 106

# 4. Дневной контур не обходит world.persons
PYTHONPATH=sim/src python3 -m unittest sim.tests.test_layering -v

# 5. Кольца разведки и наблюдатель
PYTHONPATH=sim/src python3 -m unittest sim.tests.test_scouting sim.tests.test_scouting_reports -v
```

Критерий: команда 1 печатает `89 (0.89 %)`; команда 2 находит `for hid in
sorted(world.households)` в `economy/labor.py::work_month` и 150 дворов на баронстве —
это и есть «поштучно», а не «двор игрока»; команда 3 печатает `106`; команда 4 зелёная
(дневной контур трогает только `Pack` в пути или активный `Hazard`); команда 5 проверяет
локальные кольца, слух, `observer_id` и И-3. Ревизия Critic — на отсутствие циклов по всем
`Person`/`Household` в дне.

Замеры 1–3 сняты 29.09.2026 на дереве, чей `engine/tick.py` — `sha256 788ecb4712a2…`;
команды воспроизводимы, но при другой правке кода числа сдвинутся, и тогда §Правила
обновляет владелец `sim/src/`, а не подгоняет кто-то ещё.

**Известно красным на 29.09.2026 (не моя зона, не правил).** `test_scouting` падает
3 раза на `TestScoutingCanons::test_canon_households_hunger_salt_and_delta` по трём сценариям
(`v0_hill_and_salt`, `v0_two_settlements`, `v0_shire`) — это канон чисел, разошедшийся с
деревом после пересборки мира. `test_catalog_rules` падает 2 раза
(`test_clearing_is_not_a_one_month_job`, `test_stone_price_is_derived_from_the_clearing_recipe`) —
рецепт расчистки, зона Economist. Команда 5 приведена как **инструмент**, а не как
«зелёный тест»: считать её зелёной сегодня нельзя.

**Откуда взялось «52 гекса», которое стояло здесь до 29.09.2026.** Это число **не
воспроизводится ни на одном сценарии репозитория**: объединение `works_tiles` сегодня
равно 6 (`start_stand`), 89 (`v0_barony_100`), 7 (`v0_hill_and_salt`), 23
(`v0_large_village`), 5 (`v0_native_village`), 13 (`v0_shire`, оба `v0_ruin_*`), 11
(`v0_two_settlements`). Числа «52 гекса» нет ни в одном ADR и ни в одной записи Critic;
это была правдоподобная оценка, записанная как измерение. Заменено измеренным числом
с определением и командой.
