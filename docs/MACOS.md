# macOS source launch and validation

Double-click `Start.command`, or run `./scripts/setup_macos.sh` once followed by `./run.sh`. The project-local `.venv/` is ignored by Git. Python 3.10+ must already be available; setup checks PATH and the usual Apple Silicon/Intel Homebrew locations and downloads binary PyQt5 packages from PyPI. No administrator privileges or system-Python changes are needed. Normal launch uses the bundled PNGs without Blender or FFmpeg.

The pet is a native Qt Cocoa window with a transparent background, no title bar, and an always-on-top window hint. macOS uses a normal window rather than Qt's tool-window type, which can hide when another application activates. The menu-bar music note provides Show, Hide and Quit. The native Olivia menu provides Show (`⌘0`), and the application menu provides Quit (`⌘Q`). A Carbon hotkey reserves `⌘0` (including keypad zero) while the pet is hidden so restoration also works from another app; it is released on restore/quit. Registration failure keeps a minimized Dock window as a recovery route. No key logging or Accessibility permission is involved. Escape hides to the tray when available and otherwise minimizes. The Dock/menu name may be Python: this launcher is not a standalone `.app` bundle.

Notes are local UTF-8 text at `~/Library/Application Support/Olivia Lin Fan Pet/notes.txt`. Linux retains `~/.local/share/olivia-desktop-pet/notes.txt`; previous files are not automatically moved or overwritten.

## Initial macOS port validation (before the interaction fixes)

Environment: macOS 14.8, arm64, Homebrew Python 3.13.3, PyQt5 5.15.11, Qt reporting 5.15.14.

- Created a fresh project virtual environment with `scripts/setup_macos.sh` and launched the actual desktop application through `Start.command` (Cocoa, not offscreen).
- Opened `Start.command` through Finder as well and confirmed the pet launched; switched to Finder and confirmed the pet's window remained available while the other application was active.
- Used native mouse input to switch standing/reading poses and drag the information card. Inspected the live rendered window; corrected the small provenance badge to fit macOS font metrics.
- Used Escape followed by `⌘0` while targeting the application to hide/restore the real window. This did **not** establish that the old application-only shortcut worked while another app had focus; the later user report exposed that gap. Opened the native Olivia menu and invoked Show. Verified `⌘Q` exits the foreground process cleanly, including after hiding.
- Ran all 17 tests both with the default offscreen backend and explicitly with Cocoa. Tests cover the original interactions, hide/restore and minimize fallback, window attributes, temporary-file note save/reload, and launcher path/interpreter selection including paths containing spaces. Launcher tests mock the interpreter and OS identification; they do not replace the actual launch check above.
- Checked shell syntax for both launchers and setup. The assets are unchanged. Virtual environments, previews and render outputs remain ignored.

Commands:

```bash
bash -n Start.command run.sh scripts/setup_macos.sh
.venv/bin/python -m unittest discover -s tests -v
QT_QPA_PLATFORM=cocoa .venv/bin/python -m unittest discover -s tests -v
```

## Interaction fixes on 2026-09-30

- Removed greeting/blink/pose buttons and old letter-key shortcuts. The compact card contains an editable input field, a submit arrow and an explicit service-not-connected status. Submissions stay in memory; no agent or music backend is implemented.
- Added timed automatic activities and right-click manual pose locks. Automatic changes wait during drafts, dragging, context menus and notes; rest pauses movement and blinking.
- Idle always uses the same PNG, including while blinking. The previous GIF had different portrait framing; returning to the PNG for eye overlays changed body size. Regression tests compare all pixels outside the eye regions and compare a switched pose to a fresh rendering of the same pose.
- Disabled the native drop shadow before window creation, clear the backing surface every frame, and draw an opaque card below the art. This removes the cached-shadow path and portrait bleed behind the controls.
- Added hidden-window Carbon `⌘0` registration, conflict fallback and cleanup, in addition to the native menu recovery route.

Native keyboard/compositor checks require an unlocked desktop. Automated Cocoa checks alone do not prove cross-application keyboard input or desktop composition; keep that distinction when recording results.

## Scope and limits

This validates local source-based use on the Apple Silicon machine above. Intel, other macOS versions, Linux desktop regression, multiple displays, Spaces, full-screen applications and Mission Control have not been exercised here. The stay-on-top hint is not a promise to cover full-screen windows or appear in every Space. Per-window captures and window-attribute checks are not a complete compositor compatibility test. The menu-bar status menu is provided by QSystemTrayIcon; the native Olivia menu/shortcut is an additional recovery route.

No signed/notarized app, DMG installer, automatic startup or background service is installed. Moving an already-configured project may invalidate the virtual environment; recreate `.venv/` with the setup script if Python/pip no longer resolves. Quit the app before removing a virtual environment.

## Verification of the interaction fixes

- All **22 tests passed with Cocoa**. The offscreen run passes 20 tests and skips the two native Carbon tests. Coverage includes blink pixels, clearing an old pose, automatic scheduling/manual locks, text input, native context-menu dispatch, tray/minimize recovery, Dock restoration and Carbon registration/conflict/dispatch/cleanup.
- Read the test window's actual Cocoa `NSWindow.hasShadow` property: **false**. Native window captures showed the new compact card and separate character without an old portrait layer. These captures do not establish compatibility with every desktop compositor/Space.
- On the live app, entered Chinese text and pressed Return; the field cleared and the local-only acknowledgment appeared. Used the real context menu to select and retain reading. Observed automatic pose changes before selecting a manual lock.
- Confirmed Escape hides the live window and application-targeted `⌘0` restores it. The automation tool's Finder-targeted key injection did not trigger the global route; a separate physical-keyboard check was therefore requested. **The user confirmed that hiding with Escape, switching to another app and physically pressing `⌘0` restores Olivia.**
- Shell syntax and `git diff --check` pass. Runtime artwork is unchanged; screenshots and previews remain under ignored `output/`.
