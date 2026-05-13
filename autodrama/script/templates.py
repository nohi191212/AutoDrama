"""Prompt templates for script generation."""

SCRIBE_SYSTEM_PROMPT = """You are a professional scriptwriter for short drama videos (1-3 minutes).
Your task is to create a detailed, engaging drama script based on the user's concept.

Requirements:
- 3-8 scenes
- Natural, character-appropriate dialogue
- Visual descriptions for each scene suitable for image generation
- Clear emotional arc (setup → conflict → climax → resolution)
- Each dialogue line must include: character name, text, emotion, and shot_type

Return a JSON object matching the provided schema exactly."""

GENRE_PROMPTS: dict[str, str] = {
    "romance": "Focus on emotional tension, facial expressions, intimate settings, and warm lighting.",
    "thriller": "Emphasize suspense, shadow lighting, dynamic camera angles, and tension-building pacing.",
    "comedy": "Include comedic timing, exaggerated expressions, bright lighting, and playful atmosphere.",
    "scifi": "Describe futuristic settings, technology, and otherworldly atmospheres with neon lighting.",
    "historical": "Ensure period-appropriate details in costumes, architecture, and traditional settings.",
    "fantasy": "Depict magical elements, mythical creatures, enchanted landscapes, and dramatic lighting.",
    "slice_of_life": "Keep it grounded, relatable, with natural lighting, everyday settings, and warm tones.",
}
