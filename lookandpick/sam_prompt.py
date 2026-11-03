"""Local SAM 3.1 recording GUI. Run: python -m lookandpick.sam_prompt."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import threading
import uuid

ROOT = Path(__file__).resolve().parents[1]
WEB = Path(__file__).with_name('sam_web')


class Studio:
    def __init__(self, output, python):
        self.output = Path(output).resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.python = str(Path(python).expanduser())
        self.jobs = {}
        self.lock = threading.Lock()
        self.busy = False

    def submit(self, body, prompt):
        prompt = prompt.strip()
        if not prompt or len(prompt) > 200:
            raise ValueError('Enter a prompt between 1 and 200 characters.')
        with self.lock:
            if self.busy:
                raise ValueError('SAM is processing a clip. Please wait for it to finish.')
            job = uuid.uuid4().hex
            folder = self.output / job
            folder.mkdir()
            (folder / 'original.webm').write_bytes(body)
            self.busy = True
            self.jobs[job] = {'state': 'processing', 'message': 'Loading SAM 3.1 and tracking your prompt…'}
        threading.Thread(target=self.process, args=(job, prompt), daemon=True).start()
        return job

    def process(self, job, prompt):
        folder = self.output / job
        try:
            env = dict(os.environ, PYTORCH_CUDA_ALLOC_CONF='expandable_segments:True')
            with (folder / 'inference.log').open('w') as log:
                from .gpu import gpu_lease
                with gpu_lease('sam-prompt-'+job):
                    result = subprocess.run([
                        self.python, '-I', str(ROOT / 'scripts/sam3_live.py'),
                        '--video', str(folder / 'original.webm'), '--prompt', prompt,
                        '--sample-fps', '10', '--max-frames', '100', '--no-window',
                        '--frames-out', str(folder / 'frames'), '--out', str(folder / 'annotated.mp4'),
                    ], stdout=log, stderr=subprocess.STDOUT, env=env, timeout=600)
            if result.returncode:
                lines = (folder / 'inference.log').read_text()[-2000:]
                raise RuntimeError(lines)
            meta = json.loads((folder / 'frames/metadata.json').read_text())
            with self.lock:
                self.jobs[job] = {'state': 'ready', **meta}
        except Exception as exc:
            with self.lock:
                self.jobs[job] = {'state': 'error', 'message': str(exc)}
        finally:
            with self.lock:
                self.busy = False


def handler_for(studio):
    class Handler(BaseHTTPRequestHandler):
        def reply(self, code, body, kind='application/json'):
            data = json.dumps(body).encode() if kind == 'application/json' else body
            self.send_response(code)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            parts = self.path.split('?')[0].strip('/').split('/')
            if parts[0] == 'api' and len(parts) == 3 and parts[1] == 'jobs':
                with studio.lock:
                    status = studio.jobs.get(parts[2])
                self.reply(200 if status else 404, status or {'message': 'Unknown clip'})
                return
            if parts[0] == 'clips':
                path = studio.output.joinpath(*parts[1:]).resolve()
                allowed = path.is_relative_to(studio.output) and path.suffix in {'.jpg', '.mp4', '.webm'}
            else:
                path = WEB / ('index.html' if parts == [''] else parts[0])
                allowed = len(parts) == 1 and path.name in {'index.html', 'app.js', 'style.css'}
            if not allowed or not path.is_file():
                self.reply(404, {'message': 'Not found'})
                return
            kinds = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css',
                     '.jpg': 'image/jpeg', '.mp4': 'video/mp4', '.webm': 'video/webm'}
            self.reply(200, path.read_bytes(), kinds[path.suffix])

        def do_POST(self):
            # Reject cross-origin browser writes to this local GPU service.
            origin = self.headers.get('Origin')
            if origin and origin != f'http://{self.headers.get("Host")}':
                self.reply(403, {'message': 'Local requests only'})
                return
            if self.path != '/api/process':
                self.reply(404, {'message': 'Not found'})
                return
            try:
                size = int(self.headers.get('Content-Length', 0))
                if not 0 < size <= 30 * 1024 * 1024:
                    raise ValueError('Clip must be between 1 byte and 30 MB.')
                from urllib.parse import unquote
                prompt = unquote(self.headers.get('X-Prompt', ''))
                job = studio.submit(self.rfile.read(size), prompt)
                self.reply(202, {'id': job})
            except ValueError as exc:
                self.reply(400, {'message': str(exc)})

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8767)
    parser.add_argument('--python', default='~/src/sam3-venv/bin/python')
    parser.add_argument('--output', default=str(ROOT / 'recordings/sam-prompt'))
    args = parser.parse_args()
    studio = Studio(args.output, args.python)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), handler_for(studio))
    print(f'SAM Prompt example → http://127.0.0.1:{args.port}', flush=True)
    print(f'Clips saved in {studio.output}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
