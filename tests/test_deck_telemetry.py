import ast
import time
from pathlib import Path
import pytest
from lookandpick.deck_telemetry import Telemetry, PositionHealth, sample

class Bus:
    port_handler=None
    def __init__(self): self.calls=[]
    def connect(self,**kwargs): self.calls.append(('connect',kwargs))
    def disconnect(self,**kwargs): self.calls.append(('disconnect',kwargs))
    def read(self,register,joint,**kwargs):
        self.calls.append(('read',register));
        if register=='ID':return list(sample()['motors'][i]['joint'] for i in range(6)).index(joint)+1
        return {'Present_Voltage':53,'Present_Temperature':25,'Present_Position':2200}[register]
    def __getattr__(self,name): raise AssertionError('Forbidden bus call '+name)

def test_readonly_registers_and_close_never_request_torque_release():
    bus=Bus(); reader=PositionHealth('/dev/fake',bus_factory=lambda p:bus)
    reader.open();rows=reader.read();reader.close()
    assert len(rows)==6 and all(r['position_deg'] is not None for r in rows)
    assert bus.calls[-1]==('disconnect',{'disable_torque':False})
    assert {r[1] for r in bus.calls if r[0]=='read'}=={'ID','Present_Voltage','Present_Temperature','Present_Position'}
    tree=ast.parse(Path('lookandpick/deck_telemetry.py').read_text())
    forbidden={'write','sync_write','enable_torque','Goal_Position','Torque_Enable'}
    assert not any(isinstance(n,ast.Attribute) and n.attr in forbidden for n in ast.walk(tree))

def test_disconnected_by_default_explicit_connect_close():
    bus=Bus(); t=Telemetry(reader_factory=lambda p:PositionHealth(p,bus_factory=lambda _:bus))
    assert t.snapshot()['state']=='NOT CONNECTED' and not bus.calls
    assert t.connect('/dev/ttyACM0')['state']=='LIVE / READ-ONLY'
    assert t.disconnect()['state']=='NOT CONNECTED'
    assert len([c for c in bus.calls if c[0]=='connect'])==1
    with pytest.raises(ValueError):t.connect('/tmp/arbitrary')

def test_port_busy_does_not_open_or_retry(monkeypatch):
    monkeypatch.setattr('lookandpick.deck_telemetry.port_in_use',lambda p:True)
    t=Telemetry();assert t.connect('/dev/ttyACM0')['state']=='PORT BUSY';assert t.reader is None
