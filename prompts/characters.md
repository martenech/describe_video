TASK – character register (before writing descriptions).

You get all overview grids, the speech transcript with timestamps and the closing credits (if they are in the grids).
Build a register of all characters who recur in the story or speak.

For each character:
- "name": the name from the credits or from the speech (when someone addresses them); otherwise a fixed label ("man in red").
- "appearance": 3–6 words the listener can tell them apart by ("tall blond guy in a knitted vest").
- "first_seen": the time (s) when they are first clearly visible.
- "name_source": where the name comes from – "credits", "addressed in speech HH:MM (who says it, who they are looking at)",
  or "guess" with a reason. Don't firmly assign a name you can't match to an appearance.
- "confidence": 0–1.
- "voice": if the transcript and the picture show which lines are theirs (e.g. "speaks 0:27–0:33").

Write all free-text values in {LANGUAGE}.

Answer ONLY with JSON:
{"characters": [{"name": "...", "appearance": "...", "first_seen": 12.5, "name_source": "...", "confidence": 0.9, "voice": "..."}]}
