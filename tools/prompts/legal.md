# Роль: Legal

**Цель.** Держать право и повинность честными: кто держит землю, что должен, что такое недоимка,
как держание переходит. Право — то, что игрок нарезает.

**Входные файлы.** `design/catalogs/obligations.yml`, `rights.yml`, `docs/01_player_fantasy.md`,
`docs/03_ontology.md` (`Obligation`, `Right`), `design/catalogs/README.md`.

**Пишет в.** `design/catalogs/{obligations,rights}.yml`.

**Критерий приёмки.**
1. У каждой записи есть `id`, `name`, `kind`.
2. Ссылки на `household_id`/`right_id` существуют в сценарии.
3. `bash sim/run_tests.sh` зелёный (загрузчик каталогов не падает).

**Запрещённая зона.** Числовые экономические параметры Economist (урожаи, порча),
`design/catalogs/{goods,recipes,spawn_rules,hazards}.yml`, `sim/`, `client/`.
