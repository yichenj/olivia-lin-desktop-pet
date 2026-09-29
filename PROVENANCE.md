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

- On 2026-09-29, all four accepted pose sprites were refined with the built-in image-variation tool using `gpt-image-2.5` at medium quality. Each edit used its prior accepted pose as the base; the original accepted reading pose informed the shared shorts styling, while `olivia_idle.png` and `references/search-5.png` supplied identity/style context. The four edits completed successfully; no safety rejection was returned for these edits. Earlier rejected image-generation attempts did not contribute to the delivered files and were not used to bypass the safety system.
- The edits changed only the lower-body wardrobe: all four poses now use black denim everyday shorts and lightweight, low-profile charcoal canvas sneakers with slim pale soles. The prompts explicitly requested safe, non-sexual daily clothing and preservation of face, bob, charcoal knit top, silver pendant, pose, proportions, and action props. These outputs are **reference-conditioned AI-generated fan art**, not official art or independently designed character assets.
- The accepted transparent PNGs are standalone image-variation outputs, approximately 1024 × 1536 pixels each. No supplied reference was cropped into a pose, no face was manually composited, and no body was drawn in code. The app renders the resulting images on its transparent Qt surface.
- All four images were checked in a checkerboard overview for full-body framing, outfit continuity, hands, feet, shorts, shoe shape/details, props, and transparent edges at both asset and app-display scale. `preview-action-poses.png` is the updated contact sheet. `preview-window-standing-final.png` and the three matching pose previews are Qt-rendered app-window captures composited over a neutral test desktop background, not captures of a browser page or official BSide interface.
- Each PNG is **one static pose**, not an animation cel sequence. There is no generated book-page turn or piano-playing animation. The idle GIF and previously documented blink overlay remain separate and unchanged.

## 2026-09-29 lower-body revision review — not adopted

- Two full-pose edit batches were tested with the built-in image-variation tool, `gpt-image-2.5`, medium quality. Each pose PNG was the composition base, with `references/search-3.webp` as the original outfit-style guide. A separate standing lower-body crop was also tested to isolate shorts and leg edits.
- The generated candidates consistently changed the sneakers to recognizable loafers, but did not reliably shorten the shorts or slim the legs across all four poses. The crop experiment changed body/clothing details without preserving the desired proportions. The candidates failed the requested quality gate and were not promoted, copied into `assets/poses/`, or included in the contact sheet or window previews.
- Consequently, no new accepted pose PNGs or new pose hashes exist. The four PNGs and preview files listed above remain the prior accepted revision; their existing SHA-256 values below were revalidated on 2026-09-29. Temporary review candidates are not part of the project deliverables.

| Pose | Project-relative path | SHA-256 |
|---|---|---|
| Standing | `assets/poses/standing.png` | `5c7b0d37e971b1cbd953974af6031fe52284a98645c2af3171e3429ee3aa683d` |
| Reading | `assets/poses/reading.png` | `4bc129294cdbe0391be4366c0e6767c67b83d8e040eb41fae1a96c93813cc6a8` |
| Piano | `assets/poses/piano.png` | `e83030d0cde8def512929227c1e1de4d83972a421fe9e7bba4249d80f6c7a994` |
| Daydream | `assets/poses/daydream.png` | `f1e2fa683c9bde45d69ef2612903a377ea86c570cb0572799d19fbb399784a60` |
