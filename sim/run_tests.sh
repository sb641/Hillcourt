#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/.." && pwd)"

# Уборка фикстур-сценариев, оставшихся в корне репозитория и в `design/scenarios/`
# (ADR 0190, решение владельца от 08:5x: уборка уходит в обвязку прогона, а не в
# тест).
#
# Почему так: `test_density_cap._load` и `test_hex_yield_law` пишут вариант
# сценария в корень (`load_scenario` ищет `AGENTS.md` относительно файла, поэтому
# `tempfile` без `dir=` был бы неправ), и убирают его на успешном пути и на
# `atexit`. Но `atexit` не выполняется при SIGTERM, а жёстко прерванный прогон
# оставляет `tmp*.yml` — полные сценарии на 2-7 КБ. Убитый тест не может убрать
# за собой: за то, как его запустили, отвечает обвязка, а не библиотека тестов.
# Перед стартом снимаем то, что осталось от прошлых прогонов, чтобы мусор не
# копился. Свои файлы тесты убирают сами — эти строки только подчищают наследие.
#
# `design/scenarios/` — ТОЖЕ. Там живут настоящие сценарии репозитория, и
# `tmp*.yml` среди них выглядит как сценарий: агент откроет каталог, увидит
# незнакомый файл и будет читать его как данные игры. Имя `tmp*` не должно
# встречаться среди `design/scenarios/` ни разу.
#
# `test_land_basis_*` — ТОЖЕ, и по большей причине. `test_scenario_land_basis`
# пишет вариант сценария через `tempfile.mkstemp(prefix="test_land_basis_")`:
# префикс выбран не `tmp`, чтобы не попадать под `/tmp*.yml` в `.gitignore`.
# Но два теста — `test_draft_livestock` и `test_player_orders` — перебирают
# `design/scenarios/*.yml` и отбрасывают по `startswith("tmp")`. Фикстура под
# другим именем проходит этот фильтр, и тест грузит обрубок без двора
# `hh_missing`: `load_scenario` падает, прибор краснеет из-за мусора, а не
# из-за игры. Проверено на 28 файлах: с ними `test_draft_livestock` падает,
# без них — зелёный. Уборка обязана знать про этот префикс, иначе мусор
# вернётся после первого же убитого прогона.
shopt -s nullglob
stale=()
for dir in "$root" "$root/design/scenarios"; do
  for pattern in 'tmp*.yml' 'test_land_basis_*.yml'; do
    found=("$dir"/$pattern)
    ((${#found[@]})) && stale+=("${found[@]}")
  done
done
if ((${#stale[@]})); then
  printf 'run_tests: убираю %d фикстур(у) сценариев (tmp*, test_land_basis_*)\n' "${#stale[@]}"
  rm -f -- "${stale[@]}"
fi
shopt -u nullglob

cd "$here"
PYTHONPATH="$here/src" python3 -m unittest discover -s tests -p 'test_*.py' -v
