# Долги, которые закрыты сверкой 29.09.2026

## Назначение

Список открытых долгов, которые **закрыты измерением, а не починкой кода**. Запись
существует, чтобы у кого-то, кто напишет в документе «рента 0.0 в баронстве» или
«148.7 зерна на стенде», было адресное возражение, а не ощущение, что он выдумал
запрет.

Это не реестр ADR: сами решения лежат в `docs/decisions/`, здесь только указатель
«какой долг чем закрыт и чем это проверено». Правки в чужие файлы не вносит.

## Правила

| долг | где записан | чем закрыт | чем проверено |
|---|---|---|---|
| «Рента 0.0 в баронстве при 598 живых дворах» | `docs/decisions/0200…:78`, §Долг №1 | пересборкой мира (ADR 0207), а не починкой оброка: 150 дворов платят оброк | [`0214_debt_0200_rent_is_closed.md`](../../docs/decisions/0214_debt_0200_rent_is_closed.md); замер: `v0_barony_100` 24 мес, рента **140.82** (4242) / **151.46** (1729) |
| «Окна `ACCEPTANCE.md` и хеши `test_start_stand` переносятся после замера» | `docs/decisions/0200…:81`, §Долг №2 | **частично**: окна перенесены (§1 и §1a `ACCEPTANCE.md`), хеши `test_start_stand` — **остаются открытыми**, зона Implementer | [`0213_start_stand_window_is_a_measurement.md`](../../docs/decisions/0213_start_stand_window_is_a_measurement.md) |
| Окно `start_stand` «соль 20.0, зерно 148.7, рента 13.8» | `design/scenarios/ACCEPTANCE.md` §1 | **не измерением, а заданием**: эти числа пришли из списка на проверку (`critic_canon_0143_0148.md:282`, `critic_canon_0149_0153.md:97`) и не воспроизводятся ни на одном горизонте 1–24 и ни на одном сиде | ADR 0213 §Замер, помесячный прогон |
| `LegalStatus.court` «живое поле» | `sim/tests/test_docs_claims.py` (сторож искал читателя текстом) | полем **без читателей**: чтение ищется по AST, `court` в списке мёртвых | ADR 0208 §6 (НАХОДКА 5) + `TestCatalogFieldHasAReader` |

**Правило, которое из этого следует.** Число без команды получения — мнение, а не
измерение (ADR 0190, ADR 0191). Закрытый долг не переоткрывают задним числом: если
величина снова стала прежней, нужен **новый** замер и **новая** запись, а не откат
текста.

## Мутация сторожа: доказательство, что он больше не слеп

Слепота `TestCatalogFieldHasAReader` была не мнением, а измерением (ADR 0208 §6), и
лечится она mutation-проверкой, а не пересказом. Мутация делалась **на копии дерева**,
живое дерево не менялось (ADR 0167:45-48).

```bash
# копия минимального среза, который нужен сторожу
mkdir -p /tmp/opencode/mutguard/sim/tests
tar --exclude=__pycache__ -cf - sim/src sim/tests/test_docs_claims.py \
  | (cd /tmp/opencode/mutguard && tar xf -)

# МУТАЦИЯ: поле с именем-обманкой. `court_tile` — имя существующей в дереве
# локальной переменной, поэтому текстовый поиск считает его «прочитанным».
cd /tmp/opencode/mutguard
python3 - <<'EOF'
import pathlib
p = pathlib.Path('sim/src/hillcourt/ontology.py')
s = p.read_text()
s = s.replace("    court: str\n", "    court: str\n    court_tile: str\n", 1)
p.write_text(s)
EOF

# 1. СТАРЫЙ сторож (текстовый regex) на этой мутации — ЗЕЛЁНЫЙ, то есть слеп
python3 -c "
import re, pathlib
blob = '\n'.join(q.read_text() for q in sorted(pathlib.Path('sim/src/hillcourt').rglob('*.py'))
                 if q.name not in ('ontology.py', 'catalogs.py'))
print('court_tile: старый сторож считает живым ->', bool(re.search(r'\bcourt_tile\b', blob)))"
# → court_tile: старый сторож считает живым -> True      (пропуск нарушителя)

# 2. НОВЫЙ сторож на той же мутации — КРАСНЫЙ
cd sim && PYTHONPATH=src:. python3 -m unittest tests.test_docs_claims.TestCatalogFieldHasAReader
# → FAILED (failures=1)
#   AssertionError: Lists differ: ['LegalStatus.court_tile'] != []

# 3. живое дерево не тронуто
grep -c 'court_tile: str' sim/src/hillcourt/ontology.py     # → 0
rm -rf /tmp/opencode/mutguard
```

**Результат: мутация роняет сторож.** Старый правитель зеленел на настоящем
нарушителе, новый краснеет и называет его поимённо. Эта же мутация закреплена
постоянно, без копии дерева, тестом
`test_a_name_that_only_looks_like_a_read_is_not_a_reader`: объявление локальной
переменной, одноимённая функция и строка `"court"` не считаются чтением поля, а
`status.court` — считается. Пока этот тест существует, сторож нельзя тихо вернуть
на текстовый поиск.

## Проверка

```bash
# Рента в баронстве не 0.0
PYTHONPATH=sim/src python3 -m hillcourt.runner \
  --scenario design/scenarios/v0_barony_100.yml --months 24 --seed 4242 | tail -1
# → «ренты собрано 140.8», «соль 39.3»

# Старое окно стенда не воспроизводится
PYTHONPATH=sim/src python3 -m hillcourt.runner \
  --scenario design/scenarios/start_stand.yml --months 24 --seed 1729 | tail -1
# → «зерно 1248.0, соль 39.2», «ренты собрано 74.3»

# Четыре мёртвых поля пресета, и `court` в их числе
cd sim/src/hillcourt && for f in marriage_needs_permission inheritance can_sell_land court; do
  echo -n "$f: "; grep -rn "\.$f\b" . --include='*.py' | wc -l; done
# → 0, 0, 0, 0

# Сторож держит список и не зеленеет на имени-обманке
cd ../../.. && PYTHONPATH=sim/src:. python3 -m unittest \
  sim.tests.test_docs_claims.TestCatalogFieldHasAReader -v
```

Критерий: рента и соль ненулевые; числа старого окна не появляются; все четыре поля
дают 0 читателей; `TestCatalogFieldHasAReader` зелёный (4 теста), включая
`test_a_name_that_only_looks_like_a_read_is_not_a_reader` — постоянную мутацию, которая
роняет сторож, если он снова начнёт искать читателя по слову.
