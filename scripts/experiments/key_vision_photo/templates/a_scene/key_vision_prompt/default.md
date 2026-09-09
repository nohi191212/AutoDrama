Write an English image-generation prompt for one world-establishing frame, using the supplied visual medium.
Worldview: {{script_type}}
Visual medium: {{global_visual_style}}
Creative brief: {{director_brief}}
Canvas: {{render_contract}}
Composition constraints: {{continuity_contract}}
Previous visual feedback: {{audit_feedback}}

Use only the supplied worldview for story facts. Depict two or three unnamed adult inhabitants in one coherent location, unless the creative brief specifies the count. Preserve a readable foreground, inhabited middle ground, and distant world context. Translate genre into visible clothing, equipment and architecture, not genre/style buzzwords. Preserve the supplied medium; for photography, describe real actors, practical costumes and physically plausible construction. Do not invent named protagonists or screenplay events.

Use a concise photographic scene description, approximately 150-210 words. Open with the type of photograph, subjects and setting. Describe one readable action and the space around it, then motivated light, camera angle and lens. Prefer a small number of specific visible details over adjective stacks.
Example of the writing method, not content to copy: "A candid wide-angle photograph of two night-shift workers beside an open service bay. One has stopped fastening a cuff and looks beyond the camera; the other pauses in the doorway. A shaded work lamp gives the nearer face warm, soft illumination while the open street adds a faint cool edge. Intact cotton folds and matte skin remain ordinary and unretouched. Photographed at chest height with a 35mm lens; the workers are clear, the buildings beyond lose detail naturally."

Return only JSON with exactly three nonempty strings: "shot_contract" (brief camera, blocking and depth summary), "scene_style_contract" (brief lighting and material summary), and "prompt" (the final ready-to-use English image prompt). The prompt must be continuous prose without section labels, lists, model parameters or explanations. No readable text, logos, watermark, montage, or character lineup.
