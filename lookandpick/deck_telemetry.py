"""Opt-in read-only motor telemetry. Never constructs a follower or sends motor commands."""
import copy
import fcntl
import json
import os
from pathlib import Path
import threading
import time
from .adapters.so101_health import ReadOnlySO101Health

ROOT=Path(__file__).resolve().parents[1]
REST={'shoulder_pan':-33.,'shoulder_lift':-103.,'elbow_flex':98.,'wrist_flex':74.,'wrist_roll':0.,'gripper':1.3}


def calibration():
    path=ROOT/'calibration/so101-follower-lerobot-2026-11-03.json'
    return json.loads(path.read_text()) if path.is_file() else {}


def port_in_use(port,ignore_pid=None):
    """Do not compete with a bench tool which already has this device open."""
    target=Path(port).resolve()
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit() or proc.name==str(ignore_pid): continue
        try:
            for fd in (proc/'fd').iterdir():
                try:
                    if fd.resolve()==target: return True
                except OSError: pass
        except (OSError,PermissionError): pass
    return False


class PositionHealth(ReadOnlySO101Health):
    def read(self):
        rows=super().read(); limits=calibration()
        for row in rows:
            name=row['joint']; c=limits.get(name)
            row.update(position_deg=None,range_deg=None,rest_deg=REST[name],torque_state='unknown',reading=None)
            try:
                raw=float(self._bus.read('Present_Position',name,normalize=False))
                if c:
                    span=(c['range_max']-c['range_min'])*360/4096
                    mid=(c['range_max']+c['range_min'])/2
                    row.update(position_deg=(raw-mid)*360/4096,range_deg=[-span/2,span/2],rest_deg=(REST[name]/100-.5)*span if name=='gripper' else REST[name],
                               reading=100*(raw-c['range_min'])/(c['range_max']-c['range_min']) if name=='gripper' else (raw-mid)*360/4096)
                else: row['errors'].append('No saved range calibration')
            except Exception as exc: row['errors'].append(f'Present_Position: {exc}')
        return rows


class Telemetry:
    def __init__(self,event=None,reader_factory=PositionHealth):
        self.event=event or (lambda *a,**k:None); self.factory=reader_factory; self.lock=threading.RLock()
        self.reader=None; self.thread=None; self.shutdown=threading.Event(); self.port=None
        self.data={'state':'NOT CONNECTED','read_only':True,'motors':[],'updated':None}; self._warning=False

    def snapshot(self):
        with self.lock: return copy.deepcopy(self.data)

    def connect(self,port):
        if not isinstance(port,str) or not port.startswith(('/dev/ttyACM','/dev/ttyUSB')) or '/' in port[5:]:
            raise ValueError('Choose a local /dev/ttyACM or /dev/ttyUSB device')
        with self.lock:
            if self.reader: raise ValueError('Disconnect the current telemetry reader first')
            if self.factory is PositionHealth and port_in_use(port):
                self.data.update(state='PORT BUSY',error='A bench tool has this port open'); return self.snapshot()
            reader=self.factory(port)
            try:
                reader.open()
                # Kernel exclusion prevents a later terminal tool opening the same port.
                if self.factory is PositionHealth and port_in_use(port,os.getpid()):raise OSError('Port busy; another bench tool opened it')
                bus=reader._bus
                serial=getattr(getattr(bus,'port_handler',None),'ser',None)
                if serial is None and self.factory is PositionHealth:raise RuntimeError('Cannot reserve the serial port exclusively')
                if serial is not None:
                    import termios
                    fcntl.ioctl(serial.fileno(),termios.TIOCEXCL)
                rows=reader.read()
                if any(row.get('errors') for row in rows):raise OSError('Telemetry checks failed; port closed')
            except Exception as exc:
                reader.close(); self.data.update(state='NOT CONNECTED',error=str(exc),motors=[],updated=None)
                return self.snapshot()
            self.reader=reader; self.port=port; self.shutdown.clear(); self._warning=False
            self.data={'state':'LIVE / READ-ONLY','read_only':True,'motors':rows,'updated':time.time(),'port':port}
            self.event('real_arm','connected',f'{port} read-only',0)
            self.thread=threading.Thread(target=self._poll,daemon=True,name='read-only-health');self.thread.start()
            return self.snapshot()

    def _poll(self):
        while not self.shutdown.wait(.5):
            with self.lock:
                if not self.reader: return
                try:
                    rows=self.reader.read(); self.data.update(motors=rows,updated=time.time())
                    low=any(r.get('voltage_v') is not None and r['voltage_v']<4.8 for r in rows)
                    if low and not self._warning:self.event('real_arm','under-voltage warning','One or more motors below 4.8 V',0,status='blocked')
                    self._warning=low
                    if any(r['errors'] for r in rows): raise OSError('Telemetry read failed; reconnect manually')
                except Exception as exc:
                    self.reader.close();self.reader=None;self.data.update(state='NOT CONNECTED',error=str(exc),updated=None)
                    self.event('real_arm','disconnected',str(exc),0,status='blocked');return

    def disconnect(self):
        self.shutdown.set()
        with self.lock:
            if self.reader:self.reader.close();self.reader=None;self.event('real_arm','disconnected','Port closed',0)
            self.data={'state':'NOT CONNECTED','read_only':True,'motors':[],'updated':None}
        if self.thread and self.thread is not threading.current_thread():self.thread.join(timeout=2)
        return self.snapshot()


def sample():
    return {'state':'RECORDED SAMPLE / NOT LIVE','read_only':True,'updated':None,'motors':[
        {'joint':n,'position_deg':REST[n],'range_deg':[-180,180],'rest_deg':REST[n], 'reading':REST[n],
         'voltage_v':5.3 if i!=2 else 4.7,'temperature_c':25+i,'torque_state':'unknown','errors':[]}
        for i,n in enumerate(REST)]}
