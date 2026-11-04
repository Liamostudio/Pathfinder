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

    assert all(f['scene_frame']=='marker_metres' and len(f['arm_base_offset'])==3 and f['stop_distance_m']>0 for f in run['frames'])
    assert run['frames'][-1]['stop']['kind']=='person'
