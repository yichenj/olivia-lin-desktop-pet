#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p build/idle/frames assets/animations
blender -b --factory-startup --python-exit-code 1 --python scripts/render_idle.py -- --input "$PWD/assets/portraits/idle.png" --out "$PWD/build/idle/frames" --blend-out "$PWD/build/idle/scene.blend"
ffmpeg -hide_banner -loglevel error -y -framerate 12 -i build/idle/frames/frame_%03d.png -vf "scale=430:-1:flags=lanczos,split[s0][s1];[s0]palettegen=reserve_transparent=on:transparency_color=ffffff[p];[s1][p]paletteuse=alpha_threshold=128" -loop 0 assets/animations/idle.gif
file assets/animations/idle.gif
