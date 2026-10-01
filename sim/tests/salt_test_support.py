from copy import deepcopy
from pathlib import Path

from hillcourt.scenario import load_scenario

FIXTURE = Path(__file__).with_name("salt_test_fixture.yml")


def load_salt_template():
    return load_scenario(FIXTURE, seed=4242)


def clone_salt_world(template):
    return deepcopy(template)
