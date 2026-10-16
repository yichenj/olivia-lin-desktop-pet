# macOS source launch

This guide covers launching the source checkout, window recovery and native desktop checks. Model configuration, chat behavior and the shared test commands are maintained in [Run and test chat](CHAT_TESTING.md) and [Backend and UI](AGENT_BACKEND.md).

## Install and launch

Double-click `Start.command`, or run `./scripts/setup_macos.sh` once followed by `./run.sh`. Python 3.10+ must already be available. Setup checks PATH and the usual Apple Silicon/Intel Homebrew locations, creates the Git-ignored `.venv/`, and installs binary dependencies from PyPI. It does not require administrator privileges or change system Python. Normal launch uses bundled PNGs without Blender or FFmpeg.

The UI starts the local Python backend. Configure the model before chatting, following [configuration and startup](CHAT_TESTING.md#配置和启动). The Terminal window stays open while the source launcher runs; this is not a standalone `.app` bundle, so the Dock/menu name may be Python.

Moving a configured checkout can invalidate its virtual environment. Quit the app before recreating `.venv/` with the setup script if Python or pip no longer resolves.

## Window and recovery controls

The pet uses a transparent, frameless Qt Cocoa window with an always-on-top hint. It uses a normal window rather than Qt's tool-window type, so switching applications does not intentionally hide the character.

- The menu-bar music note provides Show, Hide and Quit.
- Escape hides to the tray when available and otherwise minimizes.
- While hidden, a Carbon hotkey reserves `⌘0`, including keypad zero, for restoration from another app. It is released on restore or quit. Registration failure keeps a minimized Dock window as a recovery route.
- The native Olivia menu provides Show; `⌘Q` quits when Olivia/Python is active.
- Drag the portrait or empty card area to move. The context menu controls automatic activity, fixed poses, dialogue visibility, reconnection, movement, hiding and exit.

The hotkey does not log keystrokes or require Accessibility permission. Hiding the pet or its dialogue bubble does not stop backend work; quitting closes the backend. Dialogue visibility and streaming behavior are defined in [display behavior](AGENT_BACKEND.md#展示行为).

## Native verification

Run the commands in [native UI and previews](CHAT_TESTING.md#原生-ui-与预览) from an unlocked macOS desktop with access to WindowServer. Tests cover native hotkey registration, conflict handling and release, along with shared interaction and chat tests. The offscreen suite skips native hotkey checks and cannot establish desktop integration.

Manual checks should include Finder launch, switching to another app while the character remains visible, hiding and physically pressing `⌘0` from that other app, Dock/menu recovery, context-menu pose selection and quitting after hiding. Check the live compositor for portrait trails and unwanted shadows; rendering a Qt widget into an image is insufficient for that conclusion.

The existing native validation baseline is macOS 14.8 on Apple Silicon with Python 3.13 and PyQt5 5.15.11. It is not evidence that every current change has been revalidated on the desktop. Intel Macs, other macOS versions, multiple displays, Spaces, full-screen applications and Mission Control do not have established coverage. The always-on-top hint is not a promise to overlay full-screen apps or every Space.

## Packaging limits

The source launch does not install a signed/notarized app, DMG, automatic startup or an independent background service. Menu-bar availability depends on QSystemTrayIcon; the native menu and minimized Dock window provide additional recovery paths.
