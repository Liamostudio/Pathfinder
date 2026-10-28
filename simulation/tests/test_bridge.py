import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
import pytest
from sim_env.server import Session,handler_for
from sim_env.client import SimulationClient
from lookandpick.tools import Tools
from lookandpick.world import WorldState
from lookandpick.safety import Safety


@pytest.fixture
def bridge():
    session=Session(render=False,realtime=False)
    server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(session))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    client=SimulationClient(f'http://127.0.0.1:{server.server_port}')
    yield client,session
    server.shutdown();server.server_close();session.close();thread.join(timeout=2)


def test_external_controller_tools_use_remote_arm_tracker(bridge):
    client,session=bridge
    tools=Tools(WorldState(),client,client,Safety())
    assert tools.pick('obj1')=='picked red cube'
    assert tools.place('obj1',(.18,-.2,.018))=='placed red cube'
    assert client.state()['held'] is None
    assert client.action('move',position=[5,5,5])['status']=='refused'
    tools.stop()
    assert client.state()['stopped'] is True
    assert client.move_to((.2,.1,.1),1) is False


def test_bridge_validation_and_host_checks(bridge):
    client,_=bridge
    request=urllib.request.Request(client.url+'/api/action',b'{"action":"speed","scale":NaN}',{'Content-Type':'application/json'})
    with pytest.raises(urllib.error.HTTPError) as exc: urllib.request.urlopen(request)
    assert exc.value.code==400
    request=urllib.request.Request(client.url+'/api/state',headers={'Origin':'https://example.com'})
    with pytest.raises(urllib.error.HTTPError) as exc: urllib.request.urlopen(request)
    assert exc.value.code==403
    with pytest.raises(urllib.error.HTTPError) as exc: client._get('/../../CLAUDE.md')
    assert exc.value.code==404
    assert client.action('joints',positions=[10]*6)['status']=='error'
