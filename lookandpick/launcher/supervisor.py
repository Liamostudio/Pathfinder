"""Process-group ownership, bounded waits and sanitised logs for fixed services."""
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import shlex
import subprocess
import threading
import time


def identity(pid):
    try:
        proc = Path('/proc')/str(int(pid))
        fields = (proc/'stat').read_text().rsplit(')', 1)[1].split()
        if fields[0] == 'Z' or proc.stat().st_uid != os.getuid():
            return None
        argv = (proc/'cmdline').read_bytes().split(b'\0')
        return {'pid':int(pid), 'start':fields[19], 'pgid':int(fields[2]),
                'session':int(fields[3]), 'argv_hash':hashlib.sha256(b'\0'.join(argv)).hexdigest(),
                'argv':[v.decode(errors='replace') for v in argv if v]}
    except (OSError, ValueError, IndexError):
        return None


def group_members(pgid):
    result = []
    for proc in Path('/proc').iterdir():
        if proc.name.isdigit():
            item = identity(proc.name)
            if item and item['pgid'] == pgid:
                result.append(item['pid'])
    return result


def port_owner(port):
    inodes = set()
    for filename in ('/proc/net/tcp', '/proc/net/tcp6'):
        try:
            for line in Path(filename).read_text().splitlines()[1:]:
                fields = line.split()
                if fields[3] == '0A' and int(fields[1].rsplit(':', 1)[1], 16) == port:
                    inodes.add(fields[9])
        except (OSError, ValueError, IndexError):
            continue
    if not inodes:
        return None
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            for fd in (proc/'fd').iterdir():
                if os.readlink(fd) in {f'socket:[{v}]' for v in inodes}:
                    item = identity(proc.name)
                    if item:
                        return item
        except (OSError, PermissionError):
            continue
    return {'pid':None, 'argv':[], 'unknown':True}


class Redactor:
    def __init__(self, root):
        self.root = Path(root)
        self.known=set()

    def values(self):
        values = {v for k,v in os.environ.items() if re.search(r'KEY|TOKEN|SECRET|PASSWORD', k, re.I) and len(v)>3}
        try:
            for line in (self.root/'.env').read_text().splitlines():
                if '=' in line and not line.lstrip().startswith('#'):
                    key,value = line.split('=', 1)
                    if re.search(r'KEY|TOKEN|SECRET|PASSWORD', key, re.I):
                        try:value = shlex.split(value,comments=True)[0]
                        except (ValueError,IndexError):continue
                        if len(value)>3:
                            values.add(value)
        except OSError:
            pass
        try:
            token = (self.root/'recordings/glasses-gateway/pairing-token').read_text().strip()
            if token:
                values.add(token)
        except OSError:
            pass
        self.known.update(values)
        return sorted(self.known, key=len, reverse=True)

    def text(self, text):
        text = str(text)
        for value in self.values():
            text = text.replace(value, '[REDACTED]')
        return re.sub(r'(?i)(bearer\s+)[\w.\-]+', r'\1[REDACTED]', text)

    def clean(self, value):
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {k: ('[REDACTED]' if re.fullmatch(r'.*(?:api_key|password|secret|authorization|pairing_token)', k, re.I)
                        else self.clean(v)) for k,v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.clean(v) for v in value]
        return value


class Supervisor:
    ORDER = ('relay', 'deck', 'selfcheck', 'tls', 'logs', 'sam', 'simulator')
    def __init__(self, config):
        self.config = config
        self.redactor = Redactor(config.root)
        self.lock = threading.RLock()
        self.services = {}
        config.logs.mkdir(parents=True, exist_ok=True)
        self.registry = config.logs/'processes.json'
        try:
            entries = json.loads(self.registry.read_text())
            for name,item in entries.items():
                current = identity(item['pid'])
                if name in self.ORDER and current and all(current[k]==item[k] for k in ('start','pgid','argv_hash')) and (item.get('external') or current['pid']==current['pgid']):
                    self.services[name] = {'identity':current, 'process':None, 'reader':None,'external':bool(item.get('external')),'port':item.get('port')}
        except (OSError, ValueError, TypeError, KeyError):
            pass

    def _save(self):
        data = {k:{**{field:v['identity'][field] for field in ('pid','start','pgid','argv_hash')},'external':v.get('external',False),'port':v.get('port')} for k,v in self.services.items()}
        temp = self.registry.with_suffix('.tmp')
        temp.write_text(json.dumps(data));temp.replace(self.registry)

    def running(self, name):
        with self.lock:
            item = self.services.get(name)
            if not item:
                return False
            current = identity(item['identity']['pid'])
            return bool(current and all(current[k]==item['identity'][k] for k in ('start','pgid','argv_hash')))

    def start(self, name, spec, cancelled=lambda:False):
        if name not in self.ORDER:
            raise ValueError('Unknown service')
        with self.lock:
            if cancelled():
                raise InterruptedError('Stopped by you')
            if self.running(name):
                return
            old = self.services.get(name)
            if old and group_members(old['identity']['pgid']):
                raise ValueError(f'{name}: old child processes remain; use Stop all first')
            if spec.port and port_owner(spec.port):
                owner = port_owner(spec.port)
                raise ValueError(f'{name}: port {spec.port} is busy (PID {owner.get("pid") or "unavailable"}); stop the existing service first')
            try:
                proc = subprocess.Popen(list(spec.argv), cwd=self.config.root, env=self.config.env(),
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
            except OSError as exc:
                raise ValueError(f'{name}: cannot start the configured interpreter: {exc.strerror}') from None
            thread = threading.Thread(target=self._log, args=(name, proc), daemon=True)
            thread.start()
            info = identity(proc.pid)
            if info is None:
                proc.wait(timeout=2);thread.join(timeout=2)
                raise ValueError(f'{name} exited immediately (code {proc.returncode}). '+self.tail(name))
            self.services[name] = {'identity':info, 'process':proc, 'reader':thread}
            self._save()

    def _log(self, name, proc):
        import codecs
        decoder = codecs.getincrementaldecoder('utf-8')('replace')
        pending = ''
        with (self.config.logs/(name+'.log')).open('a', encoding='utf-8') as out:
            while True:
                chunk = os.read(proc.stdout.fileno(), 4096)
                if not chunk:
                    break
                pending += decoder.decode(chunk)
                # Keep incomplete secret prefixes until the next chunk.
                keep = max([256]+[len(v) for v in self.redactor.values()])
                pending = self.redactor.text(pending)
                newline = pending.rfind('\n')+1
                cut = newline or max(0, len(pending)-keep-4096)
                if cut:
                    out.write(pending[:cut]);out.flush();pending=pending[cut:]
            out.write(self.redactor.text(pending+decoder.decode(b'', final=True)));out.flush()
        proc.stdout.close()

    def tail(self, name, limit=4500, offset=0):
        if name not in self.ORDER:
            raise ValueError('Unknown service')
        try:
            with (self.config.logs/(name+'.log')).open('rb') as f:
                f.seek(0, 2);f.seek(max(offset, f.tell()-limit));return self.redactor.text(f.read().decode(errors='replace'))
        except OSError:
            return ''

    def adopt(self, name, owner, port):
        if name not in self.ORDER:raise ValueError('Unknown service')
        with self.lock:
            current=identity(owner['pid'])
            if not current or any(current[k]!=owner[k] for k in ('start','pgid','argv_hash')):raise ValueError('Existing process changed; re-check')
            if not self.running(name):
                self.services[name]={'identity':current,'process':None,'reader':None,'external':True,'port':port}
                self._save()

    def stop(self, name, external_confirmed=False):
        with self.lock:
            item = self.services.get(name)
            if not item:
                return
            info = item['identity'];current=identity(info['pid'])
            if current and any(current[k]!=info[k] for k in ('start','pgid','argv_hash')):
                raise ValueError(f'{name}: process identity changed; refusing to signal it')
            if item.get('external'):
                if current is None:
                    del self.services[name];self._save();return
                if not external_confirmed:raise ValueError(f'{name}: confirm stopping existing PID {info["pid"]} first')
                if current and current['pid']==os.getpid():raise ValueError('Refusing to signal the launcher')
                if current:
                    try:os.kill(info['pid'],signal.SIGTERM)
                    except ProcessLookupError:pass
                    deadline=time.monotonic()+self.config.shutdown_timeout
                    while identity(info['pid']) and time.monotonic()<deadline:time.sleep(.05)
                    current=identity(info['pid'])
                    if current and all(current[k]==info[k] for k in ('start','argv_hash')):
                        try:os.kill(info['pid'],signal.SIGKILL)
                        except ProcessLookupError:pass
                    deadline=time.monotonic()+2
                    while identity(info['pid']) and time.monotonic()<deadline:time.sleep(.02)
                if identity(info['pid']):raise ValueError(f'{name}: existing PID is still stopping')
                del self.services[name];self._save();return
            if info['pgid'] != info['pid'] or info['pgid']==os.getpgrp():
                raise ValueError('Refusing to signal an unowned process group')
            # In-memory Popen proves group ownership even if its leader has exited.
            if current is None and item['process'] is None and group_members(info['pgid']):
                raise ValueError(f'{name}: cannot verify surviving group ownership; stop it locally')
            if group_members(info['pgid']):
                try:os.killpg(info['pgid'], signal.SIGTERM)
                except ProcessLookupError:pass
                deadline = time.monotonic()+self.config.shutdown_timeout
                while group_members(info['pgid']) and time.monotonic()<deadline:
                    time.sleep(.05)
                if group_members(info['pgid']):
                    try:os.killpg(info['pgid'], signal.SIGKILL)
                    except ProcessLookupError:pass
            if item['process']:
                item['process'].wait(timeout=3)
            if item['reader']:
                item['reader'].join(timeout=2)
            if group_members(info['pgid']):
                raise ValueError(f'{name}: child processes remain; inspect locally')
            del self.services[name];self._save()

    def stop_all(self, external_confirmed=False):
        errors = []
        for name in self.ORDER:
            try:self.stop(name,external_confirmed)
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:errors.append(self.redactor.text(exc))
        return errors

    def status(self):
        return {name:{'running':self.running(name), 'pid':item['identity']['pid'], 'external':item.get('external',False),'port':item.get('port'), 'log':self.tail(name)}
                for name,item in list(self.services.items())}
