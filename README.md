# Olivia Lin — fan-made desktop pet prototype

A lightweight, always-on-top macOS and Linux/X11 desktop companion based on the public character design of BSide: Olivia Lin. This is an **unofficial fan-made prototype**, not affiliated with BSide or its rights holders. Its portraits are **AI-generated with public image-search references** and resemble those references closely; they should be described as reference-conditioned derivative fan art, not as fully independent or official artwork.

## Run on macOS

Install Python 3.10+ from [python.org](https://www.python.org/downloads/macos/) or Homebrew (`brew install python`) if it is not already installed. Then **double-click `Start.command`** in Finder. The first launch creates `.venv/` inside the project and installs the pinned PyQt5 dependency from PyPI; later launches work offline and reuse that environment. The Terminal window stays open while the pet runs.

The equivalent terminal commands are:

```bash
./scripts/setup_macos.sh
./run.sh
```

The launcher checks the project virtual environment instead of depending on Apple's system Python. `OLIVIA_PYTHON=/path/to/python3 ./run.sh` selects an explicit interpreter. The setup script supports `OLIVIA_PIP_INDEX_URL` for a custom package index. Dependencies are not installed into system Python. Native binary packages are required; setup fails with a pip error rather than attempting a lengthy Qt build if your Python/macOS combination has no compatible wheel.

- Click the menu-bar **music-note icon** to show, hide or quit Olivia.
- **Escape** hides the pet to the menu bar. If no tray is available, it minimizes instead.
- With Olivia/Python active, **⌘0** or **Olivia → 显示 Olivia** restores the window; **⌘Q** quits. The Dock/application menu may be named **Python**, since this is a source launcher, not a standalone packaged app.
- Drag an empty part of the information card to move the window. Right-click (or a trackpad secondary click) opens the pet menu.
- Notes are stored in `~/Library/Application Support/Olivia Lin Fan Pet/notes.txt`. Existing Linux-style notes are not moved automatically.

Native desktop launch and interactions were checked on macOS 14.8 / Apple Silicon with Python 3.13 and PyQt5 5.15.11. Intel Macs use the same launcher but have not been tested here. This is not a signed/notarized `.app` or a cross-Space/full-screen overlay; full-screen apps, Mission Control and multiple-display transitions are not covered by this validation. No Accessibility or Screen Recording permission is required by the pet itself.

## Run on Linux/X11

Install Python 3 and PyQt5 (for example `sudo apt install python3-pyqt5` on Debian/Ubuntu), then run:

```bash
./run.sh
```

To add an Applications-menu shortcut, run `./scripts/install_launcher.sh`. The runtime uses a system-tray menu where supported. Linux/X11 needs a desktop compositor for transparent overlays; macOS uses Qt's native Cocoa backend.

## Idle animation

The included `assets/animations/idle.gif` is a four-second, 48-frame transparent idle loop rendered from `assets/portraits/idle.png`. It moves a camera-facing image plane; it is **not** a fully modeled or rigged 3D character. Normal use does not require Blender or FFmpeg. `./scripts/render_idle.sh` regenerates the GIF and puts disposable frame images and the Blender scene in ignored `build/idle/` (the script targets Blender 4.0's Eevee API).

If the GIF is missing and both Blender and FFmpeg are available, `./run.sh` attempts to render it on first launch. Otherwise the pet falls back to the static idle PNG. `OLIVIA_SKIP_RENDER=1 ./run.sh` skips this generation step; an existing GIF still plays.

## Interactions

- The four new pose buttons switch to **站一站** (**S**), **读一会** (**R**), **弹琴** (**P**), and **发发呆** (**D**). Right-click also opens these pose choices. Press **I** or select **回 idle** to return to the existing idle loop; the selected pose otherwise stays until changed.
- These four actions are **single static, transparent PNG illustrations**—not frame-by-frame body animations. The drawing surface gives them only a tiny one-pixel vertical UI float; there is no animated page turn, piano performance, or pose-transition sequence. The pose row labels them as static.
- The original `assets/animations/idle.gif` remains the only multi-frame character loop (48 frames over four seconds). The generated blink cel is still shown briefly as a clipped eye overlay; the existing wave/smile portrait remains a still image with a subtle display-scale pulse, not an animated arm wave. Decorative music marks float independently as simple UI drawing.
- Click Olivia or **打招呼** / press **Space** for an offline scripted greeting and the existing smiling, raised-hand still. Choose **眨眨眼** / press **B** for a brief blink overlay. While awake and idle, Olivia also blinks occasionally.
- After about **55 seconds without input**, she may show a short offline idle-response bubble; automatic blink and idle responses pause while resting.
- **听一音** plays a simple system beep as a piano-like cue; it is not a piano or MIDI player. The **弹琴** pose is visual only. **小记事** opens a note field saved locally in the platform-specific notes folder (on Linux: `~/.local/share/olivia-desktop-pet/notes.txt`).
- Drag the window to move it; right-click for poses, move, rest/wake, hide, about, or quit. Escape hides to the menu bar/tray (or minimizes if unavailable); double-click toggles rest mode.

Conversation is limited to a handful of offline scripted lines. There is no account, network connection, AI chat service, official BSide functionality, or persistent autonomous behavior.

## Project layout

```text
pet.py                         Application and interaction logic
run.sh                         Shared macOS/Linux launch entry point
Start.command                  Finder double-click launcher
requirements.txt               Pinned Python UI dependency
assets/
  portraits/                   idle.png, smile.png, blink.png
  animations/                  idle.gif (shipped runtime animation)
  poses/                       standing, reading, piano, daydream PNGs
scripts/                       Mac setup, rendering, previews, Linux installation
packaging/linux/               Desktop launcher template
art/references/                Source/reference images, never loaded by the app
docs/
  PROVENANCE.md                Asset origins and revision history
  art/                         Generation prompts and accepted asset hashes
tests/                         Qt interaction tests
output/                        Local previews/candidates, ignored by Git
build/                         Render intermediates, ignored by Git
```

Only approved runtime artwork belongs in `assets/`. The four action poses wear the selected Dior Boy-inspired platform loafers with the earlier shorter shorts and slimmer leg contours. Idle, smile, blink and the idle GIF retain their original artwork. Loading a different folder does not change an image's behavior; `pet.py` selects static portraits, the GIF or the blink overlay by state.

## Generate local previews

With PyQt5 installed (on macOS, use `.venv/bin/python` in place of `python3`):

```bash
python3 scripts/render_pose_previews.py --windows
```

The contact sheet and Qt window captures are written to **`output/previews/`**, which is ignored by Git. An optional `--before-dir PATH` creates a comparison against four previous pose PNGs. `--output-dir` can select another local output folder; keep generated previews under `output/`. Confirmed previews and duplicate try-on candidates can be deleted once their approved assets have been installed. Older local screenshots, if retained, live in `output/previews/archive/`.

The Blender scene and PNG frames under `build/idle/` are also ignored and can be regenerated. The final GIF stays versioned under `assets/animations/` so running the pet does not normally require Blender or FFmpeg.

## Validation

```bash
QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s tests -v
```

These tests check loading, interactions, notes and launcher interpreter/path selection. On macOS, use `.venv/bin/python` in place of `python3`. Use `QT_QPA_PLATFORM=cocoa .venv/bin/python -m unittest discover -s tests -v` on a Mac desktop to run the same suite against native Qt windows. The offscreen suite alone does not validate desktop integration. See [macOS validation](docs/MACOS.md) for the native checks and remaining limits.

## Portrait provenance and references

Both portrait outputs—`assets/portraits/idle.png` and `assets/portraits/smile.png`—were created with the image generator (`gpt-image-2.5`) from text prompts plus two public image-search reference copies. They were **not direct crops or pasted screenshots**, but they were explicitly conditioned on the references and look unusually close to them. The accurate label is “reference-conditioned AI-generated fan art,” not “original portrait” or “official art.” The app does not display the unmodified reference files.

The optional blink cel `assets/portraits/blink.png` was generated on 2026-09-29 with `gpt-image-2.5` image variation using `assets/portraits/idle.png` as its sole visual reference. The app uses only two small eye regions from this cel as a short overlay; it does not swap in the generated full portrait, because the generation changed details elsewhere in the image.

Exact reference copies supplied to the image generator:

- [`art/references/search-3.webp`](art/references/search-3.webp) — [image-search CDN copy](https://files.manuscdn.com/search-media/310519663988616113/DqmMAsaSm5eugGTMDn69Id/TTaWgLsYkft3sgqJv7roC9.webp)
- [`art/references/search-5.png`](art/references/search-5.png) — [image-search CDN copy](https://files.manuscdn.com/search-media/310519663988616113/DqmMAsaSm5eugGTMDn69Id/CEnq52vjYDuzWHVNUkDARK.png)

The search returned these cached image URLs rather than canonical page URLs, and the files contain no source metadata that identifies their exact original page. I therefore cannot reliably attribute each cached copy to a specific publisher. The public articles consulted for character context are [Niche Gamer's BSide: Olivia Lin announcement](https://nichegamer.com/bside-olivia-lin-genshin-impact-devs-announce/) and [Inven Global's BSide: Olivia Lin article](https://www.invenglobal.com/articles/22966/mihoyo-moves-beyond-gacha-rpgs-with-ai-driven-bside-olivia-lin); the latter labels an embedded BSide image “©miHoYo.” These pages are context sources, not proof that either is the canonical page for a particular search-cache file.

Historical desktop screenshots showed generated artwork rendered by this prototype, not an official BSide interface. Screenshots are local review outputs and are no longer versioned. See [provenance record](docs/PROVENANCE.md) for file paths, hashes, and the exact generation/rendering chain.
