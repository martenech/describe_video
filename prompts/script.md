TASK – audio description script.

From the character register, the running state, the notes from the overview and the details, the speech transcript
and the list of pauses in speech, write an audio description script following the rules.

Intro and characters
- Start with a short intro (a block with start 0 or in the first pause): the video's title,
  author/channel, length, what kind of video it is (e.g. an animated film, the characters speak English).
- Introduce each character from the register at their first appearance: name + 3–6 words of appearance
  ("Phil, a stocky redhead with a backpack"). From then on call them by name only.
  If there is no pause for the introduction at the first appearance, introduce them in the first pause after it; several
  characters can be introduced at once in one sentence.
- Don't use a name with confidence below 0.7 in the register as a fact – describe the character by appearance.

Plot
- Every event the script refers to later ("the bird she freed") must have its own block.
- When a key event happens during speech and there is no pause:
  a) say it briefly in the next pause ("Meanwhile, Ellie has freed a bird from plastic rings."), or
  b) if it is a turning point, put a block at the start of the event with "extended": true – the video freezes at that time
     (extended audio description), or
  c) exceptionally, over a less important line with "over_speech": true (a short sentence only).
- Time jumps from the story ("time_jumps"): announce the jump at the start of the block in a few words
  ("Later:", "Earlier:", "A glimpse ahead:" – in {LANGUAGE}) and say when the film returns
  ("Back at the house, before all this."). Without it the listener takes the scenes as one continuous sequence.
- Same entities from the story ("same_entity") with confidence ≥ 0.7: use one label for them throughout, and at the
  first moment that shows the link, say it explicitly ("The girl at the window is the girl in the red coat from the
  street."). A character's body part or tool is introduced together with the character ("the burglar's gloved hand"),
  never as a separate "hand" or "hands".
- Every sentence has a subject. No "stomping", "someone", "a figure".
- State the end of the film and the fate of the main characters clearly, if it is shown.

Timing
- A block starts in a pause and ends before the next speech ("end" = end of the pause or start of the next block).
  At a rate of about 15 characters per second the text must fit (the audio can be sped up by at most 15 %).
- Time a block just before or at the start of the event it describes – not later.
- When an important thing doesn't fit, shorten the less important things, not it.
- Closing credits = at most ONE short block: title and director (possibly the license). Don't read lists of names
  or thanks – the time belongs to the plot. If the credits run over picture with action, describe the action.
  Mark the credits block with "credits": true (pauses after it are not reported as undescribed).

Confidence
- Every block has "confidence" 0–1: how certain it is that what it says is in the picture. Below 0.6 add "uncertain_why"
  – such blocks automatically go to a targeted visual pass.
- Text for the listener must not contain guesses ("maybe", "perhaps", "probably", "apparently", "as if" – or their
  equivalents in {LANGUAGE}). When you're not sure, say only what is certain ("A white object in the corner."); uncertainty
  belongs in "confidence" and "uncertain_why".

Pronunciation
- Foreign names and titles that the {LANGUAGE} TTS voice would mispronounce also go into the block as "tts" –
  the same text with the pronunciation spelled phonetically in {LANGUAGE} (for a Slovak voice: "Coffee Run" → "Kofí ran").
  "text" keeps the correct spelling.
  Names that recur are better put once into "pronunciation" (for a Slovak voice e.g. {"Ellie": "Elli"}) – it is applied in all blocks.
  Put into "pronunciation" only words that actually occur in the blocks.

Write "title", "text" and "uncertain_why" in {LANGUAGE}.

Answer ONLY with this JSON:
{
  "title": "...",
  "pronunciation": {"Foreign name": "pronunciation"},
  "blocks": [{"start": 0.0, "end": 4.0, "text": "...", "confidence": 0.9,
              "tts": "(only if needed)", "extended": false, "over_speech": false, "credits": false, "uncertain_why": "(only below 0.6)"}]
}
