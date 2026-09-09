Write an English image-generation prompt for one world-establishing frame, using the supplied visual medium.
Worldview: {{script_type}}
Visual medium: {{global_visual_style}}
Creative brief: {{director_brief}}
Canvas: {{render_contract}}
Composition constraints: {{continuity_contract}}
Previous visual feedback: {{audit_feedback}}

Use only the supplied worldview for story facts. Depict two or three unnamed adult inhabitants in one coherent location, unless the creative brief specifies the count. Preserve a readable foreground, inhabited middle ground, and distant world context. Translate genre into visible clothing, equipment and architecture, not genre/style buzzwords. Preserve the supplied medium; for photography, describe real actors, practical costumes and physically plausible construction. Do not invent named protagonists or screenplay events.

Write approximately 180-240 words as one observed moment from a live-action dramatic film, never a concept-art specification. Begin with a tangible human action interrupted by something outside the frame, then reveal the inhabited space and distant world. Convey tension through weight, hands and eyelines, not spectacle. Integrate camera and lighting into the prose rather than appending keyword lists. If the supplied medium is photographic, keep skin matte but alive with subtle tonal transitions, fabric ordinary and armor manufactured; avoid cosmetic smoothing and exaggerated pores. Let distant texture disappear with distance, leaving large readable shapes.
Example of the method, not content to copy: "Caught mid-task, a mechanic braces one hand on the bench and turns toward the street without quite standing. A second worker has stopped in the open doorway, watching the same unseen disturbance. They occupy a real workshop overlooking a broad service passage; beyond it, one elevated transit line crosses the dark mass of an administrative block. Warm light from the hooded bench lamp rolls gently across the mechanic's matte cheek and worn cotton collar, while faint blue exterior light finds the edge of his practical wrist brace. The 35mm camera holds him clearly within the larger place; distant windows merge into a few soft lights. Restrained negative-film grain and deep blue night, not a digitally sharpened illustration."

Return only JSON with exactly three nonempty strings: "shot_contract" (brief camera, blocking and depth summary), "scene_style_contract" (brief lighting and material summary), and "prompt" (the final ready-to-use English image prompt). The prompt must be continuous prose without section labels, lists, model parameters or explanations. No readable text, logos, watermark, montage, or character lineup.
