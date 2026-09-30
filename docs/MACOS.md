# macOS source launch and validation

Double-click `Start.command`, or run `./scripts/setup_macos.sh` once followed by `./run.sh`. The project-local `.venv/` is ignored by Git. Python 3.10+ must already be available; setup checks PATH and the usual Apple Silicon/Intel Homebrew locations and downloads binary PyQt5 packages from PyPI. No administrator privileges or system-Python changes are needed. Normal launch uses the bundled images and GIF without Blender or FFmpeg.

The pet is a native Qt Cocoa window with a transparent background, no title bar, and an always-on-top window hint. macOS uses a normal window rather than Qt's tool-window type, which can hide when another application activates. The menu-bar music note provides Show, Hide and Quit. The native Olivia menu also provides Show (`⌘0` while the application is active), and the application menu provides Quit (`⌘Q`). Escape hides to the tray when available and otherwise minimizes. The Dock/menu name may be Python: this launcher is not a standalone `.app` bundle.

Notes are local UTF-8 text at `~/Library/Application Support/Olivia Lin Fan Pet/notes.txt`. Linux retains `~/.local/share/olivia-desktop-pet/notes.txt`; previous files are not automatically moved or overwritten.

## Validation performed on 2026-09-30

Environment: macOS 14.8, arm64, Homebrew Python 3.13.3, PyQt5 5.15.11, Qt reporting 5.15.14.

- Created a fresh project virtual environment with `scripts/setup_macos.sh` and launched the actual desktop application through `Start.command` (Cocoa, not offscreen).
- Opened `Start.command` through Finder as well and confirmed the pet launched; switched to Finder and confirmed the pet's window remained available while the other application was active.
- Used native mouse input to switch standing/reading poses and drag the information card. Inspected the live rendered window; corrected the small provenance badge to fit macOS font metrics.
- Used Escape followed by `⌘0` to hide/restore the real window. Opened the native Olivia menu and invoked Show. Verified `⌘Q` exits the foreground process cleanly, including after hiding.
- Ran all 17 tests both with the default offscreen backend and explicitly with Cocoa. Tests cover the original interactions, hide/restore and minimize fallback, window attributes, temporary-file note save/reload, and launcher path/interpreter selection including paths containing spaces. Launcher tests mock the interpreter and OS identification; they do not replace the actual launch check above.
- Checked shell syntax for both launchers and setup. The assets are unchanged. Virtual environments, previews and render outputs remain ignored.

Commands:

```bash
bash -n Start.command run.sh scripts/setup_macos.sh
.venv/bin/python -m unittest discover -s tests -v
QT_QPA_PLATFORM=cocoa .venv/bin/python -m unittest discover -s tests -v
```

## Scope and limits

This validates local source-based use on the Apple Silicon machine above. Intel, other macOS versions, Linux desktop regression, multiple displays, Spaces, full-screen applications and Mission Control have not been exercised here. The stay-on-top hint is not a promise to cover full-screen windows or appear in every Space. Per-window captures and window-attribute checks are not a complete compositor compatibility test. The menu-bar status menu is provided by QSystemTrayIcon; the native Olivia menu/shortcut is an additional recovery route.

No signed/notarized app, DMG installer, automatic startup or background service is installed. Moving an already-configured project may invalidate the virtual environment; recreate `.venv/` with the setup script if Python/pip no longer resolves. Quit the app before removing a virtual environment.
