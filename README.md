# describe_video – audio description of videos for blind people

Prototype: a blind user tells an agent "please describe this video for me". The agent understands the video from frames
arranged in grids and turns it into an audio description: a synthetic voice describes what can't be heard and fits it
into the gaps in speech.

## Pipeline

1. **Input:** a YouTube URL (yt-dlp, for the prototype only – this violates YouTube's Terms of Service) or your own
   file. Video that isn't H.264 (AV1, HEVC) is converted, otherwise scene detection wouldn't see it.
2. **Speech:** YouTube subtitles (json3, else vtt), otherwise Whisper. For a music video only the singing times are stored.
   **Precise pauses:** Silero VAD (part of faster-whisper) refines speech boundaries – automatic subtitles have
   ±1 s boundaries and see no pauses at all in continuous dialogue. Speech = VAD segments that overlap a subtitle.
   `--no-vad` turns it off.
3. **Scenes:** PySceneDetect – ContentDetector + ThresholdDetector (fades). Option `--detector`.
4. **Overview:** a frame from the middle of each scene, every 4 s in long scenes (`--long-step`).
   The time is burned into the frame (yellow, MM:SS.s) and is also in the grid's text header (`overview.md`).
   A grid has 4×4 frames, ordered left to right, top to bottom.
5. **Pass 1 (model):** running state (characters, place, plot) and JSON segments to slow down → `prompts/pass1_overview.md`.
6. **Detail + pass 2:** segments at a finer fps (0.5–8 fps, at most 32 frames per segment) → `prompts/pass2_detail.md`.
7. **Character register:** name, 3–6 words of appearance, first appearance, where the name comes from, confidence → `prompts/characters.md`.
   A name with confidence below 0.7 is not used as a fact in the script.
8. **Script:** blocks `{start, end, text, confidence, tts?, extended?, over_speech?}` → `prompts/script.md`
   (introduce every character at first appearance, every reference to the past must have its own block, every sentence has a subject).
   `check` = estimated duration vs. room until the next speech; blocks with confidence below 0.6 are written to `uncertain_segments.json`.
9. **Review pass (text only):** a model reads the script as a blind listener and returns findings
   (unresolved reference, unintroduced character, plot gap, sentence without a subject, unclear ending, too long,
   interpretation) + segments for a targeted look → `prompts/review.md`, command `review`.
10. **Targeted visual pass:** `detail --prefix probe` on segments from the review and on uncertain blocks → script fix.
11. **Audio:** piper TTS (default voice per language, e.g. `sk_SK-lili-medium`). A block that doesn't fit is spoken at most 15 % faster.
    The original sound is **smoothly ducked by 9 dB** under the description (0.25 s attack, 0.6 s release). Outputs:
    - `narration_only.mp3` – just the description, back to back,
    - `description_track.mp3` – the description alone at the correct times (a separate track),
    - `audio_description_mix.mp3` – the description mixed into the original sound,
    - `video_with_ad.mp4` – two audio tracks: 1. audio description (default), 2. original audio,
    - `descriptions.vtt` – WebVTT `Kind: descriptions` for `<track kind="descriptions">` (screen readers),
    - **extended audio description** (a block with `"extended": true` that doesn't fit): the video freezes on the last
      frame until the description finishes → `video_with_ad_extended.mp4` + `descriptions_extended.vtt`,
    - `audio_info.json` – for each block: duration, room, speed-up, overflow.

## Languages

- The output language of the audio description is set with `--language` (ISO 639-1 code, default `sk`) on `prepare`
  and `auto`; it is stored in `manifest.json` and later commands read it from there. It also sets which subtitle languages are preferred.
- The prompts are in English and tell the model to write all free text in the chosen language.
- The TTS voice is chosen per language (`--voice` overrides it), e.g. `sk_SK-lili-medium`, `cs_CZ-jirka-medium`,
  `en_US-lessac-medium`, `de_DE-thorsten-medium`.
- **So far tested only with Slovak.** All examples and results below are Slovak audio descriptions.

## Layers (since 24 Oct 2026): perception → story → script

"What can be seen" is separated from "what to say". Everything above the log is plain text, so style changes
or fixes are done without watching the video again.

1. **Character register** (`characters.json`) – who recurs, appearance, confidence of the name.
2. **Perception** (`prompts/perception.md`, command `perceive`) – a structured **log** `log.jsonl`, one record
   per scene / change within a scene: place, **state of each character from the register** ("tied up" / "absent" / "not visible"),
   action, objects, on-screen text, shot, confidence, "unclear" questions. The model must not fill in gaps – better "unclear".
3. **Story** (`prompts/story.md`, command `story`) – from the text of the log and the speech only: the plot by units, how the
   characters' states develop, **questions** (a character changes state without explanation, an object without origin, an unclear ending) + segments to look at.
4. **Targeted look** – `detail --prefix probe` on the questions; the answers are written **back into the log**
   (`log-add --source probe`), not directly into the script.
5. **Script** (`script`) – from text only: register + story + log + pauses → `script.json`.
6. **Review** (`review`) → findings → another targeted look → fix. `frame work <time>` gives a full-resolution frame
   for reading text.

In `auto` mode / with `--api` a model does this; in agent mode an agent (Artemis, the author's agent setup) fills in the log, and the other
layers can be handed to an independent model without context (that is how the Charge test ran).

## Usage

```
python3 describe_video.py prepare "https://www.youtube.com/watch?v=…" -o work/ [--language sk]   # or a file path
# → the agent looks at work/grids/overview_*.jpg and work/overview.md, writes work/pass1.json
python3 describe_video.py detail work/ work/pass1.json
# → the agent looks at work/grids/detail*.jpg, writes work/characters.json and work/script.json
python3 describe_video.py check work/ work/script.json
python3 describe_video.py review work/ work/script.json            # → work/review_input.md for the reviewer
# → the reviewer (ideally another model / a colleague without context) writes work/review.json
python3 describe_video.py detail work/ work/review.json --prefix probe
# → the agent fixes the script based on the findings and the targeted grids
python3 describe_video.py audio work/ work/script.json [--tempo 1.0]

# fully automatic – with a Claude Pro/Max subscription via Claude Code (on a Mac where `claude` is logged in):
env VIDEO_BACKEND=claude-cli python3 describe_video.py auto "<URL|file>" -o work/ --max-probes 5
# (--reuse-prep = don't download and build grids again; --no-audio = no TTS)

# fully automatic via the Anthropic API (ANTHROPIC_API_KEY; NOT TESTED yet):
python3 describe_video.py auto "<URL|file>" -o work/ [--model-perception claude-sonnet-5-5] [--model-main claude-sonnet-5-5]
# auto = characters → perception (log) → story → targeted look at questions → story again → script → review
#        → targeted look at findings → script again → control review → audio;
# tokens of every call are written to work/usage.json
```

**Model in `auto` mode** (`VIDEO_BACKEND`):
- `claude-cli` – calls `claude -p` (Claude Code in non-interactive mode) logged in with your Pro/Max subscription,
  exactly as if you ran it in a terminal. It gets the grid images as paths and reads them with the Read tool
  (other tools are disabled). Models as aliases `haiku` / `sonnet`. Subscription limits apply –
  one run on Charge is about 40–50 calls, hence `--max-probes 5`; when the limit is hit the run stops with a message.
  `usage.json` shows the tokens and an estimate of what it would cost via the API.
- `api` – Anthropic API with `ANTHROPIC_API_KEY` (pay per use).
- Unset: `api` if a key is present, otherwise `claude-cli` if `claude` is on the PATH.
- After an interruption (subscription limit) the script exits with a prompt; the same command with `--resume` skips finished steps
  (state in `auto_state.json`). Without `--resume` it starts over and moves the old results to `previous/`.
- It ran with a real subscription on 24 Oct on Charge (`work_charge/`) – results below.
- Every raw model response is in `responses/<step>_<n>.txt`. Invalid JSON → one retry; a grid scene
  that still has no record after the retry goes into the log as "unclear" with confidence 0 (the story turns it into a question).
- `--max-probes` picks segments by importance (1 → 2 → 3, then time) and merges overlapping segments.
- Perception uses Sonnet by default (`--model-perception`), because all later layers live off the log.

Installation: `ffmpeg` (Mac: `brew install ffmpeg`) and
`pip install yt-dlp scenedetect opencv-python-headless faster-whisper piper-tts pillow numpy`.
Downloading from YouTube needs a JS runtime (node/deno). The voice and the Whisper model are downloaded from HuggingFace on first run.

## Examples

Both examples were made by an agent (Artemis) in agent mode: it looked at the grids itself and wrote the JSON and the script. The API was not used.

### `example_coffee_run/` – Coffee Run (Blender Studio, CC BY 4.0, 3:05, no speech)

- Overview: 27 scenes, 54 frames, 4 grids (with a frame every second there would be 185). Pass 1 picked 14 segments, detail has 18 grids at 1–3 fps.
- Script: 32 blocks (`script.md`). The 2nd and 3rd rounds of the film are described too. In the 3rd round the scenes change: the double is alone in a hospital and in a cemetery.
- Without detail the description would make no sense. In the overview it looks like "a woman runs through the city"; only at 2 fps does it become clear she runs through rooms in which her double lives through a relationship with a red-haired man.
- AdaptiveDetector misses cuts in fast animation (5 scenes instead of ~25). That is why ContentDetector is the default.

### `example_sprite_fright/` – Sprite Fright (Blender Studio, CC BY 4.0, 10:30, English)

- Speech: YouTube's automatic English subtitles, 129 segments. `[Music]` tags don't count as speech, which left 24 gaps, 286 s in total.
- Overview: 133 scenes, 209 frames, 14 grids (grid 7 was regenerated after a time-format fix). Pass 1 picked 14 segments (mostly night and gaps without speech), detail has 28 grids at 0.7–3 fps.
- Script: 59 blocks in the gaps in speech (`script.md`). It was verified that, apart from one block, none overlaps speech or overruns its gap, measured by the actual length of the spoken audio. The exception is the film's title at 9:13.5: it intentionally runs 1.4 s into the start of a song (flag `over_speech`).
  Outputs: `narration_only.mp3` (3:58), `audio_description_mix.mp3` and `video_with_ad.mp4` (10:30).
- Character names (Ellie, Rex, Victoria, Jay, Phil) come from the closing credits. The Rex/Jay split is a guess.
- The speech transcript (`transcript.json`, `overview.md`, `detail.md`) is intentionally not included in the example. It contains the film's dialogue and song lyrics. It is recreated by the `prepare` command.
- What the test showed:
  - The character descriptions at the start (0:27–1:22) have nowhere to go; there is continuous speech. This needs solving: introducing the characters before the film, or short descriptions even over less important speech.
  - The Slovak voice mispronounces foreign names, which is why the script has a `pronunciation` field (Ellie → Elli).

### Test of the improvements on Sprite Fright (24 Oct 2026) – `example_sprite_fright/`

Procedure: old script → independent reviewer (a colleague without context, text only) → targeted visual pass
(12 segments, 21 grids `grids/probe*.jpg`) → new script with a character register → second review → fixes.

| | old script | new script |
|---|---|---|
| pauses in speech | 24 (from subtitles), 286 s | 37 (VAD + subtitles), 326 s |
| blocks | 59 | 67, 8 of them freezing the picture |
| review findings, importance 1 (listener won't understand the plot) | 8 | round 2: 3 (1 false, 2 rewritten); round 3: 2 (salt, Ellie's ending) |
| bird without being freed | yes | fixed (1:01 and 1:05, picture freeze) |
| unintroduced characters | 4 of 5 | all introduced (0:29 with picture freeze; Phil 1:19; old sprite 3:20) |
| jump 4:09–5:06 | unclear | Ellie wakes up alone, the sleeping bags are empty; she unties the blond guy; four of them run, not Phil |
| ending | "a small figure" | Ellie walks away across the clearing, the old sprite watches her and throws up his hands, Ellie disappears into the forest; at dawn a march with trophies |
| "stomping" without a subject | yes | fixed |

- In round 1 the reviewer found all 4 known errors without hints, plus more: what the sprites carry
  around 3:38 (a sock with red stains), how Victoria got into the spider web, where Jay is before the slime.
- Round 2 of the review found 3 findings of importance 1: the introduction of the group runs over speech (false – the block freezes the picture,
  but the reviewer's input didn't show that at the time; **fixed in the tool**, "flag" column), spilled salt
  without explanation, and an unclear ending – both rewritten. A third review round after these fixes did not run.
- Round 3 of the review (after the fixes) found 2 essential things: the script doesn't show where the salt came from and that it hurts the sprites
  (the finale hinges on salty snacks), and Ellie's ending was still unclear. The ending is rewritten based on grid `probe10`
  (Ellie leaves, the sprite watches her). The salt remains open – I haven't verified from the picture where it was spilled from;
  another targeted pass should look at 2:00–2:17 (Phil salts sausages, a fly agaric by his flip-flops).
  Minor items from round 3 (a sentence without a subject, origin of the flashlight, cut to Victoria) are fixed.
- The standard version (`video_with_ad.mp4`, `audio_description_mix.mp3`, `descriptions.vtt`) doesn't freeze the picture,
  so the 8 freezing blocks speak over speech there (longest: the group introduction at 0:29, about 9 s).
  Only the extended version is free of overlap. The other blocks don't overlap speech (measured by actual audio length and VAD).
- The blond guy has no name in the script: "Rex" is only a guess from the credits (confidence 0.6) and is never spoken.
  Jay is a guess based on the DEEJAY label on his cassette. Phil's fate after being set on fire isn't shown in the picture, so the script doesn't state it.
- Ducking: measured −9.0 dB under each block, 0 dB outside the description. The original attempt with `sidechaincompress`
  barely ducked loud music (measured ~0 dB) – replaced with a precise envelope.
- The speech transcript and the reviewer's input (they contain the film's dialogue) are intentionally not included in the example.

### Test of the layers on a new video: `example_charge/` – Charge (Blender Studio, CC BY 4.0, 4:23, almost no speech)

A/B comparison on the same film and the same frames (7 overview grids + 19 targeted grids):
- **A, old approach:** a fresh model wrote the script directly from the frames.
- **B, new approach:** perception log (Artemis) → story from text only (another model, never saw the video) → targeted look at
  10 story questions (19 grids) → script from text only (yet another model, never saw the video). The story was not regenerated
  after the targeted look – the answers are in the log and the script draws on them from there.
- Both scripts were assessed by the review pass – separately, same prompt, with no information which is which.

| review, round 1 | A (old) | B (new) |
|---|---|---|
| blocks | 44 | 29 |
| importance 1 – listener won't understand the plot | **2** | **0** |
| importance 2 – confusing | 8 | 6 |
| importance 3 – minor | 6 | 10 |
| **total** | **16** | **16** |

- Essential errors in A: a battery the old man "pulls out" after the fight although he already had it; an unclear final shot.
  A has more blocks but lacks connections (the mechanical arm is never attached, the robot in the armchair – the same one?).
- **The total number of findings is the same (16 : 16)** – the new approach doesn't have fewer findings, but less serious ones: 0 essential (A 2) and 6 confusing (A 8), though more minor ones (wording, lengths).
- The first review calls were run by mistake without the prompt file (same conditions for A and B): A 0/8/8 = 16, B 0/7/8 = 15 – same direction, smaller difference.
- After review round 1 and one targeted look (the pink paint = a shot-through PINK spray can), B has
  **0 / 5 / 6** in round 2.
- The text-only story found questions by itself that the script would otherwise have left out: where the pink paint comes from, how he got
  through the gate, whether the small robot comes to life. Answers from the targeted grids go into the log.
- **Limitations of the test:** one video, one assessment per script (the 2 vs 0 difference may be partly chance);
  the log was filled in by Artemis, not a fresh model; A also got the targeted grids that came from the story layer's questions
  (which, if anything, favours A). Verification with a fresh model in all layers = `auto` mode (via a Pro subscription on a Mac or an API key).
- Audio: 29 blocks, none sped up; the block at 0:36.5 reaches 0.02 s into a fragment of speech at 0:38.2 (negligible), the others don't overlap speech (by actual audio length).
- Files: `log.jsonl`, `characters.json`, `story.json`, `script.json` (B), `script_old.json` (A),
  `review_comparison.json`, `script.md` (both scripts), `grids/` (overview, probe, recheck), audio and video.

### First `auto` run with a real model: `work_charge/` (24 Oct 2026)

`claude -p` with a subscription, perception Haiku, everything else Sonnet, `--max-probes 5`: 31 calls, ~27 min,
API equivalent ~2.59 USD. The result is **worse than the manual run B**: review round 1 **5 / 8 / 2** (B: 0 / 6 / 10),
round 2 **5 / 7 / 2** – the fixes didn't help. Causes (fixed in the tool, a new run hasn't happened yet):
- **Silent loss of output:** overview grids 3 and 4 (79–131 s, the climax) produced 0 and 1 records in the log, although Haiku returned
  ~12k tokens – the JSON didn't parse and the step was silently skipped. Same for the targeted look at the ending (164–179 s).
  So the script has nothing between 98 and 129 s and the ending is unclear. Raw responses weren't saved at the time.
- **`--max-probes` took the first 5 segments by time** (18–72 s); the essential findings at 82–181 s were never looked at.
- **Credits:** 6 blocks (~45 s) reading names from the credits while the plot had no description; blocks with guesses ("maybe a robot").
  → `prompts/script.md` (credits = one block, no guesses), `check` reports guesses and pauses ≥ 12 s without description.

### `example_korn/` – Korn, Falling Away From Me (official video, 4:59, protected work – internal test only)

- As agreed, this example is no longer being fixed. Note: YouTube subtitles (vtt) actually existed, the tool didn't read them then, and Whisper missed the final chorus (3:54–4:31), so blocks 25–30 run over singing. The tool reads vtt now.
- Speech at the time of the test: Whisper with VAD found nothing, because the singing runs over loud guitars. A second attempt without VAD found 39 singing segments. **Only the times** are stored (text "(singing)"), not the lyrics. Pauses in the singing: 11, 146 s in total (intro 0:00–1:00, ending 4:04–4:59 and short breaks).
- Overview: 289 scenes (fast music-video editing), 293 frames, 19 grids. Pass 1 picked 12 segments, detail has 18 grids.
- Script: 35 blocks (`script.md`), only what can be seen: the story of a girl and her father, a box with lightning, the band in her room, a crowd in front of the house, the ending on the lawn. All fit into the pauses, also by actual audio length. `narration_only.mp3` 2:04.
- What the test showed:
  - In a music video most of the time is singing. The description is squeezed into the intro and the ending; the middle part (1:25–2:54) is covered by only a few short sentences.
  - With 289 cuts, a frame from the middle of each scene is denser than 1 frame per second. For fast music videos a cap would be better (e.g. at most 1 frame per 1.5 s).
  - Uncertain: whether the father hurts the girl – the video only hints at it (he shouts, goes to the door). The script doesn't judge it and describes only what can be seen.

## Fixes made during testing

- YouTube subtitles: the pattern `sk.*` downloaded dozens of automatic translations and YouTube answered 429. Now they are downloaded separately and only exact languages (`sk,cs,en`, then `*-orig`). When they fail, the run doesn't crash and Whisper is used.
- Automatic subtitles overlap, so each line is trimmed to the start of the next one.
- AV1 video is converted to H.264. Video without sound has silence under the description. The fallback webm format is converted to mp4.
- Whisper decodes audio via ffmpeg, not via PyAV (version conflict). When it finds nothing with VAD (singing over music), it retries without VAD and stores only the singing times.
- Time "04:60.0": rounding happens before dividing into minutes.
- `auto`: pass 2 also gets `what_to_find`. On 429/5xx errors the API call is retried. Bad JSON skips the step and doesn't crash the run.

## Open

- Verify the layers with a fresh model in perception too (`auto` via a Pro subscription or an API key) and on more videos; the metric = review findings in round 1.
- Interactive mode: questions about the video ("what is on the board at 3:20?") answered from the log, with one full-resolution frame when unclear.
- Run `auto` on Charge again after the fixes (`--reuse-prep`) and compare with 5/8/2 and 0/6/10; then on more videos.
- Speaker diarization (pyannote) – solves "who is speaking" and Rex/Jay; the model on HuggingFace requires accepting terms and a token.
- WhisperX (word timings) and, for music, singing separation (Demucs) before VAD – Whisper missed the whole chorus in Korn.
- Detecting important sounds (screams, gunshots – don't talk over them), e.g. YAMNet.
- SSML/better TTS – piper is intelligible but robotic; foreign names sound bad even with the pronunciation fix.
- A player operable by keyboard and screen reader; a test with a real blind user.
- Frame cache by video hash, a test set of 5–10 videos of different types with a score (e.g. number of review findings).
- Comparison with Gemini (native video + audio) as a baseline.
- Fast music videos: cap the overview density (e.g. at most 1 frame per 1.5 s).

## License

Code and prompts: [MIT](LICENSE) – use, modify and share freely, just keep the copyright notice (credit the author, Martin Pohorelský).
The example folders contain descriptions of Blender Studio films (CC BY 4.0, © Blender Foundation); videos, audio,
frames and transcripts are not part of the repository (see `.gitignore`).
