"""Glasses input routes on the existing loopback Deck server."""
import json
from urllib.parse import urlsplit
from .glasses_input import GlassesInputs, MAX_BODY


def dispatch(handler, session, method):
    path = urlsplit(handler.path).path
    if not path.startswith('/api/glasses/'): return False
    if not hasattr(session, 'runtime') or not hasattr(session, 'submit'):
        handler._json(409, {'error':'Start Deck with --backend sim to use glasses input'}); return True
    controller = getattr(session, 'glasses_inputs', None)
    if controller is None:
        # make_handler initialises this before serving; this fallback supports injected test handlers.
        controller = GlassesInputs(session)
    try:
        if method == 'GET' and path == '/api/glasses/status':
            handler._json(200, controller.status()); return True
        if method == 'GET' and path.startswith('/api/glasses/jobs/'):
            job = session.job(path.rsplit('/',1)[-1])
            if job.get('kind') != 'glasses': raise ValueError('Unknown glasses input')
            handler._json(200, job); return True
        if method != 'POST': handler._json(404, {'error':'Not found'}); return True
        if handler.headers.get('Content-Type','').split(';')[0] != 'application/json':
            handler._json(415, {'error':'Expected application/json'}); return True
        length = int(handler.headers.get('Content-Length',0))
        if not 0 < length <= MAX_BODY: raise ValueError('Use an input below 3 MiB')
        connection = getattr(handler, 'connection', None)
        if connection: connection.settimeout(10)
        payload = json.loads(handler.rfile.read(length))
        if not isinstance(payload,dict): raise ValueError('Expected an object')
        if path == '/api/glasses/input': result = controller.submit(payload)
        elif path == '/api/glasses/confirm': result = controller.confirm(payload.get('job_id'))
        elif path == '/api/glasses/cancel': result = controller.cancel(payload.get('job_id'))
        elif path == '/api/glasses/stop': session.stop(); result = {'status':'stopped'}
        else: handler._json(404, {'error':'Not found'}); return True
        handler._json(202 if 'job_id' in result else 200, result)
    except (ValueError,TypeError,KeyError) as exc: handler._json(400, {'error':str(exc)})
    except (OSError,RuntimeError) as exc: handler._json(503, {'error':str(exc)})
    return True
