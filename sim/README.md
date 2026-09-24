# sim — симуляция Hillcourt (фаза v0)

Headless-каркас: только стандартная библиотека + `PyYAML`. Никакой графики,
никаких LLM-клиентов, никаких сетевых вызовов.

## Запуск тестов

```bash
bash sim/run_tests.sh
```

Эквивалент:

```bash
PYTHONPATH=sim/src python3 -m unittest discover -s sim/tests -p 'test_*.py' -v
```

## Прогон сценария

```bash
PYTHONPATH=sim/src python3 -m hillcourt.runner \
  --scenario design/scenarios/v0_hill_and_salt.yml --months 12 --print-log
```

Основной тик — месяц (`docs/04_tick.md`). Материя не создаётся из ничего:
каждое изменение проходит через `Ledger` (инвариант И-1).
