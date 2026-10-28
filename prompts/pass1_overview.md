TASK – pass 1 (overview).

You get one grid of frames from a video. The frames are in time order, left to right, top to bottom.
The overview has one frame from the middle of each scene (cut) and, in long scenes, a frame every 3–5 s,
so a lot of action may be missing between neighbouring frames. You also get the speech in this range
and the RUNNING STATE from the previous grids.

Do three things:
1. Update the running state: characters (a fixed label, appearance, name if it was spoken), place,
   the story so far in a few sentences, open questions ("what is she holding?").
   Don't change a character label that is already in the state.
2. Briefly describe what happens in the grid (a note for yourself, not for the listener).
3. Pick segments that need a finer look. Reasons:
   "fast motion" | "on-screen text" | "unclear action" | "many characters" | "fast cuts" | "important detail".
   Don't pick segments that are clear (static scene, talking head).
   suggested_fps: 2 for slower action, 4 for fast action and fast cuts, 8 only for a very short
   (up to 2 s) and fast event. A segment is at most 8 s long; split longer action.

Write all free-text values in {LANGUAGE}; keep the "reason" values exactly as listed above (in English).

Answer ONLY with this JSON (no other text):
{
  "state": {"characters": [{"label": "...", "appearance": "...", "name": null}],
            "place": "...", "story_so_far": "...", "open_questions": ["..."]},
  "grid_notes": "...",
  "segments": [{"start": 12.0, "end": 18.5, "reason": "fast motion", "suggested_fps": 4,
                "what_to_find": "what exactly falls off the table"}]
}
