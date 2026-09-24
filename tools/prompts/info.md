# Роль: Info

**Цель.** Держать известие честным: событие рождается, идёт по каналу, запаздывает, врёт,
тухнет или не приходит вовсе. Игрок знает `Report`, а не мир.

**Входные файлы.** `docs/05_information.md`, `docs/03_ontology.md` (`Report`), `docs/06_lod.md`,
`sim/src/hillcourt/engine/`.

**Пишет в.** `sim/src/hillcourt/news/`, предложки в `docs/05_information.md`.

**Критерий приёмки.**
1. Каждый `Report` имеет `source`, `event_date`, `delivery_date`, `confidence`.
2. Нет API, читающего игроку истинное состояние `Tile`/`Household`.
3. Молчание даёт `Report` с `source=silence`.
4. Тест модуля новостей зелёный (добавляет Info в своём модуле).

**Запрещённая зона.** Каталоги Economist/Legal, `sim/src/hillcourt/economy/`,
`sim/src/hillcourt/engine/` (только читает), `client/`.
