"""Launch Splat Studio with the shared, isolated Gaussian runtime."""
import argparse
import json
import os
from http.server import ThreadingHTTPServer
from pathlib import Path
from workspace_splat.server import library_from_env,handler_for

ROOT=Path(__file__).resolve().parent

def create_server(data,port=0, *, env_file=None):
    library=library_from_env(data,env_file=env_file,upload_budget=None,upload_limit=8*1024**3,rebuild_completed=True)
    return ThreadingHTTPServer(('127.0.0.1',port),handler_for(library,ROOT/'web',app_name='splat-studio'))

def main():
    parser=argparse.ArgumentParser(description='Splat Studio · images and video to 3D Gaussians')
    parser.add_argument('--port',type=int,default=0)
    parser.add_argument('--data',type=Path,default=ROOT/'data')
    parser.add_argument('--serve',action='store_true',help='Run only the local backend, without a desktop window')
    parser.add_argument('--ready-file',type=Path,help=argparse.SUPPRESS)
    args=parser.parse_args()
    if not args.serve:
        from .start import launch_desktop
        return launch_desktop(args.data,args.port)
    server=create_server(args.data,args.port)
    if args.ready_file:
        temporary=args.ready_file.with_suffix('.tmp')
        temporary.write_text(json.dumps({'app':'splat-studio','port':server.server_port,'pid':os.getpid()}))
        temporary.replace(args.ready_file)
    print(f'Splat Studio: http://127.0.0.1:{server.server_port}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()

if __name__=='__main__':main()
