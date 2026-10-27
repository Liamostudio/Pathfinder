"""Simulation behaviour and HTTP request boundaries; no camera, GPU or hardware."""
from email.message import Message
from io import BytesIO
import json
from types import SimpleNamespace

import pytest

from lookandpick.mission_control import DROP_ZONE, MissionControl, make_handler


def test_pick_place_updates_the_shared_scene_and_logs_results():
    session = MissionControl()
    picked = session.action({'action': 'pick'})
    assert picked['gripping']
    assert picked['events'][-1]['input'] == 'pick(obj1)'
    placed = session.action({'action': 'place'})
    assert not placed['gripping']
    assert placed['events'][-1]['output'] == 'placed red cube'
    assert next(o for o in placed['scene']['objects'] if o['id'] == 'obj1')['position'] == DROP_ZONE


def test_hand_scenarios_drive_the_existing_safety_gate():
    session = MissionControl()
    assert session.state()['speed_scale'] == 1
    assert 0 < session.action({'action': 'hand', 'mode': 'approach'})['speed_scale'] < 1
    assert session.action({'action': 'hand', 'mode': 'near'})['speed_scale'] == 0
    blocked = session.action({'action': 'pick'})
    assert not blocked['gripping']
    assert blocked['events'][-1]['status'] == 'blocked'
    session.action({'action': 'hand', 'mode': 'clear'})
    assert session.action({'action': 'pick'})['gripping']


def test_stop_is_latched_until_a_new_simulation():
    session = MissionControl()
    stopped = session.action({'action': 'stop'})
    assert stopped['stopped'] and stopped['speed_scale'] == 0
    assert not session.action({'action': 'pick'})['gripping']
    reset = session.action({'action': 'reset'})
    assert not reset['stopped']
    assert reset['revision'] > stopped['revision']
    assert session.action({'action': 'pick'})['gripping']


def test_fixed_commands_are_explicit_and_unknown_commands_do_nothing():
    session = MissionControl()
    with pytest.raises(ValueError, match='demo parser'):
        session.action({'action': 'command', 'text': 'wave the arm'})
    assert not session.arm.log
    assert session.action({'action': 'command', 'text': 'pick up the blue mug'})['selected'] == 'obj2'
    assert session.state()['gripping']


def test_model_cues_are_distinguished_from_measured_tool_calls():
    session = MissionControl()
    assert not any(event['scripted'] for event in session.state()['events'])
    cue = session.action({'action': 'cue'})['events'][-1]
    assert cue['scripted'] and cue['source'] == 'Muse Spark'
    assert 'API not connected' in cue['output']
    assert not session.arm.log


def test_session_history_is_bounded():
    session = MissionControl()
    for _ in range(110):
        session.action({'action': 'hand', 'mode': 'clear'})
    assert len(session.state()['events']) == 100


def handler(path='/', *, body=None, origin=None, host='127.0.0.1:8765', content_type='application/json'):
    """Invoke the HTTP handler without needing a listening socket."""
    handler_class = make_handler(MissionControl())
    request = handler_class.__new__(handler_class)
    request.server = SimpleNamespace(server_address=('127.0.0.1', 8765))
    request.path = path
    request.headers = Message()
    request.headers['Host'] = host
    if origin:
        request.headers['Origin'] = origin
    if body is not None:
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        request.headers['Content-Type'] = content_type
        request.headers['Content-Length'] = str(len(raw))
        request.rfile = BytesIO(raw)
    request.response = None

    def capture(code, body, content_type='application/json; charset=utf-8'):
        request.response = (code, body, content_type)

    request._send = capture
    return request


def test_local_api_and_static_files_are_served():
    request = handler('/api/state')
    request.do_GET()
    code, body, _ = request.response
    assert code == 200 and json.loads(body)['mode'] == 'simulation'
    request = handler('/')
    request.do_GET()
    assert request.response[0] == 200
    assert b'Pathfinder' in request.response[1]
    assert request.response[2].startswith('text/html')


@pytest.mark.parametrize('path', ['/../pyproject.toml', '/%2e%2e/.env', '/api/missing'])
def test_static_handler_cannot_read_outside_web_root(path):
    request = handler(path)
    request.do_GET()
    assert request.response[0] == 404


@pytest.mark.parametrize('origin,host', [('https://example.com','127.0.0.1:8765'),
                                       (None,'example.com:8765')])
def test_untrusted_origins_and_hosts_are_rejected(origin, host):
    request = handler('/api/action', body={'action':'pick'}, origin=origin, host=host)
    request.do_POST()
    assert request.response[0] == 403


def test_action_endpoint_rejects_bad_requests():
    for body in (b'not json', [], {'action':'run-shell'}, {'action':'select','id':'missing'}):
        request = handler('/api/action', body=body)
        request.do_POST()
        assert request.response[0] == 400
    request = handler('/api/action', body={'action':'pick'}, content_type='text/plain')
    request.do_POST()
    assert request.response[0] == 415


def test_valid_http_action_returns_updated_state():
    request = handler('/api/action', body={'action':'pick'}, origin='http://127.0.0.1:8765')
    request.do_POST()
    assert request.response[0] == 200
    assert json.loads(request.response[1])['gripping']
