"""AI Drama Generator - core logic for generating dramatic scripts using OpenAI."""

import json

from openai import OpenAI


def generate_drama(
    premise: str,
    characters: list[str],
    genre: str = "tragedy",
    num_scenes: int = 3,
    api_key: str | None = None,
) -> dict:
    """Generate a dramatic script based on the given premise and characters.

    Args:
        premise: The central conflict or scenario for the drama.
        characters: A list of character names to appear in the drama.
        genre: The dramatic genre (e.g. tragedy, comedy, thriller).
        num_scenes: Number of scenes to generate.
        api_key: OpenAI API key. Falls back to OPENAI_API_KEY env variable.

    Returns:
        A dict with keys ``title``, ``genre``, ``characters``, and ``scenes``.
        Each scene is a dict with ``title`` and ``dialogue`` keys.
    """
    client = OpenAI(api_key=api_key) if api_key else OpenAI()

    character_list = ", ".join(characters) if characters else "Protagonist, Antagonist"
    prompt = (
        f"Write a short {genre} drama script titled appropriately.\n"
        f"Premise: {premise}\n"
        f"Characters: {character_list}\n"
        f"Structure: exactly {num_scenes} scenes.\n\n"
        "Format your response as valid JSON with this structure:\n"
        "{\n"
        '  "title": "...",\n'
        '  "genre": "...",\n'
        '  "characters": ["..."],\n'
        '  "scenes": [\n'
        '    {"title": "Scene 1: ...", "dialogue": "..."}\n'
        "  ]\n"
        "}\n"
        "Keep dialogue vivid and theatrical. Return ONLY the JSON, no extra text."
    )

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a world-class playwright. Write dramatic, emotionally "
                    "compelling scripts. Always respond with valid JSON only."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.9,
    )

    raw = response.choices[0].message.content
    drama = json.loads(raw)
    return drama
