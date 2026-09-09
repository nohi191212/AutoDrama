# Photographic key-vision comparison

Sources consulted 2026-09-09:

- https://ai.google.dev/gemini-api/docs/image-generation — Photorealistic scenes template: "A photorealistic [type of shot] of a [subject description] in a [setting description]. [Description of the light]. Shot from a [camera angle] with a [lens type]." Candidate A adapts its concise scene-first construction.
- https://cloud.google.com/vertex-ai/generative-ai/docs/image/img-gen-prompt-guide — Photography modifiers: camera proximity/position, lighting, camera settings, lenses and film types. Example: "soft focus photograph of a bridge in an urban city at night". Candidate B prioritizes these capture conditions, without adopting HDR keywords.
- Candidate C is our film-narrative adaptation informed by those photographic principles and the user's brief, NOT a verbatim online template. Sources do not establish efficacy on GPT Image; this experiment tests transfer.

Run `D:/miniforge3/envs/autodrama/python.exe scripts/experiments/key_vision_photo/run.py --dry-run`, then omit `--dry-run` for paid API generation. Use `--candidate a_scene b_camera c_film`, `--repeat 2`, or a new `.tmp/` output directory for independent repeats.

The script injects a frozen PromptStore into PregenWorkflow and calls its actual key_vision_prompt and key_vision_image_generation runners sequentially. Gemini compiles the prompt; the image model/size/quality come unchanged from fangu.yaml. Both exact compiler inputs and final image prompts are saved. No reference images. Original production state and images are not written. Baseline uses the current production template with the SAME experimental brief, not an old differently prompted image.

The shared brief fixes people, blocking, night lighting, clothing and depth. It is an illustrative world-native situation, not a claim about screenplay events. Each run keeps a source-state hash, config hash, node bindings and template snapshot. No credentials are copied. Cache requires matching inputs and an existing image. Interrupted attempts can be retried, potentially incurring another text/image call. No hardcoded aesthetic scores or quality gates: compare skin, light, clothing, background structure, depth and tension by viewing the images. A single sample is exploratory, not statistical proof. Repeat promising candidates before promotion.
