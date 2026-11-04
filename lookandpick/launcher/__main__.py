"""python -m lookandpick.launcher — local, simulator-only demo launcher."""
import signal
import threading
from .app import Launcher, make_server


def main():
    app=Launcher();server=make_server(app)
    def stop(*_):threading.Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    print('Pathfinder launcher: http://127.0.0.1:8760/ · simulator only; real arm stays unpowered',flush=True)
    try:server.serve_forever()
    finally:server.server_close();app.close()

if __name__=='__main__':main()
