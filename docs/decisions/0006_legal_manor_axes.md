# 0006. Оси манора: пресеты, бандлы, наделы (Legal)

## Контекст

Догон к сессии Legal/Info: право v0 описано слишком абстрактно («может уйти / должен ренту»),
без англосаксонско-домесдеевской конкретики. Источник — ранние англосаксы + Domesday как гибрид.
Свободность **юридическая**, а не «никому не должен»: свободный держит землю за службу/ренту.
Раб — не виллан; виллан — не раб. Зарплаты крестьянам нет. Три валюты повинности: **трудодни,
гэфоль (натура/пенс), паёк**. Экономику зерна и yield не трогаем.

## Решение

### Три оси вместо классов
`Household`/`Person` несут `personal_status ∈ {free,tied,slave}`,
`land_relation ∈ {secure_holding,tenement,landless}`, `obligation_bundle` (id из
`obligations.yml`). Пресет — ярлык из `legal_statuses.yml`, а не тип в коде.

### Пресеты (ровно 8, тройка + права)
| preset | personal_status | land_relation | obligation_bundle |
|---|---|---|---|
| `holder` | free | secure_holding | holder_none |
| `thegn` | free | secure_holding | thegn_service |
| `sokeman` | free | secure_holding | sokeman_rent |
| `geneat` | free | tenement | geneat_service |
| `villein` | tied | tenement | villein_full |
| `cotter` | tied | tenement | cotter_monday |
| `free_landless` | free | landless | free_landless_none |
| `slave` | slave | landless | slave_ration |

Права в каталоге: `can_leave`, `marriage_needs_permission`, `court` (manor|public),
`wergeld`, `inheritance`, `can_sell_land`, `can_be_taken_on_expedition`, `ploughs`, `land_kind`.
Раб отличается от виллана флагом `wergeld: false` (у виллана `true`) и отсутствием своего
двора-агента. `thegn`/`holder` не пашут сами (`ploughs: false`).

### Бандлы повинностей (три валюты, unit обязателен)
`villein_full` (барщина по календарю, harvest_extra, boon_days_cap, geld_michaelmas,
in_kind_martinmas, sow_demesne_acres), `cotter_monday` (барщина по календарю,
harvest_extra, almost_no_cash), `geneat_service` (callout по календарю, cartage, messenger,
harvest_help, food_for_lord), `sokeman_rent` (fixed_rent_grain_or_pence, rare_labor,
may_leave), `slave_ration` (no_holding, eats_lord_board, full_control), `thegn_service`
(no_farm_labor, musters_men, self_armed, bridge_burh_fyrd), `holder_none`,
`free_landless_none`. Сезонные трудодни бандл не дублирует — берёт из `calendar_v0`.
Числа — СТАРТ из Rectitudines, у каждого числового условия `unit`
(`labor-day`/`acre`/`penny`/`in-kind`) и `note`, если к месячному тику не сведено.

### Земля манора в правилах
`land_regimes.yml`: `demesne | villein_tenement | cotter_plot | free_holding | waste |
reserved_wood` (+ общий `tenement` и выводимый `foreign`). `demesne.requires_labor_days: true`
и `feeds_household: false`; наделы кормят двор. Домен и надел не смешивают стоки.

### Код и карта вызовов из тика
Код — только `sim/src/hillcourt/legal/` (`regimes.py`, `bundles.py`, `manor.py`, `flight.py`,
`actions.py`), каталоги Legal. Вызовы:
- `scenario.load_scenario` → `legal.bundles.materialize_obligations` (сводит бандл в
  `Obligation`: сезонная барщина → `duty_days` через `legal.calendar`; `fixed_rent_grain_or_pence`
  → натуральная рента). Несведённые условия остаются данными.
- `phase_obligations` → `legal.calendar.seasonal_labor_days` (месячный долг по календарю).
- `phase_migrate` → `legal.regimes.can_leave` (tied не уходит легально).
- `legal.actions.send_party` → `legal.regimes.can_be_sent` (ось expedition).
- `legal.manor.render_labor` — трудодни двора в пул домена (`world.stats["demesne_labor_days"]`),
  не из воздуха; в тик пока не подключён (нет контура домена).
- `legal.flight.attempt_flight` — бегство tied как отдельный риск; в тик не подключён.

### Минимальный патч онтологии (список)
1. `Household`: +`personal_status`, +`land_relation`, +`obligation_bundle`.
2. `Person`: +`personal_status`, +`land_relation`, +`obligation_bundle`.
3. `LegalStatus`: новый набор полей-пресета (см. выше), вместо `owes_rent/can_be_sent/may_hold_land`.
4. `LandRegime`: +`requires_labor_days`, +`feeds_household`.
5. Новый `ObligationBundle`.
Новые сущности `Person`/`Household`/`Tile` не создавались.

## Альтернативы

- **8 классов-пресетов в коде.** Отклонено: задача прямо запрещает; пресеты — данные.
- **Свести все бандлы к месячному зерну.** Отклонено: это «налог со всего» и потеря трёх валют;
  сводим только трудодни и одну натуральную ренту, остальное помечено `note`.
- **Оставить `owes_rent`/`may_hold_land` отдельными флагами.** Отклонено: это дублирование осей;
  флаги выводятся из `land_relation`/`obligation_bundle`.
- **Смешать домен и надел в один сток.** Отклонено: `requires_labor_days`/`feeds_household`
  разведены в правилах.
- **Подключить бегство в `phase_migrate`.** Отклонено в v0: тест «виллан не уходит легально»
  должен быть детерминированным; бегство — отдельный вызов.

## Последствия

- `legal_statuses.yml` переписан на 8 пресетов; `scenario.yml` назначает их дворам, у
  `hh_court` двое рабов (`slaves: 2`, рты при стоке лорда, своего стока нет).
- `obligations.yml` — бандлы; секция `obligations` пуста (шаблоны-доли выведены).
- `world.state_hash` покрывает оси `personal_status/land_relation/obligation_bundle`.
- Тесты: `test_manor_axes.py` (5 требуемых) и обновлённый `test_legal_regimes.py`.
- Приёмка: `test_manor_axes.py` зелёный — виллан не уходит, сокмен уходит, трудодни идут в пул
  домена и не берутся из воздуха, раб ест из стока лорда, пресеты читаются из YAML.
- `test_household_economy.py` (Economist) остался зелёным: `hh_01` = сокмен с рентой 0.6.

## CALENDAR: сезонная барщина (уточнение)

Барщина не плоская «2 дня всегда». Календарь работ — часть legal-бандла:
`design/catalogs/calendar_v0.yml`, месяцы 1–12. Бандл ссылается на календарь
(`calendar: calendar_v0`), а не дублирует 12 копий чисел. Дневного календаря святых в коде нет.
Перевод недель в единицы тика один: **`month_days = week_days * 4`** (`WEEKS_PER_MONTH`).
`geneat`/`sokeman` недельных дней не имеют — только `callout` (=4 трудодня за вызов).
`slave` — `always`, сезонного нуля нет. `free_landless` — `hire_demand low|high` (наём, не долг).

### Таблица 12 месяцев × пресет → labor-days в тике (base, без помоги)
| мес | season | demesne_work | villein | cotter | geneat | sokeman | slave | boon_allowed | sow_acres |
|---|---|---|---|---|---|---|---|---|---|
| 1 | winter | wood_flock | 8 | 4 | 0 | 0 | 20 | нет | нет |
| 2 | winter | wood_flock | 8 | 4 | 0 | 0 | 20 | нет | нет |
| 3 | plough | plough | 12 | 4 | 0 | 0 | 20 | нет | нет |
| 4 | plough | plough | 12 | 4 | 0 | 0 | 20 | нет | нет |
| 5 | plough | weed | 12 | 4 | 0 | 0 | 20 | нет | нет |
| 6 | hay | hay | 8 | 4 | 4 | 4 | 20 | да | нет |
| 7 | hay | hay | 8 | 4 | 4 | 4 | 20 | да | нет |
| 8 | harvest | harvest | 12 | 12 | 4 | 0 | 20 | да | нет |
| 9 | harvest | harvest | 12 | 12 | 4 | 0 | 20 | да | нет |
| 10 | sow_winter | thresh | 8 | 4 | 0 | 0 | 20 | нет | да |
| 11 | sow_winter | thresh | 8 | 4 | 0 | 0 | 20 | нет | да |
| 12 | winter | idle_court | 8 | 4 | 0 | 0 | 20 | нет | нет |

Помочи (bene): в месяце с `boon_allowed` лорд берёт `extra_week_days × 4`
(villein +4 в мес 6–9; cotter +4 в мес 8–9) до `boon_days_cap` бандла. Это не бесплатно
юридически: счётчик `boon_used_this_year` (tension_flag). Счастье не моделируется.
Гэфоль-пахота: в месяце с `sow_demesne_acres` (`legal.calendar.sow_demesne`) зерно двора
уходит в сток домена через `Ledger.transfer`; урожайность не считаем — только график долга.

Тесты этого держат: `test_manor_axes.py` — виллан зимой (8) меньше жатвы (12); коттер в жатве
(12) больше января (4); генеат без `base_week_days`, только callout; у раба нет сезонного нуля;
бандлы ссылаются на календарь, а не дублируют числа; помога ограничена cap и считается;
гэфоль-акры двигают зерно двор→домен; tied не уходит зимой.

## Что запрещено следующим агентам

1. Вводить `if class == "Knight"`/`Explorer` или классы под имена пресетов; пресет — только YAML.
2. Плодить сущности Person/Household/Tile заново или добавлять 9-й пресет без ADR.
3. Крутить экономику зерна/yield под предлогом «сведения» бандлов; числа — seed из Rectitudines.
4. Смешивать сток домена и надела или создавать трудодни «из воздуха».
5. Вводить эрлов, гельд короны на всю Англию, коммутацию XII века как систему.
6. Ломать шерифа/известия (`info/`, `news/`) и чужие зоны (`economy/`).

## Проверка

```bash
bash sim/run_tests.sh
PYTHONPATH=sim/src python3 -m unittest discover -s sim/tests -p 'test_manor_axes.py' -v
```

Критерий: 65+ тестов зелёные; `test_manor_axes.py` держит все пять требований (виллан не
уходит легально; сокмен уходит; трудодни в пул домена, не из воздуха; раб ест из стока лорда;
пресеты из YAML, в коде нет классов); `test_legal_regimes.py` и `test_household_economy.py`
зелёные; прогон 36 месяцев печатает датированные `Report` и дельту материи `0.000000`.
