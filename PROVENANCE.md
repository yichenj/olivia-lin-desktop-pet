# Portrait provenance — Olivia Lin fan pet

## Plain-language status

The two portraits in this project are **AI-generated, reference-conditioned fan art**. They should not be described as fully independent/original character designs or as official art. Their face is strongly similar to the public BSide reference imagery; that resemblance is expected given the explicit visual conditioning, and it may make them derivative of the referenced material.

## Generation and rendering chain

- `olivia_idle.png` and `olivia_smile.png` were generated on 2026-09-29 with the app's image generator, model `gpt-image-2.5`.
- Both generation calls supplied the same two image-search copies below as visual references, plus English prompts requesting a short dark bob, charcoal-black knit top, small silver pendant, and two related portrait poses.
- I did **not** download either supplied reference and crop/paste it into the portraits; I did not manually draw, trace, or composite the faces. The generation model made new raster files conditioned on those references. Because the likeness is very close, “new raster output” should not be confused with “independent/original design.”
- Blender 4.0.2 imported `olivia_idle.png` as a texture on a camera-facing plane and rendered the 48-frame float/sway loop. `olivia_pet_idle.blend` stores that editable image-plane scene; it is not a 3D character model or facial rig.
- The desktop preview (`desktop-pet-preview.png`) shows the generated portrait rendered by the prototype over a browser page on this Linux workspace. It does not paste an unmodified BSide screenshot into the app.

## Exact image-search reference copies supplied to generation

| Local reference file | SHA-256 | Exact image URL returned by image search |
|---|---|---|
| `/workspace/olivia-desktop-pet/references/search-3.webp` | `7ce12c7df9abeec4eddbd87d82b0772ddfc10e967c0dd1a3ee0284b19962e6cc` | [search image copy 3](https://files.manuscdn.com/search-media/310519663988616113/DqmMAsaSm5eugGTMDn69Id/TTaWgLsYkft3sgqJv7roC9.webp) |
| `/workspace/olivia-desktop-pet/references/search-5.png` | `f31dcaad87f7c4f48b67554ff354f79dc0dbee6e9e8e38e661235e1a95685cbe` | [search image copy 5](https://files.manuscdn.com/search-media/310519663988616113/DqmMAsaSm5eugGTMDn69Id/CEnq52vjYDuzWHVNUkDARK.png) |

These are cached images returned by image search, not stable publisher-hosted source URLs. Their files contain no useful author/source metadata, and the search results did not provide a canonical page URL for either exact cached copy. I cannot reliably map each cache file to an individual publisher page, so I am not claiming the URLs above are the original hosts.

## Public pages consulted for context

- [Niche Gamer — “BSide: Olivia Lin is an AI companion from the creators of Genshin Impact”](https://nichegamer.com/bside-olivia-lin-genshin-impact-devs-announce/) — describes Olivia's music/piano and psychology premise. The page's browser view was blocked by a security challenge, so its text was reviewed through the public page extraction.
- [Inven Global — “miHoYo Moves Beyond Gacha RPGs with AI-Driven ‘BSide: Olivia Lin’”](https://www.invenglobal.com/articles/22966/mihoyo-moves-beyond-gacha-rpgs-with-ai-driven-bside-olivia-lin) — the opened article describes the character/music premise and labels an embedded BSide image “©miHoYo.” That credit applies to the image as labeled on Inven's page; it does not prove that either cached image-search input is that exact embedded image.

The portrait outputs are visibly intended to evoke Olivia and closely resemble public character images. Do not represent them as official assets, and do not describe their design as independent of those references.

## Generated portrait file hashes

- `/workspace/olivia-desktop-pet/olivia_idle.png` — `5a9ba6efb80a651f56cc0e1979561f3fed1e3cd62571060b4f2ff2a20460bbc0`
- `/workspace/olivia-desktop-pet/olivia_smile.png` — `cf619852fce2c6eb5d1786ae5ecd143dc6d3e7d8bea73d6ca7f78fb84956114d`

The distributable ZIP includes the generated portrait files and the provenance statement, but **does not bundle the two image-search reference copies**; the source URLs are recorded above.

## Added blink cel for gesture interactions

- `olivia_blink.png` was generated on 2026-09-29 with the built-in image-variation generator using model `gpt-image-2.5` at medium quality. Its sole input/reference was the existing `olivia_idle.png`; no external image was supplied for this addition.
- The English edit prompt requested the same transparent full-body seated portrait with only both eyelids closed, preserving identity, pose, clothing, lighting, and framing. The actual output also changed some pixels outside the eyes, so the app deliberately clips the new cel to two small eye regions and never displays it as a replacement portrait.
- This is a single generated closed-eye cel shown briefly over the original idle illustration, not a multi-frame facial rig or a complex animation. The blink button is hidden if this optional asset is unavailable.
- SHA-256 for `olivia_blink.png`: `54e22ddb500306514f83e188e46a94a195376cb48a5d9941e134a4e109156338`.

## Added full-body action-pose sprites

- Four transparent PNG pose illustrations were generated on 2026-09-29 with the built-in image generator, model `gpt-image-2.5`, medium quality. Each call supplied the existing generated `olivia_idle.png` and `olivia_smile.png` as visual references to maintain Olivia's face, bob, dark ribbed top, silver cross pendant, lighting, and 2.5D rendering style. The pose outputs are **reference-conditioned AI-generated fan art**, not official art or independently designed character assets.
- The accepted poses are standalone image-generation outputs. No supplied reference was cropped into a pose, no face was manually composited, and no geometric body was drawn in code. The app renders the resulting PNGs on its transparent Qt surface.
- The first standing, piano, and daydream requests using the reference shorts/bare-leg presentation were rejected by the image generator's safety system and produced no assets. Those prompts were not used as deliverables. They were regenerated with an opaque black full-length trouser outfit and closed shoes; the accepted reading pose retains the reference shorts. This changes the lower-body clothing in three sprites while keeping the face, hair, top, pendant, and overall render style consistent.
- All four accepted images were visually checked for face/outfit continuity, full-body framing, limb/prop plausibility, native transparent edges over a checkerboard, and legibility at the desktop-pet display size. `preview-action-poses.png` is the checkerboard review contact sheet; `preview-window-reading-final.png` is a live-window X11 screenshot over the computer's browser test background.
- Each PNG is **one static pose**, not an animation cel sequence. There is no generated book-page turn or piano-playing animation. The idle GIF and previously documented blink overlay remain separate and unchanged.

| Pose | Project-relative path | SHA-256 |
|---|---|---|
| Standing (alternate black trousers) | `assets/poses/standing.png` | `0e4a330f5de0ba4685d7aa3076b1e651970bcae5f5e08e6331c04d82830cb7ad` |
| Reading | `assets/poses/reading.png` | `81245cd02b7881747c30588048e9c661bf75e89556dd04e209fa193b561aa953` |
| Piano (alternate black trousers) | `assets/poses/piano.png` | `d5a8cfd183bc139b91bf3ea78b75e1f36c16ef3098d782db08b858e43772699f` |
| Daydream (alternate black trousers) | `assets/poses/daydream.png` | `ed73d195bbd67caa9d438f8ac795d17b603056e0761b74826f3a38f64353464` |
