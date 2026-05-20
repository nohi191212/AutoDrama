# Task

Create a detailed English sound-and-music prompt for generating an instrumental shot-level background audio bed.

The output will be sent directly to ElevenLabs Music Compose. It must describe sound, music, ambience, rhythm, transitions, and mix movement for this exact shot. Do not include spoken dialogue, lyrics, subtitles, character lines, or narration.

# Inputs

Project title: {{title}}

Episode key: {{episode_key}}

Shot id: {{shot_id}}

Shot title: {{shot_title}}

Shot duration: {{duration_seconds}} seconds

Shot transition: {{transition}}

Video prompt:
{{video_prompt}}

Reference-frame prompt:
{{ref_frame_prompt}}

Dialogue text to exclude from audio:
{{dialogue}}

Visual style:
{{visual_style_prompt}}

# Output Requirements

Return only JSON matching the schema.

`sound_description` must be English and must be usable as a direct music-generation prompt.

Required content for `sound_description`:

- Start with a one-sentence overall direction: genre, mood, instrumentation/sound palette, and that it is instrumental with no vocals and no lyrics.
- Then write timed segments that cover the complete shot duration, using the format `[0.0-3.0s] ... [No cut]`, `[3.0-6.0s] ... [J-Cut]`, `[6.0-{{duration_seconds}}s] ... [Hard cut]`.
- Time ranges must be continuous, must not overlap, and should align with the `video_prompt` timing if present.
- Focus on music, ambience, sound design, low-frequency movement, risers, impacts, reverbs, texture, stereo space, and emotional energy.
- Do not include spoken lines, voice acting, dialogue, readable text, lyrics, subtitle timing, or character speech.
- Mention sound effects only as musical/sound-design elements when useful, such as wind, drones, cloth rustle, metallic resonance, electrical hum, crystal particles, thunder-like impacts, or silence.
- End with a concise final mix note for dynamics and ending state.
