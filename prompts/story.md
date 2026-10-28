TASK – layer 2: story (from text only).

You don't see the video. You get:
- the perception LOG: records per scene (place, the state of each character from the register, action, objects,
  on-screen text, confidence, unclear questions). "not visible" = can't be told; "absent" = not in the scene.
- the character register,
- the speech transcript with timestamps (what can be heard).

Assemble the plot from these. Use only what is in the log or in the speech; don't make anything up.

1. "scenes": the plot by story units (not by cuts): start, end, place, who is present, what happens,
   how the characters' states change.
2. "characters": for each character, how their state develops over time (e.g. 0–270 s free; 272 tied up; 288 untied;
   after 305 no longer in any scene) and their last known state. If a character disappears without explanation, that is a question.
3. "questions": everything that doesn't fit or is missing for the listener to understand the plot:
   - a character changes state without explanation (tied up → running),
   - an object/event that is referred to later has no origin (salt, knife, flashlight),
   - an unclear ending for the main characters,
   - a contradiction between the log and the speech,
   - records with low confidence or "unclear" that matter for the plot.
   - a time jump that is not certain (see 4) or a doubtful identity (see 5).
   For each question give the time range where it should be checked in the video (start, end in s, at most 15 s)
   and an importance of 1 (without it the listener won't understand the plot) / 2 / 3.

4. "time_jumps": scenes that are not in chronological order – a flash-forward (e.g. the film opens with a short
   glimpse of a later fight, then cuts back to the beginning), a flashback, a dream or parallel action.
   Signs: a place, action or character state that appears briefly early and again later as part of a longer sequence;
   a scene that repeats; a character state that jumps back (a bandage present, later not yet present; a wound that
   disappears). For each: start, end, kind, evidence, and "related_to" = the time of the matching later/earlier scene.
5. "same_entity": different labels in the log that are probably the same character or object – e.g. "the girl in a red
   coat" on the street = "the girl" at the window; "a gloved hand" = the burglar's hand. Look for the same
   place, the same props (a scarf, a lantern), the same colour and material, and actions that continue each
   other. For each: the labels, evidence and confidence 0–1. Below 0.7 it is also a question (with a segment).

Write all free-text values in {LANGUAGE}.

Answer ONLY with JSON:
{"scenes": [{"start": 0, "end": 30, "place": "...", "present": ["..."], "events": "..."}],
 "characters": [{"name": "...", "timeline": "...", "last_state": "..."}],
 "questions": [{"question": "...", "start": 0, "end": 10, "importance": 1}],
 "time_jumps": [{"start": 13, "end": 20, "kind": "flash-forward", "evidence": "...", "related_to": 60}],
 "same_entity": [{"labels": ["...", "..."], "evidence": "...", "confidence": 0.8}],
 "segments": [{"start": 0, "end": 10, "reason": "story: question", "suggested_fps": 2, "what_to_find": "...", "importance": 1}]}
"segments" = the questions of importance 1–2 in the shape for a targeted look (with the same "importance" as the question).
A record with action "unclear" and confidence 0 = a hole in the log (perception failed); if it is in the middle of the plot, it is a question of importance 1.
