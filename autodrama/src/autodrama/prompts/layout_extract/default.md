Maintain a complete, reusable scene asset table from the script.

Script:
{{novel_full_all_episodes}}

Existing scenes:
{{existing_layouts}}

Only extract concrete spaces that are actually shown on camera and that characters can move through. Exclude people, props, and purely weather / mood / camera / merely-mentioned places. Create one `base` per physical space; write its fixed structure, entrance, circulation, and stable furnishings into `space_features`. Create a `variant` only for a clear visual state change that does not change the spatial topology, and reference that base.

Return the complete `layouts` plus optional short `notes`, strictly conforming to the JSON Schema. Scene names should be short and stable. Content must describe only objective, visible spatial features and state changes — no image prompt phrasing, no technical fields. `episode_keys` may only use keys present in the script. Text like plaques, scrolls, or wall inscriptions may only be described as blurred, illegible marks or patterns.

# Spatial topology (required)

For every `base` scene, write `space_features` as a small ordered set of SELF-CONTAINED sentences that make the structure unambiguous. Each sentence must stand alone (the list may be re-ordered downstream, so never rely on cross-sentence order). Cover, in plain spatial language, for every scene:

- Setting type and anchor structure (e.g. a ridge pass, a courtyard gatehouse, an open forest clearing).
- The main entrance and the approach that reaches it: WHERE it connects from and goes to, and its direction (e.g. "a single stone stair runs from the south forecourt up through the gatehouse doorway to the inner courtyard").
- The fixed architecture and largest set pieces, and by what walkable route they are reached.
- A clear foreground / midground / background layering, with relative depth between elements.
- Ground and elevation change: what the floor is, whether there is a slope / drop / rise, and how much.

# Nearby landmark rule

If the plot places this scene NEAR a sect / monastery / palace / building complex, you MUST state the landmark in `space_features` with its relative direction and the reachable route from the scene (e.g. "the sect's main hall sits north up the ridge, reached by the same stone stair through the gatehouse"). Do NOT leave out a nearby landmark that is implied by the story — describe it, even if only as a distant background mass.

If a scene is genuinely isolated (no nearby landmark), say so explicitly rather than omitting it.

# Readable text rule

Plaques, scrolls, wall inscriptions, and any signage may only appear as blurred, illegible marks or patterns. Never describe readable words.
