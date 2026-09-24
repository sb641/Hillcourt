# 0015. Review фазы H/I: излишек, гибель воза, стол второго тэна (Critic)

## Контекст

Critic проверяет ворота H1–H3 (буфер еды, живая телега, гибель воза, отчёт об
обозе, недоверие к истине назначения) и I1/I3 (второй тэн как хозяйство,
фьеф держит держателя) по заданию ночи. Репозиторий — «Initial commit»
`bdeb385`; рабочее дерево не закоммичено (` M README.md`; `?? AGENTS.md client/
design/ docs/ sim/ tools/`). Critic код не правил; его зона — только этот отчёт.
Старые ADR 0001–0014 не редактировались.

**Важно (процесс).** В `design/review/backlog_v0.md` разделов **H и I нет**:
файл кончается на G6. Контракт H/I брался из docstring-ов модулей и тестов
`test_caravan_h.py`, `test_caravan_report_h.py`, `test_shire_world.py`,
`test_manor_multi_grant.py`, `test_fief_holder_cannot_leave.py`. Это не окно
приёмки, а самодельный договор: следующему агенту H/I надо закрепить в бэклоге
или принять эти тесты за канон (см. «Что запрещено»).

**Заморозка.** Проверяемые файлы сняты в `/tmp/opencode/hc_h_i_snap`
(`cp -a`, эфемерно) и не менялись за ревью — sha256 после == до:

```
caravan.py       713b701435f77f7b97fc7cf1fba99e94eabb3e1221f7b85cd523df917477c104
briefing.py      7b6352d84838adc902f0b9d3a72f21995cecbba86ac2c096f6937f1ed761b2e0
travel.py        647ce4f8f4a6b7f4cc7c22ece66b2e737b311219121da5de1afc8478e71180c5
regimes.py       b9e0fad54aa3d15aa40a2104f29c188e606054165d9e4e7b33db1243755d354f
engine/manor.py  571f3ec93d634688e81076107915080364d8bb3381a2317fd031dc704e35aa53
v0_shire.yml     5a656eca41fca7214f20cabb6ce005403e8776c5234b787bbf81fccc8ca85283
v0_two_sett.yml a7cd9cb5bd0ddf3e3527d89cd30d240d9e0d4981db3fba5338c96b7f41ef5289
spawn_rules.yml  92a84386eaa0a0ac852fb8edd3fafaf656d397862dc3230d5a93b27563026e94
```

`git status --porcelain` до и после ревью идентичен, sha256 вывода
`f0be12662f5a1acf9e34684bfd81fa990aca6a3f02fe60e9e2e490095e07decd`.

Метод: независимые harness-скрипты `/tmp/opencode/h_check_world.py`,
`h_check_reports.py`, `h_check_mechanics.py`, `h_check_desttruth.py`,
`i_check_world.py`, `i_check_long.py` (запуск
`PYTHONPATH=/home/sb/projects/hillcourt/sim/src python3 <скрипт>`), плюс
буквальные команды `design/scenarios/ACCEPTANCE.md`.

## Что проверено и чем (команды, числа)

### 0. Тесты

`bash sim/run_tests.sh` → `Ran 217 tests in 40.140s ... OK`. Литерал
`ACCEPTANCE.md` «161 тестов» устарел (окно «161+» по знаку держится, факт 217).
Самопроверка документа печатает `ACCEPTANCE_OK`.

### H2. Восемь прогонов: 4 сида × 60 мес `v0_two_settlements` (h_check_world.py)

| seed | salt→hill | transfer'ов | grain→ash | голод_мес | delta | grain ratio |
|---|---|---|---|---|---|---|
| 1729 | 11.6000 | 5 | 23.788 | 314 | -1.64e-11 | 0.7208 |
| 42 | 11.6000 | 5 | 48.923 | 257 | -2.91e-11 | 0.9419 |
| 7 | 11.6000 | 5 | 34.600 | 276 | -2.36e-11 | 0.9611 |
| 99 | 2.0741 | 1 | 56.810 | 307 | -3.73e-11 | 0.8173 |

- `transfer>0` по соли и зерну на всех 4 сидах; `delta≈0` (порядок 1e-11).
- `ratio = delivered/carried` из `barter_memory` для
  `hill_court->ash_village:grain` **< 1.0 на 4/4 сидах** (окно «≥2» перекрыто).
- Голод жив: 257–314 голодных месяцев.
- Replay: seed 1729 `hash_equal=True` (`fe8908dba310…`), seed 42 `True`
  (`affee56b3a42…`).

**Знание о дальней деревне — только Report, сток ≠ знание.** 36 мес seed 1729:
19 Report'ов об обозе с `grain_approx` по клетке `t_02_07` (silence — 2/4/4/1 на
сидах), отчёт шумит и не равен истине: 1729 — 165.6 против фактических 145.48,
42 — 100.1 против 140.01, 7 — 91.3 против 135.45, 99 — 144.5 против 110.60. В
`build_player_view` 142 записи, из них 11 — про зерно дальней деревни. Подмена
ТОЛЬКО стоков `ash_village` ПОСЛЕ прогона (9999/9999/9999) не меняет view:
`view_a == view_b: True`. У `ReportView` нет полей истины
(`tile/household/stock/amounts/world/truth` — отсутствуют).

### H2/H3. Отчёт о прибытии и гибель воза (h_check_reports.py)

36 мес seed 1729: 7 arrival-Report'ов (`source=caravan`, есть `carried`); у всех
`carried>0`, `lost=0`, `delivery_date==event_date`, а
`max |ratio − delivered/carried| = 0.000e+00`. Примеры: `Y1-M04
salt_village→hill_court: вёз 2.13, доехало 2.13 (доля 1.00)`; `Y1-M05
hill_court→ash_village: вёз 9.00, доехало 8.65 (доля 0.96)`.

Гибель воза (`resolve_pack_loss`, `rng.hazard=rng.world=0`): загружено 9.000,
фураж съеден 0.350, в стоячий сток клетки `t_02_07` легло **8.650 = 8.650**
(загруженное без фуража), в возе осталось `0.000000000`, `status=lost`,
silence-Report ровно 1, `delta=0.00e+00`. «Весь груз» здесь = груз после
путевого фуража; материя нулевая по балансу.

### H4/H5. Излишек, телега, недоверие к назначению

- **Буфер** (`h_check_mechanics.py`, seed 1729): `need_buffer = 16.8000`
  (2.0 × месячная нужда живых дворов холма). При зерне `buffer−1` воз не
  создан (`grain packs=0`, счётчик `caravan_below_buffer=1.0`); при
  `buffer+4` погружено ровно **4.0** (только излишек).
- **Телега**: масса на старте 1.0; 11 рейсов, ёмкость первого с телегой
  **1.5**; `cart_broken=1.0`, масса 0.0, в `sink:waste` ровно 1.0,
  `origin_has_cart=False`, `delta=0.00e+00`. На естественных 60-мес прогонах
  `v0_two_settlements` телега НЕ ломается (масса 0.7–0.8, `cart_broken=None`) —
  поломка доказана только синтетическим правилом-зондом.
- **Истина назначения** (`h_check_desttruth.py`): контроль и опыт (стоки ash
  заменены на 9999) дают одинаковую подпись возов
  `[('caravan_caravan_grain_to_ash_0001_04', (('grain', 9.0),))]`,
  `decisions identical: True`.

### I6/I7. `v0_shire`: два тэна, отказ третьего, revoke (i_check_world.py)

- `households=34` (окно 32–36), поселений 17.
- Оба пожалования M6/M18: depth 1, свои амбары `manor:manor_hh_retinue_p1` и
  `manor:manor_hh_03_p1` (в `world.stocks`, `owner_kind=manor`), домены
  `t_05_02` и `t_01_10`, двор держателя в книге (`hh_retinue`, `hh_03`),
  `left_at=None`.
- Третий grant → `None`, запись `grant_thegn_rejected reason=grants_limit`;
  `root.grant_ids=[grant_001, grant_002]`.
- **Второй тэн не write-only**: из его амбара 7 `board`-переводов держателю на
  15.400, в его амбар 1 `gafol` на 4.000. Голод держателя после M18 и
  `mustered=True` зафиксированы у обоих тэнов. Корневой `corvee_pool` в жатву
  падает: Y1-M09 **240.0** → Y2-M09 **216.0** (после M18).
- **revoke второго**: `revoke=True`, сток `manor:manor_hh_03_p1` снят, слияние в
  корень через Ledger (2 проводки), корневое зерно 0.091 + 0.100 = 0.191;
  `hh_05`/`hh_03` → `manor_hill`, `t_01_10`/`t_04_10` снова в корне.
  Первый тэн не тронут (`tile_ids`/`household_ids`/сток совпали), `delta=9.09e-12`.

### I8. `v0_shire` 60 мес × 4 сида (i_check_long.py)

| seed | живых | голод_мес | delta | replay |
|---|---|---|---|---|
| 1729 | 26 | 535 | 1.00e-11 | True |
| 42 | 24 | 476 | 1.18e-11 | True |
| 7 | 25 | 514 | 1.36e-11 | True |
| 99 | 25 | 538 | -9.09e-12 | True |

Не падает, `delta≈0`, replay совпадает, голод жив.

### I9. Регрессия A1 + G1/G2 коротко

- Соль `v0_two_settlements` 36 мес: **11.60 / 11.60 / 11.60 / 2.0741**; 60 мес —
  те же числа. `delta` 36 мес ~1e-11.
- `v0_hill_and_salt` 36 мес seed 1729: замковая соль **15.6**,
  `delta=-2.27e-12`.
- Буквальные команды ACCEPTANCE: CMD1 — соль 15.6, дельта `-0.000000`, живых 12;
  CMD2 (60 мес seed 42) — живых **17** (окно 14–18), соль 15.6, голодных
  месяцев **272**, дельта `-0.000000`.
- **G2**: grant, сток держателя 0, замок 0, амбар тэна 100 → `board` из
  `manor:manor_hh_retinue_p1` **3.06**, `hunger_days=0`, `mustered=True`,
  амбар 97.940; сохранение материи за месяц `-1.93e-12`.
- **G1**: держатель фьефа с `hunger=5` и недоимкой ×100 — `left_at=None`,
  клетка не сменилась, новых `Pack` 0 (фьеф держит).

## Вердикт: H — закрыт, I — закрыт

- **H — закрыт.** Излишек грузится только выше буфера (16.8/4.0), телега
  изнашивается и ломается с нулевым балансом, катастрофа кладёт весь остаток
  груза в клетку со `silence`/`status=lost`/delta 0, arrival-Report несёт
  `carried/delivered/ratio/lost` и `ratio == delivered/carried` до float,
  dispatch не читает стоки назначения (контроль == подмена), знание о дальней
  деревне — только Report, сток≠знание, 4 сида: transfer>0, ratio<1 (4/4),
  голод>0, delta≈0, replay совпадает.
- **I — закрыт.** `v0_shire` 34 двора, два тэна depth 1 со своими амбарами и
  доменами, дворы держателей в книгах, третий grant отклонён, revoke второго
  возвращает в корень не задевая первого, второй тэн кормит держателя из своего
  амбара (`board` 7×15.4) и вносит `gafol` (4.0), корневой `corvee_pool` падает
  240→216, держатель не уходит (`left_at=None`), `mustered=True` после M18,
  60 мес × 4 сида без падений с delta≈0 и replay.
- **«Откат I» не объявляется:** второй тэн не write-only и держатель кормится из
  своего амбара — оба признака из STOP-условия отсутствуют.

Честные ограничения (не отменяют вердикт):

1. В бэклоге нет разделов H/I — окно приёмки восстановлено из тестов; это
   процессная дыра, а не провал модулей.
2. `ACCEPTANCE.md` по-прежнему ссылается на «161 тест» и `state_hash
   09db75ca0b17…`; факт — **217 OK**, хеш CMD2 seed 42 `affee56b3a42…`
   (0014-е расхождение не закрыто).
3. Поломка телеги доказана зондом, не естественным 60-месячным прогоном.
4. Падение корневого `corvee_pool` скромное: 240→216 (−10 %), но знак верный.

## Что запрещено следующим агентам

1. Ослаблять/ломать H1–H3: буфер `need_buffer_months` (16.8/излишек 4.0),
   износ/поломку телеги, `resolve_pack_loss` (весь груз в клетку, `lost`,
   `silence`, delta 0), arrival-Report (`ratio == delivered/carried`) — без
   нового ADR; держать зелёными `test_caravan_h`, `test_caravan_report_h`.
2. Читать истину назначения в решении обоза (стоки/клетки ash) вместо
   `barter_memory` и доставленных `Report`; подмена стоков назначения не смеет
   менять решение `dispatch_caravans`.
3. Отдавать игроку сток/истину места: знание о дальней деревне — только
   `Report`; `ReportView` без полей истины.
4. Считать второй тэн доказанным без проверки источника пайка: `board` должен
   идти из `manor:<id>`, `revoke_thegn` — сливать амбар в родителя, первый тэн
   оставаться нетронутым; фьеф держит держателя (`can_leave=False`, I3).
5. Создавать третьего тэна / depth > 1 / второй тэн вне лимита
   `thegn_grant_limit` в `v0_shire` без нового ADR (backlog G: «50/4 — не
   сейчас»).
6. Ссылаться на литералы `ACCEPTANCE.md` как на факт: «161 тест» и
   `state_hash 09db75ca0b17…` не воспроизводятся (факт 217 OK, `affee56b3a42…`).
7. Редактировать ADR 0001–0014; пересмотр — новым ADR со ссылкой.

## Проверка

```bash
bash sim/run_tests.sh
PYTHONPATH=sim/src python3 -m hillcourt.runner --scenario design/scenarios/v0_hill_and_salt.yml --months 36 --seed 1729
PYTHONPATH=sim/src python3 -m hillcourt.runner --scenario design/scenarios/v0_two_settlements.yml --months 60 --seed 42
PYTHONPATH=sim/src python3 -m unittest -v \
  sim.tests.test_caravan_h sim.tests.test_caravan_report_h sim.tests.test_barter_memory \
  sim.tests.test_shire_world sim.tests.test_manor_multi_grant sim.tests.test_fief_holder_cannot_leave
```

Ожидание: `Ran 217 tests ... OK`; CMD1 — соль 15.6, дельта `-0.000000`;
CMD2 — живых 17 (окно 14–18), соль 15.6, голод>0, дельта `-0.000000`;
H-тесты — буфер 16.8/излишек 4.0, ratio<1 на 4/4 сидах, `caravan_lost`;
I-тесты — два тэна depth 1, третий grant `grants_limit`, `board`/`gafol` из
`manor:<id>`, revoke в корень. Независимые harness-скрипты (`/tmp/opencode/
h_check_*.py`, `i_check_*.py`) эфемерны; числа воспроизводятся на ревизии с
хэшами из раздела «Контекст».
