# 0017. Ревизия J1/J2: корневой держатель закреплён, I3 цел, канон смены 2 заморожен (Critic)

## Контекст

Смена J1 (Implementer) закрывает дыру 3 из ADR 0016: двор игрока (`hh_court`,
корневой держатель) больше не уходит от голода, пока жив корневой манор
`manor_hill`. Смена J2 (Scribe) описывает канон: `design/scenarios/ACCEPTANCE.md`
получил раздел «Ревизия канона» со слотом смены 3, `design/scenarios/HASHES.txt`
объявлен историей смены 2 и не переписывается.

Critic не автор кода и код не правил. Проверка шла независимыми скриптами
(`/tmp/opencode/hillcourt/critic_0017/`) и каноническими командами. HEAD репозитория —
`bdeb385`; воркспейс по-прежнему не заморожен (`M README.md`, остальное untracked) —
та же процессная дыра, что в 0014/0015/0016. Baseline смены 3 до патчей J1 —
`/tmp/opencode/hillcourt/baseline_shift3.txt`. ADR 0001–0016 не редактировались.

## Что проверено и чем

**J1.1 — тесты.** `bash sim/run_tests.sh` → `Ran 222 tests in 128.929s`, `OK`.
В `HASHES.txt` канона смены 2 — 217; +5 тестов J1 (в т.ч.
`test_root_holder_cannot_leave`), провалов нет. Хвост h1-диагностики:
`grain->ash {1729: 23.788, 42: 48.923, 7: 25.95, 99: 51.697}`,
`salt->hill {1729: 11.6, 42: 11.6, 7: 11.6, 99: 2.074}`.

**J1.2 — корневой держатель.** `verify_j1.py` на `v0_hill_and_salt` seed 1729:
пресет `holder.can_leave=True`, но `can_leave(hh_court)=False` (держит `manor_hill`,
а не ярлык). После `hunger_days=5` + недоимка `100×due` → `phase_migrate`:
`hh_court.left_at is None`, клетка `t_01_01` не менялась, новых `Pack` — 0.

**J1.3 — I3 не сломан.** После `grant_thegn` `hh_retinue`: `can_leave=False`,
`phase_migrate` — no-op (`left_at None`, клетка та же, новых `Pack` 0). Контроли:
свободный не-держатель `hh_06` — `can_leave=True`, уходит (`left_at=Y1-M01`);
`hh_02` (виллан) — `can_leave=False`, не уходит.

**J1.4 — 60 мес `v0_two_settlements` seed 42 со script.** `hh_court.left_at is
None`. После Y3-M07 (месяц прежнего ухода) замок кормит двор: `board` из
`settlement:hill_court` в сток `hh_court` — 30 переводов, `eat` из его стока — 30.
`Ledger.delta(total_matter) = -1.27e-11` (печатается `-0.000000`). Replay двух
прогонов — `True`. Живых **18**, голод **289**, hash
`09c25081bbdc37a2daf8c2a01b10799cfe30ef4f388fbda4d8a87fa2c1ae3b4e` — совпал
с записью смены 3 в ACCEPTANCE. Runner-лог: `живых дворов 18 … голодных месяцев
289 … хеш 09c25081bbdc`.

**J1.5 — `v0_hill_and_salt` 36/1729 не изменился.** Salt **15.6**,
`delta -0.000000`, hash
`17613abecd0ccde6d6b8bcea884ac72b0ccaaf79af6845fd767968127e27ab39`, материя
4282.085 — буквально как в `baseline_shift3.txt`/`HASHES.txt`.

**A1 (соль) не сдвинут.** Ledger-скрипт ACCEPTANCE §3 (60 мес, seed 42):
`load 7`, `unload 5`, `unloaded_total 11.6`, `external_in 0`,
`teleport household→castle 0`. Соль не ушла в 4.0; канал
`household → pack → settlement:hill_court` цел.

**J2.1 — ACCEPTANCE §6.** Канон смены 2 (217 OK; hill 12/15.6/`17613abe…`;
two 17/272/`affee56b…`; shire 26/535/`abf199ef…`; `caravan_unload→hill_court`
11.6) совпадает с `baseline_shift3.txt` и `HASHES.txt`. Слот смены 3 присутствует,
окна живых/голода **14–18** (two) и **24–28** (shire) явно привязаны к ревизии
смены 2, смена 3 помечена «подтвердить после J3–J4». Самопроверка ACCEPTANCE
печатает `ACCEPTANCE OK`.

**J2.2 — HASHES.txt.** Содержит три канонические runner-команды, полные хеши,
replay (`repeat_equal=True`, `inprocess_matches_run=True`) и `unload 11.6`;
помечен как история («не переписывается»). Не изменялся: md5
`c37f9f2ddcbef47b75fd5a0de55beb69`.

**J2.3 — три канонические команды на текущем коде (смена 3):**

| команда | канон смены 2 (HASHES) | смена 3 (сейчас) | вывод |
|---|---|---|---|
| `run_tests.sh` | 217 OK | **222 OK** | +5 тестов J1 |
| `v0_hill_and_salt` 36/1729 | 12, 15.6, `17613abe` | 12, 15.6, `17613abe` | не изменился |
| `v0_two_settlements` 60/42 | 17, 272, `affee56b` | **18, 289**, соль 15.6, `-0.000000`, `09c25081` | сдвиг J1 |
| `v0_shire` 60/1729 | 26, 535, `abf199ef` | **27, 555**, соль 15.6, `0.000000`, `788e6d2e` | сдвиг J1 |

Replay two/42 через `run()` → `True` и hash `09c25081…`. Расхождения с
`HASHES.txt` — ожидаемый сдвиг J1 (корневой двор больше не уходит → он остаётся
едоком замка); канон смены 2 при этом не подгонялся.

**J2.4 — «looks better»/подгонка окон.** В ACCEPTANCE запреты на месте
(строки 106–107, 137, 141); окна смены 2 (14–18/24–28) не переписаны под текущий
прогон; смена 3 помечена как неподтверждённая.

## Вердикт

- **J1 — ЗАКРЫТ.** Корневой держатель закреплён механизмом уровня манора:
  `can_leave` смотрит `manor.holder_person_id`, а не ярлык, поэтому держит
  держателя и корневого (`manor_hill`), и вложенного (`manor_hh_retinue_p1`)
  манора. Голод + недоимка не уводят `hh_court`; новых `Pack` нет, клетка та же.
- **I3 — ЦЕЛ.** Держатель фьефа `hh_retinue` прикреплён; свободный `hh_06`
  уходит; виллан `hh_02` — нет.
- **Соль — 15.6, не 4.0.** A1 держится: `external_in 0`, `teleport 0`, выгрузка
  11.6. STOP-сигнал не сработал: **J3/J4 можно принимать**.
- **J2 — КАНОН СМЕНЫ 2 ЗАМОРОЖЕН.** `HASHES.txt` — история, ACCEPTANCE хранит
  канон смены 2 и слот смены 3. Полные окна смены 3 — только после J3–J4 новой
  записью, не подгонкой.

Остаточные ограничения (не провал J1, следующему агенту — отдельным ADR):

1. «Пустой корневой манор»: `can_leave` ключуется на `holder_person_id`. Зонд
   (`probe_residual.py`): при очистке слота держателя `can_leave(hh_court)` снова
   `True`. Смертности в v0 нет, поэтому в штатном прогоне слот не пустеет; дыра
   названа в 0016 и J1 её не закрывает.
2. h1-диагностика тестов сдвинулась на seed 7/99 (grain→ash 34.6→25.95 и
   56.81→51.697, ratio 0.8173→0.864), seed 1729/42 без изменений — следствие J1,
   не окно приёмки.
3. Время `run_tests.sh` — 128.9s против 41.0s в `HASHES.txt` (тяжёлые 60-мес
   прогоны в тестах J1). Не критерий, но зафиксировано.
4. 0016 в «Проверке» указывает окно shire 24–26, ACCEPTANCE §1/§6 — 24–28.
   Канонический лист — ACCEPTANCE; 0016 не редактируется (можно поправить новым
   ADR).

## Что запрещено следующим агентам

1. Переписывать/дописывать `HASHES.txt` и подгонять окна смены 2 под текущий
   прогон; окна меняет новый ADR, а не прогон.
2. Ломать `legal/regimes.can_leave` (манор держит держателя любого уровня) и I3
   без нового ADR; возвращать корневому двору уход от голода нельзя.
3. Редактировать ADR 0001–0016; 0017 — только добавление.
4. Маскировать уход/голод наймом, «богом» или правкой `hunger`/`arrears`;
   создавать материю из ничего.
5. Объявлять окна смены 3 каноном до J3–J4.
6. Чинить остаточные пункты 1–4 здесь же: каждый — отдельная задача с ADR.

## Проверка

```bash
bash sim/run_tests.sh
PYTHONPATH=sim/src python3 -m hillcourt.runner --scenario design/scenarios/v0_hill_and_salt.yml --months 36 --seed 1729
PYTHONPATH=sim/src python3 -m hillcourt.runner --scenario design/scenarios/v0_two_settlements.yml --months 60 --seed 42
PYTHONPATH=sim/src python3 -m hillcourt.runner --scenario design/scenarios/v0_shire.yml --months 60 --seed 1729
python3 /tmp/opencode/hillcourt/critic_0017/verify_j1.py
```

Критерий: `Ran 222 tests ... OK`; `v0_hill_and_salt` 36/1729 — соль 15.6, hash
`17613abe…`; `v0_two_settlements` 60/42 — `hh_court.left_at is None`, живых 18,
голод 289, hash `09c25081…`; `verify_j1.py` — 0 `FAIL`. Git status до/после
записи ADR идентичен (` M README.md`, далее untracked `AGENTS.md client/ design/
docs/ sim/ tools/`).
