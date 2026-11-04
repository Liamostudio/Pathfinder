"""Authenticated iPhone-to-Deck relay. TLS is mandatory off loopback; no model keys here.

python -m lookandpick.glasses_gateway --deck-url http://127.0.0.1:8769

For a phone: explicitly choose --bind / --public-host and supply --cert / --key.
The phone must trust the server certificate normally; no TLS bypass is provided.
"""
import argparse
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import ssl
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from .glasses_input import MAX_BODY


def deck_base(url):
    value=urlsplit(url)
    if value.scheme!='http' or value.hostname not in ('127.0.0.1','localhost') or value.path not in ('','/') or value.query or value.fragment or value.username or value.password:
        raise ValueError('Deck must be a loopback HTTP base URL')
    return url.rstrip('/')


def load_token(path):
    path=Path(path).expanduser()
    if not path.exists():
        path.parent.mkdir(parents=True,exist_ok=True)
        try:
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as f:f.write(secrets.token_urlsafe(32)+'\n')
        except FileExistsError:pass
    if path.stat().st_mode&0o077:raise ValueError('Pairing token file must have mode 600')
    token=path.read_text().strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{24,128}',token):raise ValueError('Invalid pairing token file')
    return token


def handler_for(base,token,authorities):
    base=deck_base(base); opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass  # never log pairing tokens or utterances
        def send(self,status,value):
            data=json.dumps(value,allow_nan=False).encode();self.send_response(status)
            for k,v in {'Content-Type':'application/json','Content-Length':str(len(data)), 'Cache-Control':'no-store',
                        'X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'none'; frame-ancestors 'none'"}.items():self.send_header(k,v)
            self.end_headers()
            try:self.wfile.write(data)
            except (BrokenPipeError,ConnectionResetError):pass
        def auth(self):
            if self.headers.get('Host') not in authorities:return False
            # Native URLSession sends no Origin. No browser-origin access is granted.
            if self.headers.get('Origin') is not None:return False
            supplied=self.headers.get('Authorization','')
            return hmac.compare_digest(supplied.encode(),('Bearer '+token).encode())
        def relay(self,method):
            self.connection.settimeout(10)
            if not self.auth():return self.send(401,{'error':'Pair this iPhone with the gateway token'})
            parts=urlsplit(self.path)
            if parts.query or parts.fragment:return self.send(400,{'error':'Query parameters are not accepted'})
            routes={'/v1/status':'/api/glasses/status','/v1/input':'/api/glasses/input','/v1/confirm':'/api/glasses/confirm',
                    '/v1/cancel':'/api/glasses/cancel','/v1/stop':'/api/glasses/stop'}
            path=routes.get(parts.path)
            if method=='GET' and re.fullmatch(r'/v1/jobs/[a-f0-9]{32}',parts.path):path='/api/glasses/jobs/'+parts.path.rsplit('/',1)[1]
            elif (method=='GET' and parts.path!='/v1/status') or (method=='POST' and parts.path not in routes.keys()-{'/v1/status'}):path=None
            if path is None:return self.send(404,{'error':'Not found'})
            data=None
            if method=='POST':
                if self.headers.get('Content-Type','').split(';')[0]!='application/json':return self.send(415,{'error':'Expected application/json'})
                try:length=int(self.headers.get('Content-Length',0))
                except ValueError:return self.send(400,{'error':'Invalid length'})
                limit=MAX_BODY if path.endswith('/input') else 4096
                if not 0<length<=limit:return self.send(413,{'error':'Input exceeds upload limit'})
                try:
                    data=self.rfile.read(length)
                    if len(data)!=length:raise ValueError('Interrupted upload')
                    if not isinstance(json.loads(data),dict):raise ValueError('Expected an object')
                except (OSError,ValueError):return self.send(400,{'error':'Invalid upload'})
            request=urllib.request.Request(base+path,data=data,method=method,headers={'Content-Type':'application/json'})
            try:
                with opener.open(request,timeout=5 if path.endswith('/stop') else 12) as reply:
                    raw=reply.read(MAX_BODY+1)
                    if len(raw)>MAX_BODY:raise ValueError('Reply exceeds limit')
                    self.send(reply.status,json.loads(raw))
            except urllib.error.HTTPError as exc:
                try:value=json.loads(exc.read(8192))
                except ValueError:value={'error':'Deck refused the request'}
                self.send(exc.code,value)
            except (OSError,ValueError):self.send(503,{'error':'Deck is unavailable; use its local STOP control'})
        def do_GET(self):self.relay('GET')
        def do_POST(self):self.relay('POST')
    return Handler


def make_server(bind,port,base,token,*,cert=None,key=None,public_host=None):
    base=deck_base(base)
    address=ipaddress.ip_address(bind)
    if not address.is_loopback and (not cert or not key or not public_host):
        raise ValueError('Phone access requires --cert, --key and --public-host; TLS verification stays enabled')
    if bool(cert)!=bool(key):raise ValueError('Supply both --cert and --key')
    host=public_host or bind
    if not re.fullmatch(r'[A-Za-z0-9.-]+',host):raise ValueError('Use a hostname or IPv4 address for public-host')
    server=ThreadingHTTPServer((bind,port),BaseHTTPRequestHandler)
    authorities={f'{host}:{server.server_port}'}
    if address.is_loopback:authorities.update({f'localhost:{server.server_port}',f'127.0.0.1:{server.server_port}'})
    server.RequestHandlerClass=handler_for(base,token,authorities)
    if cert:
        try:
            context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.minimum_version=ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(cert,key);server.socket=context.wrap_socket(server.socket,server_side=True)
        except Exception:server.server_close();raise
    return server


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--bind',default='127.0.0.1');p.add_argument('--port',type=int,default=8772)
    p.add_argument('--deck-url',default='http://127.0.0.1:8769');p.add_argument('--token-file',type=Path,default=Path('recordings/glasses-gateway/pairing-token'))
    p.add_argument('--cert',type=Path);p.add_argument('--key',type=Path);p.add_argument('--public-host');a=p.parse_args()
    try:token=load_token(a.token_file);server=make_server(a.bind,a.port,a.deck_url,token,cert=a.cert,key=a.key,public_host=a.public_host)
    except (ValueError,OSError) as exc:p.error(str(exc))
    print(f'Glasses gateway: {"https" if a.cert else "http"}://{a.public_host or a.bind}:{server.server_port}',flush=True)
    print(f'Pairing token is in {a.token_file}. It is not the Meta API key. No microphone is opened by this process.',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
if __name__=='__main__':main()
