TASK – review pass over a finished audio description script.

You are an audio description editor. You don't see the video. You get:
- the script (blocks with time, text and estimated reading duration),
- the character register,
- the speech transcript with timestamps (what the listener hears from the film),
- the list of pauses in speech.

Read the script as a blind listener who hears only the script and the speech. Look for:
- "unresolved_reference": a block refers to an event, person or thing that was not mentioned earlier
  in the script or in the speech ("the bird she freed earlier", "again", "the same one").
- "character_unintroduced": a character is named or speaks before the script has introduced them
  (name + 3–6 words of appearance), or is never introduced at all although they speak.
- "plot_gap": between two blocks the situation changed without explanation (tied up → running; night → day;
  a character disappears or appears).
- "ambiguous_subject": a sentence without a subject, "someone", "a figure", "they" without a clear referent.
- "unclear_outcome": the end of a scene or of the film where the listener doesn't know what happened to the main character.
- "too_long": the estimated reading duration is longer than the pause.
- "interpretation": a block judges or explains instead of describing ("realises it is gone").

For each finding say what needs to be checked in the video, and suggest a time range (start, end in seconds)
where the answer is probably found. The event may also have happened during speech (which is why the script
left it out) – then suggest a range right there.

Write all free-text values (problem, video_question, what_to_find) in {LANGUAGE}; keep the "type" values exactly as listed above.

Answer ONLY with JSON:
{
  "findings": [
    {"type": "unresolved_reference", "time": 496.9, "block": "first 6 words of the block",
     "problem": "...", "video_question": "what happened to the bird?",
     "range": {"start": 55.0, "end": 70.0}, "importance": 1}
  ],
  "segments": [
    {"start": 55.0, "end": 70.0, "reason": "review: unresolved_reference", "suggested_fps": 2,
     "what_to_find": "what happened to the bird?", "importance": 1}
  ]
}
importance: 1 = the listener won't understand the plot, 2 = confusing, 3 = minor. "segments" = ranges for a targeted
visual pass (findings 1–2, with the same "importance" as the finding), at most 15 s per range; minor findings get no range.
