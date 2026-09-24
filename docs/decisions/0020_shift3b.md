# 0020. Смена 3b: паёк корня считается по нужде двора, канон смены 3 сдвинут фиксом `_board` (Scribe)

## Контекст

Смена 3b — точечный ночной фикс поверх смены 3 (ADR 0019), тот же репозиторий/чат,
21.09.2026. Ворота: не ломать A1, G2, J1, I3; не трогать yield/каталоги/сценарии;
третий тен и 50 дворов по-прежнему запрещены. ADR 0001–0019 не редактировались,
`design/scenarios/HASHES.txt` — история смены 2 и не переписывается. Роли: Implementer
(фикс `_board`, зонды), Legal (проверка, что вмешательство не нужно), Critic
(независимая проверка), Scribe (эта запись).

## Что проверено по пунктам (дыра была/не было → что сделали → число)

**0) SimRunner канон ДО патчей (baseline).**
`bash sim/run_tests.sh` — **230 OK**. `v0_hill_and_salt` 36/1729 — соль **15.6**, `delta
-0.000000`, hash `17613abe…`. `v0_two_settlements` 60/42 — живых **18**, голод **289**,
hash `09c25081…`. `v0_shire` 60/1729 — живых **27**, голод **568**, hash `3500373d…`.
`caravan_unload` соли → `settlement:hill_court` — **11.6**, дельта ±0.000000.

**1) Зонд G2 на КОРНЕ (Implementer). Дыра была.**
`_board` (economy/manor.py) платил по взрослым: `1.0 × 4 × буфер 1.02 = 4.08`, тогда как
`phase_consume` требует нужду ВСЕГО двора. `hh_court`
(`v0_hill_and_salt`) — 2 названных взрослых + 2 раба + 2 ребёнка: `need = 4×1.0 +
2×0.7 = 5.4`. При полном амбаре корня держатель голодал (`hunger 1`).
**Что сделали:** `_board` считает `monthly_food_need(world, hh)` (взрослый 1.0, ребёнок
0.7) × буфер против порчи. **Числа:** board **5.508**, hunger **0**; `board src =
settlement:hill_court` (root `Manor.stock_id`); чужая книга не кормит (полный амбар тэна
при пустом корне → голод честен); relief уже шёл по книге; yield/каталоги не тронуты.
**Зонд:** `test_root_barn_probe` — **4** теста, итог **234 OK**.

**2) Рычаги в логе (Implementer). Дыры нет.**
Все шесть рычагов пишут `player_actions` и двигают книгу. Числа: `ease_week_work` M3 →
`corvee_pool 176→40`, `worked 90→65` (boon в M3 запрещён календарём); `call_boon` M8 →
`boon_days 3.0`, пул `252→255`; `set_tile_regime` `t_01_10→reserved_wood` → спрос M3
`90→60`; `grant_tenement` `hh_02` на `t_01_10` → Right + режим + спрос `90→60`;
`grant_tool` `iron_share 1.0` → Ledger `grant_tool` castle→двор, delta 0; `grant_thegn`
→ `manor:<id>`, двор держателя в книге.
**GAP (не изобретали):** `revoke_thegn` есть в коде/логе, но нет в
`runner.ACTION_HANDLERS`; `revoke_tenure/set_rent_share/send_pack/send_messenger/patrol`
как действия игрока в коде отсутствуют.
**Тест:** `test_levers_log` — **+8**, итог **242 OK**.

**3) Legal. Не потребовался.**
Зонд не трогал `can_leave`; J1/I3 не ослаблены: корневой держатель и тэны по-прежнему не
уходят (`can_leave=False`, `left_at=None`), свободный `hh_06` уходит.

**4) Critic (независимая проверка).**
A1 жив (соль `15.6`, unload `11.6`, `external_in 0`, телепорт `0`); G2 корень (board
`5.508`/hunger `0`) и тэн (board `3.06`/hunger `0`, замок `0/999` идентичны); J1/I3 целы;
СТОП-сигнала нет; голод шира не маскирован (**556**, замок `0.0`, yield не тронут).
Тесты — **242 OK**.

**5) Канон ПОСЛЕ 3b (hash сдвинулся из-за фикса `_board`).**
`v0_hill_and_salt` 36/1729 — живых 12, голод **33**, hash
`ec8d7606f1206316b6c588d11044c860f58cc9d89056b00d458ca1994ef87a39`.
`v0_two_settlements` 60/42 — живых **18**, голод **284**, hash
`ee89adb0cbc78b791ca5f1e0f8318b126fb756c0dfdba73ba7841339719af365`.
`v0_shire` 60/1729 — живых **27**, голод **556**, hash
`e2a2f2fe1d3d861743d0da994e43adb789ceac23cb9d328b5ac41bfd67cfe01f`.
Соль **15.6** везде, `delta ±0.000000`. **Причина сдвига:** board кормит домохозяйство, а
не только взрослых; материя/урожай бит-в-бит прежние (hill материя **4282.085**).

## Вердикт

- **G2 на корне — ЗАКРЫТ.** `_board` платит по нужде всего двора (`monthly_food_need`),
  корневой держатель ест из своей книги (`settlement:hill_court`), чужой амбар его не
  кормит; голод честен при пустой своей книге.
- **Рычаги — ЛОГ И КНИГА ЛАДНЫ.** Шесть рычагов пишут `player_actions` и двигают учёт.
  GAP-список честен: `revoke_thegn` вне runner, пяти других действий в коде нет.
- **Legal — НЕ ПОТРЕБОВАЛСЯ.** J1/I3 целы, `can_leave` не трогали.
- **A1/J1/I3 — ЦЕЛЫ.** Соль `15.6`, unload `11.6`, `external_in 0`, телепорт `0`; СТОП нет.
- **Канон смены 3 сдвинут фиксом книги.** Новые хеши/числа (242 OK; two 18/284;
  shire 27/556; hill 12/33) фиксируются в `ACCEPTANCE.md` §6 записью 3b, а не подгонкой.
  `HASHES.txt` и канон смен 2–3 не переписываются.

## Последствия

- Пайок корня покрывает весь двор; двор игрока больше не голодает при полном амбаре корня.
- `state_hash` всех трёх миров изменился законно (паёк — перевод, не урожай); суммарная
  материя hill бит-в-бит прежняя (`4282.085`) — материя не создана.
- Открытые GAP рычагов (`revoke_thegn` вне runner; пять действий отсутствуют) остаются —
  каждый отдельная задача с новым ADR.
- Голод шира вырос до `556` — следствие честного пайка, не подгонки; yield не поднимался.

## Что запрещено следующим агентам

1. Ломать A1 (соль), G2 (книга корня/тэна), J1/I3 (`can_leave`) без нового ADR.
2. Поднимать yield / создавать материю, чтобы замаскировать голод шира `556`.
3. Переписывать `HASHES.txt` и канон смен 2–3; канон 3b меняет новый ADR, а не прогон.
4. Третий тен, 50 дворов / 4 тэна, биржа / глобальные цены, кузня / кожа / бой, Godot, LLM.
5. Редактировать ADR 0001–0019; 0020 — только добавление.

## Проверка

```bash
bash sim/run_tests.sh
PYTHONPATH=sim/src python3 -m hillcourt.runner --scenario design/scenarios/v0_hill_and_salt.yml --months 36 --seed 1729
PYTHONPATH=sim/src python3 -m hillcourt.runner --scenario design/scenarios/v0_two_settlements.yml --months 60 --seed 42
PYTHONPATH=sim/src python3 -m hillcourt.runner --scenario design/scenarios/v0_shire.yml --months 60 --seed 1729
PYTHONPATH=sim/src python3 -m unittest sim.tests.test_root_barn_probe sim.tests.test_levers_log -v
```

Критерий: `Ran 242 tests ... OK`; `v0_hill_and_salt` 36/1729 — голод 33, соль 15.6,
`delta -0.000000`, hash `ec8d7606…`; `v0_two_settlements` 60/42 — живых 18, голод 284,
соль 15.6, hash `ee89adb0…`; `v0_shire` 60/1729 — живых 27, голод 556, соль 15.6, hash
`e2a2f2fe…`; `unload` соли `11.6`, `external_in 0`, телепорт 0. Окна — в
`design/scenarios/ACCEPTANCE.md` §6 (смена 3b). Git status до/после записи ADR идентичен
(` M README.md`, далее untracked `AGENTS.md client/ design/ docs/ sim/ tools/`).
