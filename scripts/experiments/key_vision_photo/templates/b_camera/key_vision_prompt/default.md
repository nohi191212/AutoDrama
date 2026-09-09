Write an English image-generation prompt for one world-establishing frame, using the supplied visual medium.
Worldview: {{script_type}}
Visual medium: {{global_visual_style}}
Creative brief: {{director_brief}}
Canvas: {{render_contract}}
Composition constraints: {{continuity_contract}}
Previous visual feedback: {{audit_feedback}}

Use only the supplied worldview for story facts. Depict two or three unnamed adult inhabitants in one coherent location, unless the creative brief specifies the count. Preserve a readable foreground, inhabited middle ground, and distant world context. Translate genre into visible clothing, equipment and architecture, not genre/style buzzwords. Preserve the supplied medium; for photography, describe real actors, practical costumes and physically plausible construction. Do not invent named protagonists or screenplay events.

Write approximately 180-240 words, starting with photographic capture and light rather than worldbuilding spectacle. Use a plausible single camera position, 35mm lens, focus plane and restrained 35mm negative-film response. Explain the physical source, direction, softness and falloff of the light. Then place people and world details inside that exposure. Background remains a comprehensible location, not a portrait-bokeh smear.
Example of the method, not content to copy: "A 35mm color-negative photograph taken from chest height at the edge of a service bay after dark. Focus falls on the nearest worker and the doorway behind him; the far buildings are softly resolved. A warm shaded fixture above the bench lights one side of his face, with dim blue exterior light separating his shoulder. His cheek has a broad, subdued highlight, fine natural variation and no glossy sheen. Two workers interrupt their task and look toward the street. A raised walkway leads to a few large building masses beneath a dark sky."
Avoid HDR, artificial microcontrast, exaggerated pores, milky lifted blacks and etched distant surfaces.

Return only JSON with exactly three nonempty strings: "shot_contract" (brief camera, blocking and depth summary), "scene_style_contract" (brief lighting and material summary), and "prompt" (the final ready-to-use English image prompt). The prompt must be continuous prose without section labels, lists, model parameters or explanations. No readable text, logos, watermark, montage, or character lineup.
