# Hillcourt / «Двор на холме»

Пошаговый стратегический симулятор фронтира. Игрок — мелкий держатель на краю
бывшей великой машины (магия и империя мертвы ~600 лет). Он нарезает права на
землю и повинности, а не командует крестьянину «руби дерево». Домохозяйства —
автономные экономические агенты. Мир не появляется из дерева технологий.

Фаза 0 (этот репозиторий сейчас) — **логистика и каркас**, не игра:

- headless-симуляция на Python (`sim/`), без Godot и без ассетов;
- каталоги данных (`design/catalogs/`), сценарий v0 (`design/scenarios/`);
- документы-законы (`docs/`), ADR (`docs/decisions/`);
- `client/` (Godot) заморожен и в фазе 0 не трогается.

## Быстрый старт

```bash
bash sim/run_tests.sh                     # тесты (stdlib unittest, без зависимостей)
PYTHONPATH=sim/src python3 -m hillcourt.runner --scenario design/scenarios/v0_hill_and_salt.yml --months 12
```

Зависимости: Python 3.11+ и PyYAML. Больше ничего.

## Порядок чтения для нового агента

1. `AGENTS.md` — закон: кто где пишет, Definition of Done, запреты.
2. `docs/00_constitution.md` — инварианты. Нарушение инварианта = брак.
3. `docs/02_scope_v0.md` — что в v0 есть, чего нет.
4. `docs/03_ontology.md` — сущности и поля.
5. `docs/09_law_and_land.md` — вводная по праву и земле: карта срезов
   (`07_legal`, `08_manor`, онтология, каталоги, ADR) и сводные таблицы.

## Дерево

```
docs/            документы-законы и ADR
design/catalogs/ каталоги YAML: goods, recipes, spawn_rules, hazards
design/scenarios/ сценарии мира (v0_hill_and_salt.yml)
sim/src/         headless-симуляция (пакет hillcourt)
sim/tests/       тесты инвариантов
tools/prompts/   роли агентов (Economist, Legal, Info, Critic, Implementer, Scribe)
client/          Godot-клиент. ФАЗА 0: заморожено.
```

Лицензия: MIT-friendly. Никаких чужих движков и ассетов стратегий в основе.
