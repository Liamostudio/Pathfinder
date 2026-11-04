#!/usr/bin/env bash
# One local entry point; service start/stop and safety checks live in the page.
set -euo pipefail
cd "$(dirname "$0")/.."
launcher_python="workspace_splat/.venv/bin/python"
if [ ! -x "$launcher_python" ]; then
    echo 'Restore workspace_splat/.venv first; see docs/launcher.md.' >&2
    exit 1
fi
mkdir -p recordings/launcher
# Reuse a verified launcher listener; never trust a random process on this port.
if "$launcher_python" - <<'PY'
import json,urllib.request
try:
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://127.0.0.1:8760/api/state',timeout=1) as response:
        value=json.load(response)
    assert value.get('application')=='Pathfinder launcher'
except Exception:raise SystemExit(1)
PY
then
    if command -v xdg-open >/dev/null; then xdg-open http://127.0.0.1:8760/ >/dev/null 2>&1 & fi
    exit 0
fi
"$launcher_python" -u -m lookandpick.launcher > recordings/launcher/launcher.log 2>&1 &
launcher_pid=$!
for launcher_attempt in {1..30}; do
    if ! kill -0 "$launcher_pid" 2>/dev/null; then
        echo 'Launcher could not start. Port 8760 may be occupied; see recordings/launcher/launcher.log.' >&2
        exit 1
    fi
    if "$launcher_python" - <<'PY'
import json,urllib.request
try:
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://127.0.0.1:8760/api/state',timeout=.3) as response:value=json.load(response)
    assert value.get('application')=='Pathfinder launcher'
except Exception:raise SystemExit(1)
PY
    then
        if command -v xdg-open >/dev/null; then xdg-open http://127.0.0.1:8760/ >/dev/null 2>&1 & fi
        echo 'Pathfinder launcher: http://127.0.0.1:8760/'
        exit 0
    fi
    sleep .1
done
echo 'Launcher did not become ready; inspect recordings/launcher/launcher.log.' >&2
exit 1
