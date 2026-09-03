Write a single image-model prompt, in English, for the character identity board described by the following content, art direction, and framing. The final prompt text must be English.

Character content:
{{roleboard_character_description}}

Art direction:
{{roleboard_style_prompt}}

Framing:
{{roleboard_view_requirement}}

Treat the already-established age, identity, stable appearance, and clothing as fixed facts. For stable visible parts that the material does not specify but a complete character design needs, design a concrete, restrained, non-conflicting completion. For a human, specify the facial shape and bone structure, brow/eye/ nose/lip morphology, age texture, hair silhouette, body proportions, and the clothing outline, structure, and material. For a creature, specify head structure, torso proportions, limbs, surface texture, and stable identifying features. Each look should converge on one clear main silhouette plus one or two restrained identifying details; avoid unfounded standard beauty, generic hero faces, and overly ornate decoration.

Abstract adjectives (delicate, handsome, refine, dignified, elegant, ethereal, etc.) cannot substitute for design; if used, they must be immediately followed by concrete geometry, proportions, silhouettes, or material descriptions. The prompt must describe only the final image and add no plot events, relationships, rank, props, injuries, actions, emotions, or transient effects. Visual style, character image, costume, and material derive only from the current inputs; do not copy a fixed character or clothing template. Briefly write any stable design you completed into the design note of the structured result, not into the final image prompt. Do not write the project, fields, paths, model, interface, workflow, source, or production notes. Output only JSON matching the given JSON Schema.
