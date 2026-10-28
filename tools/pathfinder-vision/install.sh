#!/usr/bin/env bash
# Installs Pathfinder Vision into its own virtual environment, adds a `pathfinder-vision` command and an
# application-menu launcher, and downloads the three model files (about 15 MB). Safe to re-run.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
venv="${PATHFINDER_VISION_VENV:-$HOME/.local/share/pathfinder-vision/venv}"
export PIP_DEFAULT_TIMEOUT="${PIP_DEFAULT_TIMEOUT:-60}" PIP_RETRIES="${PIP_RETRIES:-10}"  # tolerate a flaky connection

echo "Creating the environment in $venv"
python3 -m venv "$venv"
"$venv/bin/pip" install --quiet --upgrade pip
echo "Installing (needs the internet once: mediapipe, numpy, OpenCV)"
"$venv/bin/pip" install --quiet -e "$here"

mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications"
ln -sf "$venv/bin/pathfinder-vision" "$HOME/.local/bin/pathfinder-vision"
cat > "$HOME/.local/share/applications/pathfinder-vision.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Pathfinder Vision
Comment=Camera viewer that shows what is running and what it detects (hands, people)
Exec=$HOME/.local/bin/pathfinder-vision
Terminal=true
Categories=Utility;
DESKTOP

"$venv/bin/pathfinder-vision" --download-models
echo
echo "Done. Run: pathfinder-vision --list-cameras   then   pathfinder-vision --camera N"
echo "(~/.local/bin must be on your PATH; if the command is not found, run $venv/bin/pathfinder-vision instead.)"
