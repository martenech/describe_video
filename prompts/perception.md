TASK – layer 1: perception (a structured log, not prose).

You get a grid of frames, the list of scenes in the grid (which cells belong to which scene) and the character register.
You don't write a story or an audio description – you only record what can be seen in the frames. Later, another
model that doesn't see the video will assemble the plot from this log. Whatever you leave out of the log doesn't exist for it.

For EVERY scene in the grid (and another record when something changes within a scene) fill in ALL fields:
- "start", "end": time in seconds (from the header / list of scenes).
- "place": where it is ("forest clearing, night"), or "unclear".
- "characters": one value for EVERY character from the register:
    their state in your own words ("tied up, pine cone in mouth", "running off to the left"), or "absent" (the scene
    clearly doesn't contain them), or "not visible" (they may be there but it can't be told – darkness, wide shot, from behind).
  New characters who are not in the register go into "new_characters" with a description.
- "action": what happens, concretely, in order ("A hands B a knife; B hides it"). If it can't be told: "unclear".
- "objects": important objects (only those something happens with or that stand out).
- "onscreen_text": verbatim what is legible; "illegible" if there is text but it can't be read;
  null if there is no text.
- "shot": wide / medium / close-up / overhead / point of view…
- "confidence": 0–1 for the whole record.
- "unclear": a list of concrete questions these frames can't answer ("what is she holding?").

Don't make things up. Better "unclear" / "not visible" and a question than invented content.

Write all free-text values in {LANGUAGE}. The sentinel values "absent", "not visible", "unclear" and "illegible"
stay exactly as written here (in English).

Answer ONLY with JSON:
{"records": [{"start": 12.0, "end": 18.5, "scene": 4, "place": "...",
  "characters": {"Ellie": "kneels by a snail, drawing", "Phil": "absent"},
  "new_characters": [], "action": "...", "objects": ["..."], "onscreen_text": null,
  "shot": "close-up", "confidence": 0.9, "unclear": []}]}
