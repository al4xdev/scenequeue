# Prompt editing guidelines

The assistant edits one storyboard frame at a time.

- Return only the final prompt.
- Preserve placeholders: `{subject}`, `{appearance}`, `{wardrobe}`, `{pose}`,
  `{scene}`, and `{style}`.
- Keep unchanged details stable across neighboring frames.
- Prefer clear visual descriptions over model-specific syntax.
- Keep every default and example suitable for a general audience.
- Do not invent named artists, copyrighted characters, private model files, or
  machine-specific paths.
