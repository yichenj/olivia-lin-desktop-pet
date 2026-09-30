# Dior Boy footwear study

Adopted after user review: the approved standing try-on and three matching seated edits are installed in `assets/poses/`. Generated with built-in image_gen using the preceding sprites, approved standing try-on and official Dior product photo. Outputs are reference-conditioned fan art, not exact product geometry or pixel-locked edits. The confirmed comparison and duplicate standing try-on were removed after adoption. Generate fresh previews under ignored `output/previews/`.

Product: https://www.dior.com/en_us/fashion/products/KDB862ACA_S900

Reference photo: https://assets.christiandior.com/is/image/diorprod/KDB862ACAS900_E03?%24default_GHC%24=&crop=397%2C883%2C1206%2C667

The reference photo belongs to Dior and is stored at `art/references/dior-boy.jpg` only as the generation reference.

The three seated edits' exact prompts are in [seated-prompts.md](seated-prompts.md).

## Standing prompt

Use case: precise-object-edit / identity-preserve. Create a shoe try-on study.
Image 1 is the EDIT TARGET: the current adult Olivia standing sprite. Image 2 is the exact official Dior Boy Platform women's loafer footwear reference.
Replace ONLY BOTH SHOES in image 1 with an accurately proportioned pair of image 2's black brushed calfskin Dior Boy platform loafers: slightly squared round moccasin toe with distinctive apron stitching, broad leather saddle strap, small gold-lettered black leather Christian Dior Paris nameplate (not a triangle), thick black matte EVA lug sole with 5.5cm rear platform and thick forefoot, relatively level platform stance. Match the shoe reference's geometry and detailing, adapted to each foot's existing direction and perspective. Normal realistic women's shoe size, not oversized, do not lengthen feet. Soft leather highlights from the scene lighting rather than artificially bright white reflections.
Keep her entire face, hair, expression, necklace, top, hands, shorts, hips and leg silhouettes, pose, leg length, lighting, camera position, and placement exactly unchanged. Keep bare ankles with no socks. This is only a footwear comparison, not a body revision.
Output one full-body 1024x1536 PNG with true transparent background and clean alpha, preserve full figure and both shoes inside canvas. No background, no grid, no new props or text labels.
