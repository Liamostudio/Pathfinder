#!/usr/bin/env bash
# Run every test suite in the environment it needs. From the repository root:  scripts/test_all.sh
#   1. workspace environment: Codex's workspace/voxel/splat tests plus the whole of tests/ (core, models, arm, calibration)
#   2. simulation environment: the MuJoCo simulation tests and the kinematics-vs-MuJoCo check (needs mujoco)
#   3. JavaScript: the workspace and voxel front-end maths
# None of these touch hardware.
set -u
cd "$(dirname "$0")/.."
status=0
run() { echo; echo "== $1"; shift; "$@" || status=1; }
run "workspace environment (workspace_splat, gaussian_studio, tests/)" \
    workspace_splat/.venv/bin/python -m pytest gaussian_studio/tests workspace_splat/tests tests -q -p no:cacheprovider
if [ -x simulation/.venv/bin/python ]; then
    run "simulation environment (MuJoCo)" \
        simulation/.venv/bin/python -m pytest simulation/tests tests/test_kinematics.py -q -p no:cacheprovider
fi
if command -v node >/dev/null 2>&1; then
    run "JavaScript" node --test tests/workspaces/*.test.mjs workspace_splat/tests/viewer.test.mjs
else
    echo; echo "== JavaScript: SKIPPED, node is not on PATH. The front-end maths tests were NOT run, so treat them as unverified here."
fi
echo; [ "$status" -eq 0 ] && echo "ALL SUITES PASSED" || echo "SOME SUITES FAILED"
exit "$status"
