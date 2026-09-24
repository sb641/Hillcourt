# Hillcourt («Двор на холме»)

A turn-based frontier strategic simulation and autonomous economic engine.

The player is a petty landholder on the rim of a post-imperial frontier (magic and high empire collapsed ~600 years ago). Unlike traditional RTS or colony builders where players directly micromanage workers ("chop tree", "build road"), Hillcourt models **feudal tenure, customary obligations, and autonomous household agency**. Households make autonomous economic decisions based on survival needs, material reality, and imperfect local information. The world does not emerge from an arbitrary technology tree.

---

## Phase 0: Headless Simulation & Invariants

This repository is currently in **Phase 0** — a deterministic, test-driven simulation engine and ontology, not a graphical game:

- **Headless Python Engine (`sim/`):** Pure Python 3.11+ simulation with zero heavy dependencies (standard library + PyYAML). No game engines, rendering loops, or graphics in the simulation core.
- **Data-Driven Catalogs (`design/catalogs/`):** Strict declarative definitions for goods, recipes, seasonal rhythms, hazards, and legal land regimes.
- **Scenarios & State Verification (`design/scenarios/`):** Concrete world scenarios with deterministic hashing and matter conservation invariants.
- **Constitutional Architecture & ADRs (`docs/`):** 80+ recorded architectural decision records (ADRs) and formal specifications governing information propagation, travel times, land rights, and demography.
- **Client Layer (`client/`):** Godot client scaffolding is deliberately frozen in Phase 0 until all simulation invariants are proven green.

---

## Autonomous Multi-Agent Architecture

Hillcourt is actively developed and maintained by an autonomous multi-agent collective governed by `AGENTS.md` and `docs/00_constitution.md`. Each agent role operates under strict functional boundaries and Definition of Done:

| Role | Domain Responsibility | Write Scope |
|---|---|---|
| **Economist** | Goods, recipes, storage, spoilage, customary rents, matter preservation | `design/catalogs/`, `sim/src/hillcourt/economy/` |
| **Legal** | Tenures, customary dues, defaults, inheritance, obligations | `design/catalogs/obligations.yml`, `rights.yml`, `sim/src/hillcourt/legal/` |
| **Info** | Fog of information, rumor decay, travel delays, zero player omniscience | `sim/src/hillcourt/news/` |
| **Critic** | Adversarial review: perpetual motion traps, infinite resource loops, tech-tree leakage | `docs/decisions/` (review reports) |
| **Implementer** | Core simulation loop, hex topology, pathfinding, test harnesses | `sim/`, `sim/tests/` |
| **Scribe** | Formal specs, ontology sync, constitutional alignment | `docs/` |
| **Orchestrator** | Final acceptance gates, merge resolution, ADR ratification | Repository-wide |

*Note: Internal engineering documentation and ADRs (`docs/*`) are maintained in Russian to prevent ontological drift across community models.*

---

## Quickstart

### Prerequisites
- Python 3.11+
- PyYAML

```bash
pip install pyyaml
```

### 1. Run Test Suite
Run the full invariant and economy test suite (built on standard library `unittest`):

```bash
bash sim/run_tests.sh
```

Or run directly via `python3`:

```bash
PYTHONPATH=sim/src python3 -m unittest discover -s sim/tests -p 'test_*.py' -v
```

### 2. Run Headless Simulation
Execute a 12-month simulation run against the canonical frontier scenario:

```bash
PYTHONPATH=sim/src python3 -m hillcourt.runner --scenario design/scenarios/v0_hill_and_salt.yml --months 12
```

---

## Repository Map

```
docs/             Constitutional laws, ontology specifications, and ADRs (0001–0080+)
design/
  catalogs/       Declarative YAML catalogs (goods, recipes, hazards, land regimes)
  scenarios/      World scenarios (v0_hill_and_salt.yml, barony, settlement topologies)
sim/
  src/hillcourt/  Core simulation package (engine, economy, legal, news, hazards)
  tests/          Deterministic invariant tests (matter conservation, land rights)
tools/prompts/    Agent role contracts and operational prompts
client/           Godot client (frozen in Phase 0)
```

---

## Core Invariants

1. **Conservation of Matter:** Matter cannot appear from nothing. Every resource unit requires a catalog storage location, a recipe, or a declared spawn rule (`test_matter_conservation.py`).
2. **Zero Omniscience:** Information has mass and speed. News travels along roads, rivers, and messengers with realistic delays and potential distortion.
3. **Tenure over Command:** The landholder commands parcels, jurisdictions, and courts — not individual peasant actions.
4. **Deterministic Simulation:** Same seed and scenario must produce the exact identical state history.

---

## License

MIT-friendly open-source architecture. Built with clean, ground-up simulation logic without proprietary strategy assets or third-party engines.
