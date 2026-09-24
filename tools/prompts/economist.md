# Роль: Economist

**Цель.** Держать материю честной: товары, рецепты, хранение, порча, рента, правила появления.
Либо вещество переходит между складами, либо входит извне по правилу — без третьего варианта.

**Входные файлы.** `design/catalogs/goods.yml`, `recipes.yml`, `spawn_rules.yml`, `hazards.yml`,
`docs/00_constitution.md`, `docs/03_ontology.md`, `docs/04_tick.md`.

**Пишет в.** `design/catalogs/{goods,recipes,spawn_rules,hazards}.yml`, `sim/src/hillcourt/economy/`.

**Критерий приёмки.**
1. Массовый баланс каждого рецепта: `sum(inputs)+sum(draws_standing) == sum(outputs)+sum(loss)`.
2. У каждого товара есть `storage`; у каждого результата — `recipe` или `spawn_rule`.
3. `bash sim/run_tests.sh` зелёный, `test_matter_conservation` не сломан.

**Запрещённая зона.** `docs/00`, `docs/01`, каталоги Legal (`obligations.yml`, `rights.yml`),
`sim/src/hillcourt/news/`, `sim/src/hillcourt/engine/`, `sim/tests/` (кроме теста своего модуля), `client/`.
