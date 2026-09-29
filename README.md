# Olivia Lin — fan-made desktop pet prototype

A lightweight, always-on-top Linux desktop companion based on the public character design of BSide: Olivia Lin. This is an **unofficial fan-made prototype**, not affiliated with BSide or its rights holders. Its portraits are **AI-generated with public image-search references** and resemble those references closely; they should be described as reference-conditioned derivative fan art, not as fully independent or official artwork.

## Run it

On this Linux/X11 workspace, run:

```bash
./run.sh
```

To add a shortcut to this Linux user's Applications menu, run `./install_launcher.sh`; it installs only a `.desktop` shortcut under `~/.local/share/applications/`.

The included `olivia_idle.gif` is a four-second, 48-frame transparent idle loop rendered with Blender from `olivia_idle.png`. `olivia_pet_idle.blend` is the editable Blender scene behind that sprite: it places the generated portrait on a camera-facing image plane and keyframes a gentle float/sway. It is **not** a fully modeled or rigged 3D character. `render_idle.sh` regenerates the Blender scene, frames, and GIF. If the GIF is missing, `./run.sh` renders it on first launch. To skip rendering and use a static portrait with a UI float instead, run `OLIVIA_SKIP_RENDER=1 ./run.sh`.

Requirements on other Debian/Ubuntu Linux desktops: Blender 4.x (optional if the GIF is already present), Python 3, PyQt5, and FFmpeg (only required to render the GIF). This prototype targets X11. A desktop compositor is needed for transparent overlays; Xfce's **Window Manager Tweaks → Compositor** setting was enabled in this workspace. To restore its earlier preference after closing the pet, run `xfconf-query -c xfwm4 -p /general/use_compositing -s false` (restart Xfce's window manager if needed).

## Interactions

- The four new pose buttons switch to **站一站** (**S**), **读一会** (**R**), **弹琴** (**P**), and **发发呆** (**D**). Right-click also opens these pose choices. Press **I** or select **回 idle** to return to the existing idle loop; the selected pose otherwise stays until changed.
- These four actions are **single static, transparent PNG illustrations**—not frame-by-frame body animations. The drawing surface gives them only a tiny one-pixel vertical UI float; there is no animated page turn, piano performance, or pose-transition sequence. The pose row labels them as static.
- The original `olivia_idle.gif` remains the only multi-frame character loop (48 frames over four seconds). The generated blink cel is still shown briefly as a clipped eye overlay; the existing wave/smile portrait remains a still image with a subtle display-scale pulse, not an animated arm wave. Decorative music marks float independently as simple UI drawing.
- Click Olivia or **打招呼** / press **Space** for an offline scripted greeting and the existing smiling, raised-hand still. Choose **眨眨眼** / press **B** for a brief blink overlay. While awake and idle, Olivia also blinks occasionally.
- After about **55 seconds without input**, she may show a short offline idle-response bubble; automatic blink and idle responses pause while resting.
- **听一音** plays a simple system beep as a piano-like cue; it is not a piano or MIDI player. The **弹琴** pose is visual only. **小记事** opens a note field saved locally in `~/.local/share/olivia-desktop-pet/notes.txt`.
- Drag the window to move it; right-click for poses, move, rest/wake, hide, about, or quit. Escape hides; double-click toggles rest mode.

Conversation is limited to a handful of offline scripted lines. There is no account, network connection, AI chat service, official BSide functionality, or persistent autonomous behavior.

## Action-pose preview

The checkerboard contact sheet at [`preview-action-poses.png`](preview-action-poses.png) shows all four accepted transparent pose assets. [`preview-window-reading-final.png`](preview-window-reading-final.png) is an X11 capture of the live transparent desktop window in its reading pose; the browser page behind it is only the test desktop background, not app content or an official BSide screenshot. The same pose asset files live under `assets/poses/` and are described in [PROVENANCE.md](PROVENANCE.md).

The face, bob, charcoal knit top, and silver pendant remain visually keyed to the existing references. The reading pose keeps the reference shorts; standing, piano, and daydream use modest black full-length trousers and flats as an alternate lower-body outfit.

## Portrait provenance and references

Both portrait outputs—`olivia_idle.png` and `olivia_smile.png`—were created with the image generator (`gpt-image-2.5`) from text prompts plus two public image-search reference copies. They were **not direct crops or pasted screenshots**, but they were explicitly conditioned on the references and look unusually close to them. The accurate label is “reference-conditioned AI-generated fan art,” not “original portrait” or “official art.” The app does not display the unmodified reference files.

The optional blink cel `olivia_blink.png` was generated on 2026-09-29 with `gpt-image-2.5` image variation using `olivia_idle.png` as its sole visual reference. The app uses only two small eye regions from this cel as a short overlay; it does not swap in the generated full portrait, because the generation changed details elsewhere in the image.

Exact reference copies supplied to the image generator:

- [`references/search-3.webp`](references/search-3.webp) — [image-search CDN copy](https://files.manuscdn.com/search-media/310519663988616113/DqmMAsaSm5eugGTMDn69Id/TTaWgLsYkft3sgqJv7roC9.webp)
- [`references/search-5.png`](references/search-5.png) — [image-search CDN copy](https://files.manuscdn.com/search-media/310519663988616113/DqmMAsaSm5eugGTMDn69Id/CEnq52vjYDuzWHVNUkDARK.png)

The search returned these cached image URLs rather than canonical page URLs, and the files contain no source metadata that identifies their exact original page. I therefore cannot reliably attribute each cached copy to a specific publisher. The public articles consulted for character context are [Niche Gamer's BSide: Olivia Lin announcement](https://nichegamer.com/bside-olivia-lin-genshin-impact-devs-announce/) and [Inven Global's BSide: Olivia Lin article](https://www.invenglobal.com/articles/22966/mihoyo-moves-beyond-gacha-rpgs-with-ai-driven-bside-olivia-lin); the latter labels an embedded BSide image “©miHoYo.” These pages are context sources, not proof that either is the canonical page for a particular search-cache file.

The visible character in the bundled desktop screenshot is the generated portrait rendered by this prototype. The page visible behind it is only the test desktop's browser window; the pet screenshot is not an official BSide screenshot. See [PROVENANCE.md](PROVENANCE.md) for file paths, hashes, and the exact generation/rendering chain.
