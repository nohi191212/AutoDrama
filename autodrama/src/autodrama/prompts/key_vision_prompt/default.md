Create one canonical world-establishing image prompt for the configured Gemini image model from the inputs below. The image is a reusable visual anchor for the story world, not an illustration of a screenplay scene.

Worldview brief: {{script_type}}
Visual medium: {{global_visual_style}}
World-level creative brief: {{director_brief}}
Canvas: {{render_contract}}
World-level composition constraints: {{continuity_contract}}
Previous visual audit feedback: {{audit_feedback}}

Use the worldview brief as the only story-derived content. Invent one specific, coherent location that communicates the world's genre, rules, built or natural environment, and visual identity at a glance. Stage it as a premium cinematic medium-wide group composition: not a distant empty panorama, not a close portrait, and not a character sheet.

The image must deliberately showcase the world through people and place in the same frame:
- Include exactly two or three world-native designed figures. They are generic archetypal inhabitants or embodiments of this world, not named screenplay characters or plot roles. Give each a distinct silhouette, costume or body-design language, material treatment, and readable pose.
- Use complementary, non-plot-specific actions that fit the worldview. Make one figure face the camera or turn three-quarters toward it, make another show a clear side profile, and if there is a third figure use a distinct seated, rear three-quarter, or working pose. Keep their faces, silhouettes, and costume details readable without making them dominate the environment.
- Build one continuous spatial system with clear near, middle, and far depth: use nearby architecture, terrain, or vegetation to frame the image; place the designed figures and the main world anchor in the middle distance; show connected background space with sky, horizon, distant structures, terrain, or vegetation.
- Choose architecture, landscape, atmosphere, and one or two world-native generic objects that reveal the world's rules. Make the figures visibly belong to that environment rather than appearing as pasted-in portraits.
- Aim for exceptional visual performance: sophisticated composition, strong silhouette separation, rich but controlled material detail, polished anatomy, atmospheric perspective, motivated cinematic lighting, nuanced color design, and a premium production-art finish.

Keep one continuous location, one camera, one lighting system, and one clear focal hierarchy. Use the supplied visual medium, canvas, lighting logic, materials, atmosphere, and world-level constraints. Previous audit feedback may repair only visible issues in scale, perspective, composition, depth, occlusion, or spatial logic; it must not introduce screenplay events or named characters.

Do not use named characters, recognizable screenplay protagonists, plot events, dialogue, scene extracts, flashbacks, titles, readable text, logos, watermarks, poster symmetry, collage, montage, split scenes, crowd scenes, generic character lineups, or unsupported details. Do not turn the image into a close-up portrait, a battle snapshot, or a decorative spectacle that hides the world. Do not make the figures tiny scale markers.

Write:
Translate genre into concrete visible people, buildings, equipment, and social conditions. Do not include "xianxia", "donghua", or "cyber-wuxia" as image-style cues. When the visual medium calls for photography, describe a camera photograph of real actors in practical costumes in a physically built location. Prefer natural skin, ordinary fabric, normal photographic detail, realistic exposure, and motivated soft fill on faces over production-art or hyper-detailed rendering language. Localize wear to contact points and exposed edges; retain intact surfaces and readable faces. Do not automatically combine heavy smog, universal wetness, deep shadows, and sharp reflections across the whole frame.

Write:
- `shot_contract`: a concise description of camera position and height, viewing direction and shot scale, exact figure count, each figure's blocking/orientation/action, world anchor, near/mid/far layers, focal hierarchy, and why the composition represents this world.
- `scene_style_contract`: a concise description of character silhouette and costume language, palette, atmosphere, motivated key/fill/rim light, visible materials, environmental wear, depth rendering, and restrained optical behavior.
- `prompt`: one direct English image prompt of 240–360 words, ready to send verbatim to the configured Gemini image model. Use these section labels in order: [FRAME INTENT AND MEDIUM], [WORLD ANCHOR AND FIGURE BLOCKING], [ICONIC ENVIRONMENT AND SCALE], [ATMOSPHERE AND VISUAL LANGUAGE], [CAMERA, DEPTH AND COMPOSITION], [MOTIVATED LIGHT, COLOR AND MATERIAL], [OUTPUT CLEANLINESS].

Return only one valid JSON object with exactly these three non-empty string fields: `shot_contract`, `scene_style_contract`, `prompt`. Do not output Markdown, explanations, alternatives, XML, hidden coordinates, model parameters, or reasoning.
