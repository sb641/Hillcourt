# Роль: Implementer

**Цель.** Каркас Python, тесты, runner, сценарии. Чтобы симуляция запускалась headless,
не знала спрайтов и не нарушала инварианты.

**Входные файлы.** `docs/00_constitution.md`, `docs/02_scope_v0.md`, `docs/03_ontology.md`,
`docs/04_tick.md`, `docs/06_lod.md`, `design/catalogs/*.yml`, `design/scenarios/v0_hill_and_salt.yml`.

**Пишет в.** `sim/`, `sim/tests/`, `tools/`, `design/scenarios/*.yml`.
Каталоги Economist/Legal — только читает.

**Критерий приёмки.**
1. `bash sim/run_tests.sh` зелёный: `test_matter_conservation`, `test_tick_runs`, `test_catalog_rules`.
2. Runner: `python3 -m hillcourt.runner --scenario ... --months N` крутит N месяцев и печатает
   датированные `Report`, а не состояние клеток.
3. Детерминизм: тот же `seed` → тот же результат.
4. Нет импортов Godot/графики в `sim/src/`; нет LLM в тике; дневной контур не итерирует всех `Person`.

**Запрещённая зона.** `design/catalogs/` (каталоги Economist/Legal), `docs/00`, `client/`.
