from lookandpick.deck_demo import run_demo

def test_seeded_filming_demo_three_identical_runs():
    runs=[run_demo(0) for _ in range(3)]
    assert runs[0]==runs[1]==runs[2]
    run=runs[0];states={f['hand_gate']['state'] for f in run['frames']}
    assert {'clear','slow','stop','latched'}<=states
    assert any(e['source']=='Scripted model / no API' for e in run['events'])
    assert any(e['input']=='STOP' for e in run['events'])
    assert any(e['output'].startswith('placed') for e in run['events'])
    assert run['frames'][-1]['stopped']
