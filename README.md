# Olivia Lin — fan-made desktop pet prototype

A lightweight, always-on-top macOS and Linux/X11 desktop companion based on the public character design of BSide: Olivia Lin. This is an **unofficial fan-made prototype**, not affiliated with BSide or its rights holders. Its portraits are **AI-generated with public image-search references** and resemble those references closely; they should be described as reference-conditioned derivative fan art, not as fully independent or official artwork.

## Run on macOS

Install Python 3.10+ from [python.org](https://www.python.org/downloads/macos/) or Homebrew (`brew install python`) if it is not already installed. Then **double-click `Start.command`** in Finder. The first launch creates `.venv/` inside the project and installs the pinned PyQt5 and OpenAI SDK dependencies from PyPI; later launches reuse that environment. Chat requires access to the configured Ark endpoint. The Terminal window stays open while the pet runs.

The equivalent terminal commands are:

```bash
./scripts/setup_macos.sh
./run.sh
```

The launcher checks the project virtual environment instead of depending on Apple's system Python. `OLIVIA_PYTHON=/path/to/python3 ./run.sh` selects an explicit interpreter. The setup script supports `OLIVIA_PIP_INDEX_URL` for a custom package index. Dependencies are not installed into system Python. Native binary packages are required; setup fails with a pip error rather than attempting a lengthy Qt build if your Python/macOS combination has no compatible wheel.

- Click the menu-bar **music-note icon** to show, hide or quit Olivia.
- **Escape** hides the pet to the menu bar. If no tray is available, it minimizes instead.
- **⌘0** restores the hidden window even while another app is active. This global shortcut is reserved only while Olivia is hidden and released on restore/quit; if registration conflicts, Olivia minimizes to the Dock instead. **Olivia → 显示 Olivia** and the music-note menu also restore it; **⌘Q** quits when Olivia/Python is active. The Dock/application menu may be named **Python**, since this is a source launcher, not a standalone packaged app.
- Drag an empty part of the information card to move the window. Right-click (or a trackpad secondary click) opens the pet menu.

Native desktop launch and interactions were checked on macOS 14.8 / Apple Silicon with Python 3.13 and PyQt5 5.15.11. Intel Macs use the same launcher but have not been tested here. This is not a signed/notarized `.app` or a cross-Space/full-screen overlay; full-screen apps, Mission Control and multiple-display transitions are not covered by this validation. No Accessibility or Screen Recording permission is required by the pet itself.

## Run on Linux/X11

Install Python 3.10+, create a virtual environment, and install `requirements.txt` (PyQt5 and the OpenAI-compatible SDK), then run:

```bash
./run.sh
```

To add an Applications-menu shortcut, run `./scripts/install_launcher.sh`. The runtime uses a system-tray menu where supported. Linux/X11 needs a desktop compositor for transparent overlays; macOS uses Qt's native Cocoa backend.

## Everyday activity and input

- Olivia starts in **自动活动**: she randomly switches between idle, standing, reading, piano and daydreaming without repeating the current pose. Activities last roughly 30–140 seconds depending on the pose; reading lasts longer than standing. There are no spontaneous greetings or system beeps.
- Right-click to choose a pose and **keep it**. Select **自动活动** to resume the schedule. Manual selection never times out back to idle.
- Automatic changes wait while you type a draft, drag the pet or use its context menu. Hidden/minimized pets pause automatic activity. Ordinary letter keys and Space belong to the input field.
- Enter or the arrow sends a message to a local backend, which streams an Ark model reply into a comic-style speech bubble beside Olivia. Every input uses the same text-only send path, including while Olivia is speaking. The backend handles new topics and adjusts unfinished replies; the frontend has no task or turn controls. The oval bubble grows with its text and only scrolls at its maximum size. It supports copying and hides after 30 idle seconds; reading pauses the timer. Click **对话**, or use the context menu’s **关闭对话 / 展开对话**, to toggle it. There are no stop-reply controls: just keep talking. The bubble has no close cross; the dialogue toggle hides it without interrupting the reply. Reconnect through the right-click menu if needed. Olivia can delegate background analysis, local file work and shell commands while you continue chatting, then return with the result. It does not play music.
- Chat history is saved in SQLite in the user data directory and reloaded on startup. Messages belong to agents: Olivia is always agent 1, with a per-agent turn counter that survives replies and restarts. User inputs and completed replies enter model context; superseded, failed and interrupted drafts remain in storage but are excluded. Copy `.olivia.example.json` to the ignored `.olivia.local.json` and fill in `api_key`, `base_url` and `model`. No real service URL or endpoint is embedded in code or the example. `ARK_API_KEY`, `ARK_BASE_URL` and `ARK_MODEL` can override the local values. See [chat setup and tests](docs/CHAT_TESTING.md).
- Local execution uses the configured `workspace`. Set `shell_enabled` in the ignored local configuration to enable arbitrary shell commands with your user permissions; the working directory is not a sandbox. Cancelling work stops subsequent steps and its process group, but does not undo completed effects.
- Drag the portrait or card to move the window. Right-click for automatic activity, fixed poses, move, hide and quit. Escape hides to the menu bar/tray, or minimizes if unavailable. Double-clicking no longer changes activity.

All five poses are static transparent PNG illustrations, with a tiny vertical float. Idle blinks automatically every 9–17 seconds using only two clipped eye regions from `blink.png`. The base PNG and its size stay identical during a blink. This is not a rigged character, animated piano performance or page-turn sequence.

The renderer draws only the selected portrait, clears transparent pixels every frame, disables native window shadows and keeps the opaque input card below the portrait. The old idle GIF and its Blender/FFmpeg generation scripts have been removed. All runtime character artwork uses PNG; `smile.png` remains a historical asset and is not loaded at runtime.

## Project layout

```text
pet.py                         Application, activity scheduler and input UI
backend_client.py              Qt JSON-RPC client and backend process lifecycle
speech_bubble.py               Streaming comic-style reply window
backend/                       Service, harness, context provider, SQLite, Ark adapter
prompts/olivia.md               Editable character system prompt
mac_hotkey.py                  macOS hidden-window restore shortcut
run.sh                         Shared macOS/Linux launch entry point
Start.command                  Finder double-click launcher
requirements.txt               Pinned Python UI and model SDK dependencies
assets/
  portraits/                   idle.png, smile.png, blink.png
  poses/                       standing, reading, piano, daydream PNGs
scripts/                       Mac setup, previews, Linux installation
packaging/linux/               Desktop launcher template
art/references/                Source/reference images, never loaded by the app
docs/
  PROVENANCE.md                Asset origins and revision history
  art/                         Generation prompts and accepted asset hashes
tests/                         Backend, transport, UI integration and desktop tests
output/                        Local previews/candidates, ignored by Git
build/                         Render intermediates, ignored by Git
```

Approved character artwork lives in `assets/`; the legacy smile art is retained for provenance. The four action poses wear the selected Dior Boy-inspired platform loafers with the earlier shorter shorts and slimmer leg contours. Idle, smile and blink retain their original artwork. The current renderer selects one PNG by activity, plus the idle eye overlay when blinking.

## Chat architecture

- [Overall design](docs/AGENT_BACKEND.md) — process boundaries, modules and UI behavior.
- [Agent architecture decision](docs/AGENT_ARCHITECTURE.md) — rationale and tradeoffs for continuous conversation with independent background execution; includes the first background scheduler, internal controls and result delivery.
- [Communication protocol](docs/CHAT_PROTOCOL.md) — text-only input, streamed display messages and errors.
- [Result delivery and timers](docs/AGENT_ARCHITECTURE.md#51-subagent-结果暂缓timer-与提前询问) — deferral, early user questions, timer cancellation and restart recovery.
- [History and memory](docs/HISTORY_MEMORY.md) — SQLite schema and replaceable context provider.
- [Persona sources](docs/PERSONA.md) — public character background and fan-written dialogue style.
- [Run and test chat](docs/CHAT_TESTING.md) — setup, offline tests, live backend and UI smoke scripts.

## Generate local previews

With PyQt5 installed (on macOS, use `.venv/bin/python` in place of `python3`):

```bash
python3 scripts/render_pose_previews.py --windows
```

The contact sheet and Qt window captures are written to **`output/previews/`**, which is ignored by Git. An optional `--before-dir PATH` creates a comparison against four previous pose PNGs. `--output-dir` can select another local output folder; keep generated previews under `output/`. Confirmed previews and duplicate try-on candidates can be deleted once their approved assets have been installed. Older local screenshots, if retained, live in `output/previews/archive/`.

The retired GIF and its generation scripts remain recoverable from Git history. Any old render intermediates under `build/` remain ignored and are not used by the app.

## Validation

```bash
QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s tests -v
```

These tests check activity scheduling, manual pose persistence, text entry, pixel-level blink/pose regressions, notes and launcher interpreter/path selection. Native-only tests also exercise Carbon hotkey registration, conflict cleanup and event dispatch. On macOS, use `.venv/bin/python` in place of `python3`. Use `QT_QPA_PLATFORM=cocoa .venv/bin/python -m unittest discover -s tests -v` on a Mac desktop to run the same suite against native Qt windows. The offscreen suite alone does not validate desktop integration. See [macOS validation](docs/MACOS.md) for the native checks and remaining limits.

## Portrait provenance and references

Both portrait outputs—`assets/portraits/idle.png` and `assets/portraits/smile.png`—were created with the image generator (`gpt-image-2.5`) from text prompts plus two public image-search reference copies. They were **not direct crops or pasted screenshots**, but they were explicitly conditioned on the references and look unusually close to them. The accurate label is “reference-conditioned AI-generated fan art,” not “original portrait” or “official art.” The app does not display the unmodified reference files.

The optional blink cel `assets/portraits/blink.png` was generated on 2026-09-29 with `gpt-image-2.5` image variation using `assets/portraits/idle.png` as its sole visual reference. The app uses only two small eye regions from this cel as a short overlay; it does not swap in the generated full portrait, because the generation changed details elsewhere in the image.

Exact reference copies supplied to the image generator:

- [`art/references/search-3.webp`](art/references/search-3.webp) — [image-search CDN copy](https://files.manuscdn.com/search-media/310519663988616113/DqmMAsaSm5eugGTMDn69Id/TTaWgLsYkft3sgqJv7roC9.webp)
- [`art/references/search-5.png`](art/references/search-5.png) — [image-search CDN copy](https://files.manuscdn.com/search-media/310519663988616113/DqmMAsaSm5eugGTMDn69Id/CEnq52vjYDuzWHVNUkDARK.png)

The search returned these cached image URLs rather than canonical page URLs, and the files contain no source metadata that identifies their exact original page. I therefore cannot reliably attribute each cached copy to a specific publisher. The public articles consulted for character context are [Niche Gamer's BSide: Olivia Lin announcement](https://nichegamer.com/bside-olivia-lin-genshin-impact-devs-announce/) and [Inven Global's BSide: Olivia Lin article](https://www.invenglobal.com/articles/22966/mihoyo-moves-beyond-gacha-rpgs-with-ai-driven-bside-olivia-lin); the latter labels an embedded BSide image “©miHoYo.” These pages are context sources, not proof that either is the canonical page for a particular search-cache file.

Historical desktop screenshots showed generated artwork rendered by this prototype, not an official BSide interface. Screenshots are local review outputs and are no longer versioned. See [provenance record](docs/PROVENANCE.md) for file paths, hashes, and the exact generation/rendering chain.
