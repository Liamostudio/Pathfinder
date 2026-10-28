import pytest
from sim_env.scenarios import SCENARIOS,run

@pytest.mark.parametrize('scenario',SCENARIOS,ids=lambda s:s.name)
def test_acceptance_scenarios(scenario):
    assert run(scenario,0)['passed']


def test_seed_reproducible():
    a=run(SCENARIOS[0],42)
    b=run(SCENARIOS[0],42)
    assert a['samples']==b['samples']
    assert a['end_state']['scene']==b['end_state']['scene']
