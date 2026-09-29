#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p frames
blender -b --factory-startup --python-exit-code 1 --python render_idle.py -- --input "$PWD/olivia_idle.png" --out "$PWD/frames" --blend-out "$PWD/olivia_pet_idle.blend"
ffmpeg -hide_banner -loglevel error -y -framerate 12 -i frames/frame_%03d.png -vf "scale=430:-1:flags=lanczos,split[s0][s1];[s0]palettegen=reserve_transparent=on:transparency_color=ffffff[p];[s1][p]paletteuse=alpha_threshold=128" -loop 0 olivia_idle.gif
file olivia_idle.gif
