#!/usr/bin/env python3
"""describe_video.py – audio description of videos for blind viewers (pipeline skeleton).

Chain:    video (YouTube URL or your own file)
          → speech transcript (YouTube subtitles, else Whisper) with timestamps, pauses refined with Silero VAD
          → cut detection (PySceneDetect, fallback ffmpeg scdet)
          → OVERVIEW: a frame from the middle of each scene + every N s in long scenes,
            burned-in time, grids left to right / top to bottom, text header
          → character register → PERCEPTION (log per scene) → STORY (text only, questions)
          → targeted look at the questions (DETAIL grids, answers back into the log)
          → SCRIPT (text only) → REVIEW → targeted look at the findings → script again
          → AUDIO: TTS (piper) mixed with the original sound (ducked under the description)

Commands (each step is standalone, results live in the work folder):
  prepare  <URL|file> -o WORK [--language sk] [--long-step 4] [--detector content|histogram|adaptive]
  detail   WORK segments.json [--prefix probe]   (grids of segments at a finer fps)
  perceive WORK [--grids overview|probe] [--api] (perception log; without --api an input for an agent)
  log-add  WORK records.json [--source probe]    (agent's records into log.jsonl)
  story    WORK [--api]                          (plot and questions from text only)
  script   WORK [--api]                          (script from text only)
  check    WORK script.json                      (block lengths, guesses, uncovered pauses)
  review   WORK script.json [--api]              (findings of a reviewer who does not see the video)
  frame    WORK <time> …                         (frame in full resolution)
  audio    WORK script.json [--voice sk_SK-lili-medium]
  auto     <URL|file> -o WORK                    (everything by itself: Anthropic API with ANTHROPIC_API_KEY,
                                                  or Claude Code with a Pro/Max subscription – VIDEO_BACKEND)

Agent mode: an agent runs `prepare`, looks at the grids itself
(WORK/grids/overview_*.jpg + WORK/perception_input_overview.md), fills in the log (`log-add`), does the next layers
itself or hands them to an independent model (inputs *_input.md), and finally runs `audio`.
Rules and answer formats are in prompts/*.md (the same for an agent and for `auto`).
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
PROMPTS = HERE / "prompts"

# ---------------------------------------------------------------- languages

LANGUAGES = {"sk": "Slovak", "cs": "Czech", "en": "English", "de": "German", "pl": "Polish", "hu": "Hungarian"}
VOICES = {"sk": "sk_SK-lili-medium", "cs": "cs_CZ-jirka-medium", "en": "en_US-lessac-medium", "de": "de_DE-thorsten-medium"}
ISO639_2 = {"sk": "slk", "cs": "ces", "en": "eng", "de": "deu", "pl": "pol", "hu": "hun"}
# hedging words that must not reach the listener (check skipped for other languages)
GUESS_WORDS = {"sk": r"možno|asi|zrejme|pravdepodobne|akoby",
               "cs": r"možná|asi|zřejmě|pravděpodobně|jakoby",
               "en": r"maybe|perhaps|probably|possibly|apparently|seemingly"}


def load_prompt(name: str, language: str) -> str:
    """Prompt file with {LANGUAGE} replaced by the full language name (str.replace – prompts contain JSON braces)."""
    return (PROMPTS / name).read_text().replace("{LANGUAGE}", LANGUAGES.get(language, language))


def work_language(work: Path) -> str:
    """Output language of the description, from the manifest (default "sk")."""
    return json.loads((work / "manifest.json").read_text()).get("language") or "sk"

# ---------------------------------------------------------------- helpers


def fmt(t: float, tenths: bool = True) -> str:
    """Time as MM:SS.s (or H:MM:SS.s)."""
    t = round(max(0.0, t), 1 if tenths else 0)  # otherwise 59.97 s → "00:60.0"
    h, rest = divmod(t, 3600)
    m, s = divmod(rest, 60)
    sec = f"{s:04.1f}" if tenths else f"{int(s):02d}"
    return (f"{int(h)}:{int(m):02d}:{sec}" if h >= 1 else f"{int(m):02d}:{sec}")


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, text=True, capture_output=True, **kw)


def duration(video: Path) -> float:
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(video)])
    return float(r.stdout.strip())


def font(size: int) -> ImageFont.FreeTypeFont:
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
              "/Library/Fonts/Arial Bold.ttf"):
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


# ---------------------------------------------------------------- 1. input


def get_video(source: str, work: Path, language: str) -> tuple[Path, list[dict]]:
    """Downloads the video from a URL (yt-dlp) or copies your own file.
    Returns the path to the video and the speech transcript [{start, end, text}]."""
    work.mkdir(parents=True, exist_ok=True)
    video = work / "video.mp4"
    transcript: list[dict] = []
    if re.match(r"https?://", source):
        # Note: downloading from YouTube is against its terms of service – prototype only.
        ytdlp = [sys.executable, "-m", "yt_dlp", "--js-runtimes", "node", "-o", str(work / "video.%(ext)s")]
        run(ytdlp + ["-f", "bv*[height<=720][vcodec^=avc1]+ba[ext=m4a]/bv*[height<=720]+ba/b[height<=720]",
                     "--merge-output-format", "mp4", "--remux-video", "mp4", "--write-info-json", source])
        # Subtitles separately and only exact languages: the pattern "sk.*" also catches dozens of automatic
        # translations (sk-en, sk-de…) and YouTube answers 429. A subtitle failure does not stop the run (→ Whisper).
        for langs in (f"{language},cs,en", f"{language}-orig,cs-orig,en-orig"):
            r = subprocess.run(ytdlp + ["--skip-download", "--write-subs", "--write-auto-subs", "--sub-langs", langs,
                                        "--sub-format", "json3", source], text=True, capture_output=True)
            if r.returncode == 0 and (list(work.glob("video.*.json3")) or list(work.glob("video.*.vtt"))):
                break
            print(f"! subtitles ({langs}) could not be downloaded: {r.stderr.strip().splitlines()[-1:] }", file=sys.stderr)
        transcript = load_subtitles(work, language)
    else:
        if Path(source).resolve() != video.resolve():
            shutil.copy(source, video)
    normalize(video)
    if not transcript:
        transcript = whisper(video, language)
    (work / "transcript.json").write_text(json.dumps(transcript, ensure_ascii=False, indent=1))
    return video, transcript


def normalize(video: Path) -> None:
    """Converts a video that is not H.264 (AV1 from YouTube, HEVC from an iPhone…) to H.264 at up to 720p.
    OpenCV in PySceneDetect often fails to decode AV1 and silently returns a single scene."""
    codec = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                 "stream=codec_name", "-of", "default=nw=1:nk=1", str(video)]).stdout.strip()
    if codec == "h264":
        return
    print(f"Converting video {codec} → H.264 …", file=sys.stderr)
    tmp = video.with_suffix(".h264.mp4")
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(video),
         "-vf", "scale=-2:'min(720,ih)'", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
         "-c:a", "aac", "-b:a", "160k", str(tmp)])
    tmp.replace(video)


def has_audio(video: Path) -> bool:
    return bool(run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                     "-of", "csv=p=0", str(video)]).stdout.strip())


def _vtt(p: Path) -> list[dict]:
    """WebVTT → [{start, end, text}] (yt-dlp sometimes saves it instead of json3)."""
    def sec(x: str) -> float:
        parts = x.replace(",", ".").split(":")
        return sum(float(c) * 60 ** i for i, c in enumerate(reversed(parts)))
    out = []
    for block in re.split(r"\n\s*\n", p.read_text(errors="replace")):
        m = re.search(r"([\d:.,]+)\s+-->\s+([\d:.,]+)", block)
        if not m:
            continue
        text = " ".join(l for l in block[m.end():].splitlines() if l.strip())
        text = re.sub(r"<[^>]+>", "", text).strip()
        if text:
            out.append({"start": sec(m.group(1)), "end": sec(m.group(2)), "text": re.sub(r"\s+", " ", text)})
    return out


def is_music(work: Path) -> bool:
    for p in work.glob("*.info.json"):
        if "Music" in (json.loads(p.read_text()).get("categories") or []):
            return True
    return False


def load_subtitles(work: Path, language: str) -> list[dict]:
    """YouTube subtitles (json3, else vtt) → [{start, end, text}]. Manual ones take precedence over automatic ones.
    For a music video (category Music) the text is replaced with "(singing)" – audio description needs only
    the timing, and song lyrics are copyrighted and must not be stored."""
    files = sorted(work.glob("video.*.json3"))
    if not files:
        vtt = sorted(work.glob("video.*.vtt"))
        if not vtt:
            return []
        out = _vtt(sorted(vtt, key=lambda p: (not p.name.split(".")[1].startswith(language), p.name))[0])
        return _finish_subtitles(out, work)
    def order(p: Path):
        code = p.name.split(".")[1]
        return (not code.startswith(language), "orig" in code, code)
    data = json.loads(sorted(files, key=order)[0].read_text())
    out = []
    for e in data.get("events", []):
        text = "".join(s.get("utf8", "") for s in e.get("segs", [])).strip()
        if not text:
            continue
        a = e.get("tStartMs", 0) / 1000
        text = re.sub(r"\s+", " ", text)
        record = {"start": a, "end": a + e.get("dDurationMs", 0) / 1000, "text": text}
        if re.fullmatch(r"\[[^\]]*\]", text):  # [Music], [Applause] – sound, not speech
            record["sound"] = True
        out.append(record)
    return _finish_subtitles(out, work)


def _finish_subtitles(out: list[dict], work: Path) -> list[dict]:
    for z in out:
        if re.fullmatch(r"\[[^\]]*\]|♪+", z["text"].strip()):  # [Music], [Applause] – sound, not speech
            z["sound"] = True
    # automatic subtitles overlap (a line stays until the next one comes) – trim to the start of the next one
    for x, y in zip(out, out[1:]):
        x["end"] = min(x["end"], max(y["start"], x["start"] + 0.5))
    if is_music(work):
        for z in out:
            if not z.get("sound"):
                z["text"] = "(singing)"
        for p in work.glob("video.*.vtt"):
            p.unlink()  # do not keep song lyrics
        for p in work.glob("video.*.json3"):
            p.unlink()
    return out


def whisper(video: Path, language: str) -> list[dict]:
    """Speech transcript via faster-whisper (if installed). VAD filter so that it
    does not invent text in music."""
    if not has_audio(video):
        return []
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("! faster-whisper is not installed – continuing without a speech transcript", file=sys.stderr)
        return []
    model = WhisperModel(os.environ.get("WHISPER_MODEL", "small"), compute_type="int8")
    # audio is decoded by ffmpeg (16 kHz mono float32), not PyAV – avoids a version conflict of the av library
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    segs, info = model.transcribe(np.frombuffer(raw, np.float32), language=None, vad_filter=True)
    out = [{"start": s.start, "end": s.end, "text": s.text.strip()}
           for s in segs if s.no_speech_prob < 0.6 and s.text.strip()]
    print(f"Whisper: language {info.language}, {len(out)} speech segments", file=sys.stderr)
    if not out:
        # Singing over loud music (music video): VAD does not let it through. Second attempt without VAD;
        # we take only the TIMING of the singing (lyrics are not stored – not needed and copyrighted).
        segs, _ = model.transcribe(np.frombuffer(raw, np.float32), language=info.language, vad_filter=False,
                                   condition_on_previous_text=False)
        out = [{"start": s.start, "end": s.end, "text": "(singing)"}
               for s in segs if s.avg_logprob > -1.0 and s.no_speech_prob < 0.9 and s.text.strip()]
        print(f"Whisper without VAD: {len(out)} singing segments (timing only)", file=sys.stderr)
    return out


def speech_gaps(transcript: list[dict], length: float, minimum: float = 1.5) -> list[dict]:
    """Segments without speech where a description can go."""
    out, cursor = [], 0.0
    for s in sorted((p for p in transcript if not p.get("sound")), key=lambda x: x["start"]):
        if s["start"] - cursor >= minimum:
            out.append({"start": round(cursor, 2), "end": round(s["start"], 2)})
        cursor = max(cursor, s["end"])
    if length - cursor >= minimum:
        out.append({"start": round(cursor, 2), "end": round(length, 2)})
    return out


def speech_with_vad(video: Path, transcript: list[dict]) -> list[dict]:
    """More precise speech boundaries: subtitles (especially automatic ones) have ±1 s boundaries and overlap,
    so a continuous "dialogue" in them has no pauses at all. Silero VAD (part of faster-whisper) finds
    the real breaths and pauses. Speech = VAD segments that overlap a subtitle (±1 s), so that music
    and shouts without a subtitle do not block pauses; a subtitle without any VAD segment stays speech
    (VAD may have missed quiet speech). Without faster-whisper returns speech from the subtitles."""
    speech_t = [p for p in transcript if not p.get("sound")]
    if not speech_t or not has_audio(video):
        return [{"start": p["start"], "end": p["end"]} for p in speech_t]
    try:
        import numpy as np
        from faster_whisper.vad import VadOptions, get_speech_timestamps
    except ImportError:
        return [{"start": p["start"], "end": p["end"]} for p in speech_t]
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    ts = get_speech_timestamps(np.frombuffer(raw, np.float32),
                               VadOptions(threshold=0.5, min_silence_duration_ms=300, speech_pad_ms=150,
                                          min_speech_duration_ms=200))
    vad = [(t["start"] / 16000, t["end"] / 16000) for t in ts]

    def overlaps(a0, a1, b0, b1, tol=0.0):
        return a0 < b1 + tol and b0 < a1 + tol

    speech = [{"start": a0, "end": a1} for a0, a1 in vad
              if any(overlaps(a0, a1, p["start"], p["end"], 1.0) for p in speech_t)]
    speech += [{"start": p["start"], "end": p["end"]} for p in speech_t
               if not any(overlaps(a0, a1, p["start"], p["end"]) for a0, a1 in vad)]
    speech.sort(key=lambda x: x["start"])
    print(f"VAD: {len(vad)} voice segments, {len(speech)} speech segments after merging with subtitles", file=sys.stderr)
    return speech


def load_speech(work: Path) -> list[dict]:
    """Speech segments for timing: from the manifest (VAD), else from the transcript."""
    m = json.loads((work / "manifest.json").read_text())
    if m.get("speech") is not None:
        return m["speech"]
    return [p for p in json.loads((work / "transcript.json").read_text()) if not p.get("sound")]


# ---------------------------------------------------------------- 2. scenes


def scenes(video: Path, threshold: float, length: float, detector: str = "content") -> list[dict]:
    """Scene detection → [{start, end, kind}].

    Preferably PySceneDetect (https://www.scenedetect.com):
      detector "content" (default, ContentDetector, threshold 27) – cuts by change of colour and brightness,
      "histogram" – similar, by the brightness histogram,
      "adaptive" – AdaptiveDetector, robust to camera motion, but misses cuts in fast animation
                   (Coffee Run: 5 scenes instead of ~25),
      + always ThresholdDetector for fades to black, which the content detectors miss.
    Without PySceneDetect falls back to the ffmpeg scdet filter (`threshold` 0–1)."""
    try:
        from scenedetect import AdaptiveDetector, ContentDetector, HistogramDetector, ThresholdDetector, detect
    except ImportError:
        print("! PySceneDetect missing (pip install scenedetect opencv-python-headless) – using ffmpeg scdet",
              file=sys.stderr)
        return _scenes_ffmpeg(video, threshold, length)
    det = {"content": lambda: ContentDetector(min_scene_len="0.4s"),
           "histogram": lambda: HistogramDetector(min_scene_len="0.4s"),
           "adaptive": lambda: AdaptiveDetector(min_scene_len="0.4s")}[detector]()
    cuts = {round(a.seconds, 2) for a, _ in detect(str(video), det)}
    fades = {round(a.seconds, 2) for a, _ in detect(str(video), ThresholdDetector(min_scene_len="0.4s"))}
    bounds = sorted(t for t in cuts | fades if 0.3 < t < length - 0.3)
    # merge boundaries closer than 0.3 s (both detectors find the same cut)
    merged: list[float] = []
    for t in bounds:
        if not merged or t - merged[-1] > 0.3:
            merged.append(t)
    points = [0.0] + merged + [length]
    out = []
    for a, b in zip(points, points[1:]):
        kind = "fade" if any(abs(a - s) <= 0.3 for s in fades) and a > 0 else "cut"
        out.append({"start": round(a, 2), "end": round(b, 2), "kind": kind if a > 0 else "start"})
    return out


def _scenes_ffmpeg(video: Path, threshold: float, length: float) -> list[dict]:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(video), "-an",
                        "-vf", f"scale=320:-2,scdet=threshold={threshold * 100:.0f}",
                        "-f", "null", "-"], text=True, capture_output=True)
    cuts = [float(m) for m in re.findall(r"lavfi\.scd\.time:\s*([\d.]+)", r.stderr)]
    bounds = [0.0] + [t for t in cuts if 0.3 < t < length - 0.3] + [length]
    out = []
    for a, b in zip(bounds, bounds[1:]):
        if b - a >= 0.2:
            out.append({"start": round(a, 2), "end": round(b, 2)})
    return out


def overview_times(sc: list[dict], long_step: float) -> list[tuple[float, int]]:
    """A frame from the middle of each scene; in a scene longer than 1.5×step also every `long_step` s."""
    out = []
    for i, s in enumerate(sc, 1):
        d = s["end"] - s["start"]
        if d <= long_step * 1.5:
            out.append((s["start"] + d / 2, i))
        else:
            n = math.ceil(d / long_step)
            step = d / n
            out += [(s["start"] + step * (k + 0.5), i) for k in range(n)]
    return out


# ---------------------------------------------------------------- 3. frames and grids

CELL_WIDTH = 480  # 4 columns × 480 = 1920 px; the model scales them down to ~1568 px


def frame(video: Path, t: float, target: Path, text: str) -> None:
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}",
         "-i", str(video), "-frames:v", "1", "-vf", f"scale={CELL_WIDTH}:-2", str(target)])
    im = Image.open(target).convert("RGB")
    d = ImageDraw.Draw(im)
    f = font(22)
    x0, y0, x1, y1 = d.textbbox((0, 0), text, font=f)
    d.rectangle((0, 0, x1 - x0 + 14, y1 - y0 + 12), fill=(0, 0, 0))
    d.text((7 - x0, 6 - y0), text, font=f, fill=(255, 230, 0))
    im.save(target, quality=90)


def grid(files: list[Path], target: Path, header: str, columns: int = 4) -> None:
    """Frames into a grid in order left to right, top to bottom, with a header."""
    ims = [Image.open(p) for p in files]
    w, h = ims[0].size
    rows = math.ceil(len(ims) / columns)
    hh = 44
    out = Image.new("RGB", (w * columns + (columns - 1) * 4, hh + h * rows + (rows - 1) * 4), (40, 40, 40))
    d = ImageDraw.Draw(out)
    d.text((10, 8), header, font=font(26), fill=(255, 255, 255))
    for i, im in enumerate(ims):
        r, s = divmod(i, columns)
        out.paste(im, (s * (w + 4), hh + r * (h + 4)))
    out.save(target, quality=85)


def make_grids(video: Path, times: list[tuple[float, int]], work: Path, prefix: str,
               per_grid: int = 16, note: str = "") -> list[dict]:
    """Makes frames with burned-in time and grids. Returns the list of grids with cell times."""
    (work / "frames").mkdir(parents=True, exist_ok=True)
    (work / "grids").mkdir(parents=True, exist_ok=True)
    files = []
    for i, (t, sc) in enumerate(times, 1):
        p = work / "frames" / f"{prefix}_{i:04d}.jpg"
        frame(video, t, p, f"{fmt(t)}  sc.{sc}")
        files.append((p, t, sc))
    grids = []
    for g in range(0, len(files), per_grid):
        chunk = files[g:g + per_grid]
        n = g // per_grid + 1
        hd = f"{prefix} {n}: {fmt(chunk[0][1])} – {fmt(chunk[-1][1])}  ({len(chunk)} frames{', ' + note if note else ''})"
        path = work / "grids" / f"{prefix}_{n:02d}.jpg"
        grid([k[0] for k in chunk], path, hd)
        grids.append({"grid": n, "file": str(path.relative_to(work)), "header": hd,
                      "cells": [{"index": j + 1, "time": round(k[1], 2), "time_text": fmt(k[1]), "scene": k[2]}
                                for j, k in enumerate(chunk)]})
    return grids


def transcript_in_range(transcript: list[dict], a: float, b: float) -> str:
    lines = [f"[{fmt(s['start'])}–{fmt(s['end'])}] {s['text']}" for s in transcript
             if s["end"] >= a and s["start"] <= b]
    return "\n".join(lines) if lines else "(no speech)"


def grid_text_header(g: dict, transcript: list[dict]) -> str:
    """Text header for the model: cell times + speech in the grid's range."""
    cells = ", ".join(f"{c['index']}={c['time_text']}(sc.{c['scene']})" for c in g["cells"])
    a, b = g["cells"][0]["time"], g["cells"][-1]["time"]
    return (f"### {g['header']}\nFile: {g['file']}\nCells (left to right, top to bottom): {cells}\n"
            f"Speech in this range:\n{transcript_in_range(transcript, a - 2, b + 2)}\n")


# ---------------------------------------------------------------- commands


def cmd_prepare(a) -> None:
    work = Path(a.o)
    video, transcript = get_video(a.source, work, a.language)
    length = duration(video)
    sc = scenes(video, a.threshold, length, a.detector)
    times = overview_times(sc, a.long_step)
    grids = make_grids(video, times, work, "overview")
    speech = speech_with_vad(video, transcript) if not a.no_vad else None
    gaps = speech_gaps(speech if speech is not None else transcript, length)
    info = {}
    for p in work.glob("*.info.json"):
        j = json.loads(p.read_text())
        info = {k: j.get(k) for k in ("title", "channel", "license", "duration", "webpage_url", "description", "categories")}
        if "Music" in (info.get("categories") or []):
            info["description"] = None  # often contains the lyrics
    manifest = {"video": str(video.name), "duration": length, "info": info, "scenes": sc,
                "speech_gaps": gaps, "speech": speech, "overview": grids, "language": a.language}
    (work / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    md = [f"# Video overview – {info.get('title') or video.name}", f"Duration {fmt(length)}, scenes {len(sc)}, "
          f"frames in overview {len(times)}, grids {len(grids)}, speech segments {len(transcript)}.",
          "Speech gaps (descriptions go here): " + (", ".join(f"{fmt(m['start'])}–{fmt(m['end'])}" for m in gaps) or "none"),
          "Scenes: " + ", ".join(f"{i}:{fmt(s['start'])}–{fmt(s['end'])}" for i, s in enumerate(sc, 1)), ""]
    md += [grid_text_header(g, transcript) for g in grids]
    (work / "overview.md").write_text("\n".join(md))
    print(f"Done: {len(sc)} scenes, {len(times)} frames, {len(grids)} grids → {work}/overview.md")


def scene_number(sc: list[dict], t: float) -> int:
    return next((i for i, s in enumerate(sc, 1) if s["start"] <= t < s["end"]), len(sc))


def cmd_detail(a) -> None:
    work = Path(a.work)
    m = json.loads((work / "manifest.json").read_text())
    video = work / m["video"]
    transcript = json.loads((work / "transcript.json").read_text())
    segments = json.loads(Path(a.segments).read_text())
    segments = segments.get("segments", segments) if isinstance(segments, dict) else segments
    output, md = [], ["# Segment detail", ""]
    for i, u in enumerate(segments, 1):
        fps = float(u.get("suggested_fps", 4))
        fps = min(max(fps, 0.5), 8)  # sensible bounds
        n = max(2, round((u["end"] - u["start"]) * fps))
        n = min(n, 32)  # cap: at most 2 grids per segment
        step = (u["end"] - u["start"]) / n
        times = [(t, scene_number(m["scenes"], t)) for t in (u["start"] + step * (k + 0.5) for k in range(n))]
        grids = make_grids(video, times, work, f"{a.prefix}{i:02d}", note=f"{1 / step:.1f} fps, {u.get('reason', '')}")
        output.append({**u, "actual_fps": round(1 / step, 2), "grids": grids})
        md.append(f"## Segment {i}: {fmt(u['start'])}–{fmt(u['end'])} – {u.get('reason', '')}")
        md += [grid_text_header(g, transcript) for g in grids]
    m["detail" if a.prefix == "detail" else a.prefix] = output
    (work / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, indent=1))
    (work / f"{a.prefix}.md").write_text("\n".join(md))
    print(f"Done: {len(output)} segments, {sum(len(v['grids']) for v in output)} grids → {work}/{a.prefix}.md")


# ---------------------------------------------------------------- audio

CHARS_PER_S = 15.0
MAX_SPEEDUP = 1.15  # a block that does not fit may be spoken at most 15 % faster


def tts_text(b: dict, sc: dict) -> str:
    """Text of a block for the voice: the "tts" field, else "text" with replacements from the script's
    "pronunciation" (e.g. {"Ellie": "Elli", "Sprite Fright": "Sprajt frajt"})."""
    if b.get("tts"):
        return b["tts"]
    t = b["text"]
    for foreign, spoken in sorted(sc.get("pronunciation", {}).items(), key=lambda x: -len(x[0])):
        t = re.sub(rf"\b{re.escape(foreign)}", spoken, t)
    return t  # reading speed estimate (Slovak, piper ~ 14–16 chars/s)


def free_room(b: dict, i: int, blocks: list[dict], speech: list[dict], length: float) -> float:
    """How many seconds a block has before the next speech or the next block starts."""
    limits = [length]
    if i + 1 < len(blocks):
        limits.append(blocks[i + 1]["start"])
    if not b.get("over_speech"):
        limits += [s["start"] for s in speech if s["start"] > b["start"] + 0.15]
        in_speech = [s for s in speech if s["start"] <= b["start"] + 0.15 < s["end"]]
        if in_speech:
            speech_end = max(s["end"] for s in in_speech)
            if speech_end - b["start"] > 0.7:
                return 0.0  # block starts inside speech
            # VAD boundaries are accurate to tenths – a block just before the end of a line is still accepted
            limits = [h for h in limits if h > speech_end]
            return max(0.0, min(limits) - speech_end)
    return max(0.0, min(limits) - b["start"])


UNCOVERED_S = 12.0  # a speech gap in which the script says nothing for this many seconds


def cmd_check(a) -> list[dict]:
    """Estimate (without TTS): does every block fit? Text without guesses? No long pause without description?
    Low-confidence blocks and uncovered pauses → uncertain_segments.json (input for `detail`)."""
    work = Path(a.work)
    m = json.loads((work / "manifest.json").read_text())
    sc = json.loads(Path(a.script).read_text())
    speech = load_speech(work)
    lang = m.get("language") or "sk"
    guesses = re.compile(rf"\b({GUESS_WORDS[lang]})\b", re.I) if lang in GUESS_WORDS else None
    blocks = sc["blocks"]
    errors, uncertain = [], []
    for i, b in enumerate(blocks):
        dur = len(tts_text(b, sc)) / CHARS_PER_S
        room = free_room(b, i, blocks, speech, m["duration"])
        if dur > room * MAX_SPEEDUP + 0.2 and not b.get("extended"):
            errors.append({"block": i + 1, "time": b["start"], "type": "too_long",
                           "text": f"~{dur:.1f} s of text, room {room:.1f} s (even with 15 % speed-up)"})
        if b.get("confidence", 1) < 0.6:
            uncertain.append({"start": max(0.0, b["start"] - 3), "end": min(m["duration"], b["start"] + 8),
                              "reason": "low confidence", "suggested_fps": 2, "importance": 2,
                              "what_to_find": b.get("uncertain_why") or b["text"]})
        if guesses and guesses.search(b["text"]):
            errors.append({"block": i + 1, "time": b["start"], "type": "guess",
                           "text": f"guess in the text for the listener: \"{guesses.search(b['text']).group(0)}\""})
    # pauses in which nothing is said for a long time – a hole in the plot (e.g. scene perception failed)
    speaking = sorted((b["start"], b["start"] + len(tts_text(b, sc)) / CHARS_PER_S) for b in blocks)
    # closing credits get one block on purpose – what follows it is not an undescribed hole
    credits = min((b["start"] for b in blocks if b.get("credits")), default=m["duration"])
    for g in m["speech_gaps"]:
        g = {"start": g["start"], "end": min(g["end"], credits)}
        cursor = g["start"]
        for a0, a1 in speaking + [(g["end"], g["end"])]:
            a0 = min(max(a0, g["start"]), g["end"])
            if a0 - cursor >= UNCOVERED_S:
                errors.append({"block": "-", "time": cursor, "type": "uncovered",
                               "text": f"pause {fmt(cursor)}–{fmt(a0)} ({a0 - cursor:.0f} s) without description"})
                for z in range(int(cursor), int(a0), 15):
                    uncertain.append({"start": float(z), "end": float(min(a0, z + 15)), "reason": "uncovered pause",
                                      "suggested_fps": 1, "importance": 2,
                                      "what_to_find": "What happens in this segment? The script says nothing here."})
            cursor = max(cursor, min(a1, g["end"]))
    for c in errors:
        print(f"block {c['block']} ({fmt(c['time'])}): {c['text']}")
    if not errors:
        print("All blocks fit into the pauses (estimate), no guesses and no long uncovered pauses.")
    if not uncertain and (work / "uncertain_segments.json").exists():
        (work / "uncertain_segments.json").unlink()
    if uncertain:
        (work / "uncertain_segments.json").write_text(json.dumps({"segments": uncertain}, ensure_ascii=False, indent=1))
        print(f"{len(uncertain)} segments to look at (low confidence, uncovered pauses) → {work}/uncertain_segments.json (for `detail`)")
    return errors


def _tts(voice: Path, text: str, wav: Path, tempo: float) -> float:
    subprocess.run([sys.executable, "-m", "piper", "-m", str(voice), "-f", str(wav), "--length-scale", f"{tempo:.3f}"],
                   input=text, text=True, check=True, capture_output=True)
    return duration(wav)


def _vtt_time(t: float) -> str:
    h, rest = divmod(max(0.0, t), 3600)
    mi, se = divmod(rest, 60)
    return f"{int(h):02d}:{int(mi):02d}:{se:06.3f}"


DUCK_DB = -9.0  # original sound under the description
RAMP_IN, RAMP_OUT = 0.25, 0.6  # s – smooth transition before the description and after it


def duck_envelope(spans: list[tuple[float, float]]) -> str:
    """ffmpeg expression for the volume filter (eval=frame): 1 outside the description, 10^(DUCK_DB/20) during it,
    linear ramp RAMP_IN s before the start and recovery RAMP_OUT s after the end (overlapping spans merge)."""
    g = 10 ** (DUCK_DB / 20)
    parts = [f"clip((t-{a - RAMP_IN:.3f})/{RAMP_IN},0,1)*clip(({b + RAMP_OUT:.3f}-t)/{RAMP_OUT},0,1)" for a, b in spans]
    if not parts:
        return "1"
    return f"1-{1 - g:.4f}*min(1,{'+'.join(parts)})"


def _mix(video: Path, clips: list[tuple[float, Path, float]], length: float, target_mix: Path,
         target_track: Path | None) -> None:
    """Description (clips: time, wav, duration) + original sound. The original sound is smoothly ducked under
    the description by DUCK_DB (exact envelope, not a compressor – that barely ducked loud music)."""
    inputs, filt = ["-i", str(video)], []
    for j, (t, wav, _) in enumerate(clips, 1):
        inputs += ["-i", str(wav)]
        ms = int(t * 1000)
        filt.append(f"[{j}:a]aresample=48000,aformat=channel_layouts=stereo,adelay={ms}|{ms}[n{j}]")
    n = len(clips)
    filt.append("".join(f"[n{j}]" for j in range(1, n + 1)) +
                f"amix=inputs={n}:duration=longest:normalize=0,apad,atrim=0:{length:.3f}[nar]")
    envelope = duck_envelope([(t, t + d) for t, _, d in clips])
    if has_audio(video):
        filt.append(f"[0:a]aresample=48000,aformat=channel_layouts=stereo,volume=eval=frame:volume='{envelope}'[duck]")
    else:
        filt.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{length:.3f}[duck]")
    filt.append("[nar]asplit=2[nar2][nar3]" if target_track else "[nar]anull[nar2]")
    filt.append("[duck][nar2]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95:level=disabled[mix]")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(filt),
           "-map", "[mix]", "-c:a", "libmp3lame", "-q:a", "3", str(target_mix)]
    if target_track:
        cmd += ["-map", "[nar3]", "-c:a", "libmp3lame", "-q:a", "4", str(target_track)]
    run(cmd)


def _extend_video(video: Path, freezes: list[tuple[float, float]], target: Path) -> None:
    """Extended audio description: at times t the picture freezes for d seconds (last frame),
    the original sound goes silent for that time, then the video continues."""
    points = [0.0] + [t for t, _ in freezes] + [None]
    filt, pieces = [], []
    for k in range(len(points) - 1):
        a0, a1 = points[k], points[k + 1]
        d = freezes[k][1] if k < len(freezes) else 0.0
        cut = f"start={a0:.3f}" + (f":end={a1:.3f}" if a1 is not None else "")
        filt.append(f"[0:v]trim={cut},setpts=PTS-STARTPTS" + (f",tpad=stop_mode=clone:stop_duration={d:.3f}" if d else "") + f"[v{k}]")
        filt.append(f"[0:a]atrim={cut},asetpts=PTS-STARTPTS" + (f",apad=pad_dur={d:.3f}" if d else "") + f"[a{k}]")
        pieces.append(f"[v{k}][a{k}]")
    filt.append("".join(pieces) + f"concat=n={len(pieces)}:v=1:a=1[v][a]")
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-filter_complex", ";".join(filt),
         "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
         "-c:a", "aac", "-b:a", "160k", str(target)])


def cmd_audio(a) -> None:
    """TTS (piper) → narration only, a separate description track, a mix with the original sound (smooth ducking),
    a video with two audio tracks, WebVTT kind=descriptions. A block that does not fit is sped up
    by at most 15 %; if it still does not fit and has "extended": true, an extended version is also made,
    in which the picture stops for that time."""
    work = Path(a.work)
    m = json.loads((work / "manifest.json").read_text())
    lang = m.get("language") or "sk"
    sc = json.loads(Path(a.script).read_text())
    speech = load_speech(work)
    voice_name = a.voice or VOICES.get(lang)
    if not voice_name:
        sys.exit(f"No default voice for language \"{lang}\" – pass --voice (a piper voice name).")
    ad = work / "audio"
    ad.mkdir(exist_ok=True)
    voice = ad / f"{voice_name}.onnx"
    if not voice.exists():
        run([sys.executable, "-m", "piper.download_voices", "--download-dir", str(ad), voice_name])
    blocks, info = sc["blocks"], []
    for i, b in enumerate(blocks):
        wav = ad / f"block_{i + 1:03d}.wav"
        text = tts_text(b, sc)
        d = _tts(voice, text, wav, a.tempo)
        room = free_room(b, i, blocks, speech, m["duration"])
        tempo = a.tempo
        if d > room + 0.05 and room > 0:
            tempo = max(a.tempo / MAX_SPEEDUP, a.tempo * room / d)  # speed up by at most 15 %
            d = _tts(voice, text, wav, tempo)
        overflow = max(0.0, d - room)
        info.append({"block": i + 1, "start": b["start"], "duration": round(d, 2), "room": round(room, 2),
                     "tempo": round(tempo / a.tempo, 3), "overflow": round(overflow, 2),
                     "freeze": bool(b.get("extended")) and overflow > 0.1, "wav": wav})
    for x in info:
        if x["overflow"] > 0.05 and not x["freeze"] and not blocks[x["block"] - 1].get("over_speech"):
            print(f"! block {x['block']} ({fmt(x['start'])}) overflows by {x['overflow']:.1f} s "
                  f"(even after speeding up to {x['tempo']:.2f})", file=sys.stderr)
    # 1) narration only
    silence = ad / "silence.wav"
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono", "-t", "0.8", str(silence)])
    (ad / "list.txt").write_text("".join(f"file '{x['wav'].name}'\nfile 'silence.wav'\n" for x in info))
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(ad / "list.txt"),
         "-c:a", "libmp3lame", "-q:a", "4", str(work / "narration_only.mp3")])
    video = work / m["video"]
    # 2) standard version: mix + separate track + video with two tracks + VTT
    _mix(video, [(x["start"], x["wav"], x["duration"]) for x in info], m["duration"],
         work / "audio_description_mix.mp3", work / "description_track.mp3")
    _video_with_two_tracks(video, work / "audio_description_mix.mp3", work / "video_with_ad.mp4", lang)
    _write_vtt(work / "descriptions.vtt", [(x["start"], x["start"] + x["duration"], blocks[x["block"] - 1]["text"]) for x in info], lang)
    outputs = ["narration_only.mp3", "description_track.mp3", "audio_description_mix.mp3", "video_with_ad.mp4", "descriptions.vtt"]
    # 3) extended version (picture stops), if some block needs it
    frz = [(x["start"] + x["room"], x["overflow"] + 0.3) for x in info if x["freeze"]]
    if frz:
        def shift(t):
            # a freeze at time tz shifts only what comes AFTER it; a block that starts at tz
            # (room 0) plays exactly during the freeze
            return t + sum(d for tz, d in frz if tz < t - 1e-6)
        ev = ad / "video_extended.mp4"
        _extend_video(video, frz, ev)
        dl = duration(ev)
        _mix(ev, [(shift(x["start"]), x["wav"], x["duration"]) for x in info], dl, ad / "extended_mix.mp3", None)
        _video_with_two_tracks(ev, ad / "extended_mix.mp3", work / "video_with_ad_extended.mp4", lang)
        _write_vtt(work / "descriptions_extended.vtt",
                   [(shift(x["start"]), shift(x["start"]) + x["duration"], blocks[x["block"] - 1]["text"]) for x in info], lang)
        outputs += ["video_with_ad_extended.mp4", "descriptions_extended.vtt"]
        print(f"Extended version: {len(frz)} freezes, {sum(d for _, d in frz):.1f} s in total", file=sys.stderr)
    (work / "audio_info.json").write_text(json.dumps([{k: v for k, v in x.items() if k != "wav"} for x in info],
                                                     ensure_ascii=False, indent=1))
    print("Done: " + ", ".join(outputs) + f" in {work}")


def _video_with_two_tracks(video: Path, mix: Path, target: Path, lang: str = "sk") -> None:
    """Track 1 = audio description (default), track 2 = original sound."""
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-i", str(mix), "-map", "0:v", "-map", "1:a"]
    if has_audio(video):
        cmd += ["-map", "0:a"]
    cmd += ["-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
            "-metadata:s:a:0", f"language={ISO639_2.get(lang, lang)}",
            "-metadata:s:a:0", f"title=Audio description ({lang.upper()})",
            "-disposition:a:0", "default"]
    if has_audio(video):
        cmd += ["-metadata:s:a:1", "title=Original audio", "-disposition:a:1", "0"]
    run(cmd + [str(target)])


def _write_vtt(target: Path, cues: list[tuple[float, float, str]], lang: str = "sk") -> None:
    """WebVTT for <track kind="descriptions"> – screen readers read it by themselves."""
    lines = ["WEBVTT", "Kind: descriptions", f"Language: {lang}", ""]
    for k, (a0, a1, text) in enumerate(cues, 1):
        lines += [str(k), f"{_vtt_time(a0)} --> {_vtt_time(a1)}", text, ""]
    target.write_text("\n".join(lines))


# ---------------------------------------------------------------- layers: perception → story → script


def scenes_in_grid(m: dict, g: dict) -> list[dict]:
    """Scenes the grid contains, with cell numbers and times."""
    out: dict[int, dict] = {}
    for c in g["cells"]:
        sc = c["scene"] or scene_number(m["scenes"], c["time"])
        z = out.setdefault(sc, {"scene": sc, "start": m["scenes"][sc - 1]["start"], "end": m["scenes"][sc - 1]["end"], "cells": []})
        z["cells"].append(c["index"])
    return list(out.values())


def _characters_text(work: Path) -> str:
    reg = work / "characters.json"
    if not reg.exists():
        return "(no character register yet – refer to characters by description)"
    return "\n".join(f"- {p['name']}: {p['appearance']} (name confidence {p.get('confidence', '-')})"
                     for p in json.loads(reg.read_text())["characters"])


def perception_input_for_grid(work: Path, m: dict, g: dict, transcript: list[dict], question: str | None = None) -> str:
    sc = scenes_in_grid(m, g)
    r = [f"### {g['header']}", f"File: {g['file']}"]
    if question:
        r.append(f"QUESTION (targeted look): {question}")
    r.append("Scenes in the grid: " + "; ".join(f"sc.{x['scene']} {fmt(x['start'])}–{fmt(x['end'])} = cells {','.join(map(str, x['cells']))}" for x in sc))
    r.append("Cells: " + ", ".join(f"{c['index']}={c['time_text']}" for c in g["cells"]))
    a, b = g["cells"][0]["time"], g["cells"][-1]["time"]
    r.append("Speech in range:\n" + transcript_in_range(transcript, a - 2, b + 2))
    return "\n".join(r) + "\n"


def fix_record_time(z: dict, scenes: list[dict]) -> dict:
    """A time outside the record's scene is a misread of the burned-in MM:SS (scene at 3:43 written as 3.0) – use the scene's."""
    if isinstance(z.get("scene"), int) and 1 <= z["scene"] <= len(scenes):
        s = scenes[z["scene"] - 1]
        if not s["start"] - 1 <= (z.get("start") or 0) <= s["end"] + 1:
            z["start"], z["end"] = s["start"], s["end"]
    return z


def load_log(work: Path) -> list[dict]:
    p = work / "log.jsonl"
    if not p.exists():
        return []
    scenes = json.loads((work / "manifest.json").read_text())["scenes"]
    recs = [fix_record_time(json.loads(l), scenes) for l in p.read_text().splitlines() if l.strip()]
    return sorted(recs, key=lambda z: (z.get("start", 0), z.get("source", "") != "overview"))


def append_to_log(work: Path, records: list[dict], source: str) -> None:
    with open(work / "log.jsonl", "a") as f:
        for z in records:
            f.write(json.dumps({**z, "source": source}, ensure_ascii=False) + "\n")


def log_text(log: list[dict]) -> str:
    """Compact text log for the layers that do not see the video."""
    r = []
    for z in log:
        chars = "; ".join(f"{k}: {v}" for k, v in (z.get("characters") or {}).items())
        r.append(f"[{fmt(z.get('start', 0))}–{fmt(z.get('end', 0))} sc.{z.get('scene', '?')} {z.get('source', '')}] "
                 f"place: {z.get('place')} | characters: {chars} | new: {z.get('new_characters') or '-'} | "
                 f"action: {z.get('action')} | objects: {', '.join(z.get('objects') or []) or '-'} | "
                 f"text: {z.get('onscreen_text')} | shot: {z.get('shot')} | confidence: {z.get('confidence')} | "
                 f"unclear: {'; '.join(z.get('unclear') or []) or '-'}"
                 + (f" | answer to question: {z['answer']}" if z.get("answer") else ""))
    return "\n".join(r)


def cmd_perceive(a) -> None:
    """Layer 1. Without --api writes WORK/perception_input_<grids>.md (for an agent: fill in records for every grid
    following prompts/perception.md and add them to WORK/log.jsonl, e.g. with `log-add`). With --api the model
    does it grid by grid. --grids overview | probe (targeted grids from `detail --prefix probe`)."""
    work = Path(a.work)
    m = json.loads((work / "manifest.json").read_text())
    transcript = json.loads((work / "transcript.json").read_text())
    if a.grids == "overview":
        items = [(g, None) for g in m["overview"]]
    else:
        items = [(g, u.get("what_to_find")) for u in m.get(a.grids, []) for g in u["grids"]]
    head = f"CHARACTER REGISTER:\n{_characters_text(work)}\n"
    inp = [head] + [perception_input_for_grid(work, m, g, transcript, q) for g, q in items]
    (work / f"perception_input_{a.grids}.md").write_text("\n".join(inp))
    if not a.api:
        print(f"Input: {work}/perception_input_{a.grids}.md ({len(items)} grids); records → {work}/log.jsonl")
        return
    lang = m.get("language") or "sk"
    system = load_prompt("ad_rules.md", lang) + "\n\n" + load_prompt("perception.md", lang)
    failed = []
    for g, q in items:
        content = [{"type": "text", "text": head + perception_input_for_grid(work, m, g, transcript, q)}, _image(work / g["file"])]
        sc = scenes_in_grid(m, g)
        recs: list[dict] = []
        for attempt in range(2):
            recs = _api_json(work, f"perception_{a.grids}", a.model, system, content, 16000, key="records").get("records") or []
            recs = [fix_record_time(z, m["scenes"]) for z in recs]
            # overview: every scene of the grid must have a record; targeted grid: at least one record
            missing = ([x for x in sc if not any(z.get("scene") == x["scene"] or x["start"] - 0.3 <= (z.get("start") or -1) < x["end"]
                                                 for z in recs)] if a.grids == "overview" else ([] if recs else sc))
            if not missing:
                break
            if not attempt:
                print(f"! perception {g['file']}: scenes {[x['scene'] for x in missing]} left without a record – grid again",
                      file=sys.stderr)
        for x in missing:
            # a hole in the log must be visible – the story asks a question about it instead of not knowing about it
            recs.append({"start": x["start"], "end": x["end"], "scene": x["scene"], "action": "unclear", "confidence": 0,
                         "unclear": ["perception of this scene failed – it is unknown what happens in it"] + ([q] if q else [])})
        failed += [x["scene"] for x in missing]
        if q:
            for z in recs:
                z.setdefault("answer", z.get("action"))
                z.setdefault("question", q)
        append_to_log(work, recs, a.grids)
    print(f"Log: {len(load_log(work))} records → {work}/log.jsonl")
    if failed:
        print(f"! perception ({a.grids}): {len(failed)} scenes without a record (in the log as \"unclear\"): {failed}", file=sys.stderr)


def cmd_log_add(a) -> None:
    """Adds records from a JSON file ({"records": [...]} or a list) to WORK/log.jsonl (agent mode)."""
    work = Path(a.work)
    recs = json.loads(Path(a.file).read_text())
    recs = recs.get("records", recs) if isinstance(recs, dict) else recs
    append_to_log(work, recs, a.source)
    print(f"+{len(recs)} → {len(load_log(work))} records in {work}/log.jsonl")


def story_input(work: Path) -> str:
    m = json.loads((work / "manifest.json").read_text())
    transcript = json.loads((work / "transcript.json").read_text())
    return (f"# Video: {m['info'].get('title') or m['video']}, duration {fmt(m['duration'])}\n\n"
            f"# Characters\n{_characters_text(work)}\n\n# Perception LOG\n{log_text(load_log(work))}\n\n"
            f"# Speech with timestamps\n{transcript_in_range(transcript, 0, m['duration'])}\n")


def cmd_story(a) -> None:
    """Layer 2 (text only). Writes WORK/story_input.md; with --api produces WORK/story.json
    (plot, character states, questions + "segments" for `detail --prefix probe`)."""
    work = Path(a.work)
    inp = story_input(work)
    (work / "story_input.md").write_text(inp)
    if a.api:
        o = _api_json(work, "story", a.model, load_prompt("story.md", work_language(work)), [{"type": "text", "text": inp}], 8000,
                      key="scenes")
        (work / "story.json").write_text(json.dumps(o, ensure_ascii=False, indent=1))
        print(f"Story: {len(o.get('scenes', []))} units, {len(o.get('questions', []))} questions")
    else:
        print(f"Input: {work}/story_input.md (prompt: {PROMPTS}/story.md) → {work}/story.json")


def script_input(work: Path) -> str:
    m = json.loads((work / "manifest.json").read_text())
    info = m.get("info", {})
    story = (work / "story.json").read_text() if (work / "story.json").exists() else "(none)"
    st = json.loads(story) if story != "(none)" else {}
    # time jumps and identities are easy to overlook inside the story JSON – the script must act on them
    jumps = "\n".join(f"- {fmt(j.get('start', 0))}–{fmt(j.get('end', 0))} {j.get('kind')} (matches {fmt(j.get('related_to') or 0)}): "
                      f"{j.get('evidence')}" for j in st.get("time_jumps") or []) or "(none)"
    same = "\n".join(f"- {' = '.join(e.get('labels') or [])} (confidence {e.get('confidence')}"
                     f"{'' if (e.get('confidence') or 0) >= 0.7 else ' – only possible, do not state it'}): {e.get('evidence')}"
                     for e in st.get("same_entity") or []) or "(none)"
    return (f"# Video: {info.get('title') or m['video']} | channel: {info.get('channel')} | duration {fmt(m['duration'])} | "
            f"license: {info.get('license') or '-'}\n\n# Characters\n{_characters_text(work)}\n\n"
            f"# Time jumps (announce each one in the script, and the return)\n{jumps}\n\n"
            f"# Same characters/objects under different labels (use one label, say the link once)\n{same}\n\n"
            f"# Story\n{story}\n\n# Perception LOG\n{log_text(load_log(work))}\n\n"
            f"# Speech with timestamps\n{transcript_in_range(json.loads((work / 'transcript.json').read_text()), 0, m['duration'])}\n\n"
            f"# Speech gaps (blocks go here)\n"
            + ", ".join(f"{g['start']:.1f}–{g['end']:.1f} ({g['end'] - g['start']:.1f} s)" for g in m["speech_gaps"]) + "\n")


def cmd_script(a) -> None:
    """Layer 4 (text only): WORK/script_input.md; with --api produces WORK/script.json."""
    work = Path(a.work)
    inp = script_input(work)
    (work / "script_input.md").write_text(inp)
    if a.api:
        lang = work_language(work)
        system = load_prompt("ad_rules.md", lang) + "\n\n" + load_prompt("script.md", lang)
        sc = _api_json(work, "script", a.model, system, [{"type": "text", "text": inp + (a.extra or "")}], 16000,
                       key="blocks")
        (work / "script.json").write_text(json.dumps(sc, ensure_ascii=False, indent=1))
        print(f"Script: {len(sc.get('blocks', []))} blocks")
    else:
        print(f"Input: {work}/script_input.md (prompts: ad_rules.md + script.md) → {work}/script.json")


def cmd_frame(a) -> None:
    """Frame in full resolution (to read on-screen text): WORK/frames_full/<t>.png."""
    work = Path(a.work)
    m = json.loads((work / "manifest.json").read_text())
    (work / "frames_full").mkdir(exist_ok=True)
    for t in a.times:
        target = work / "frames_full" / f"{float(t):08.2f}.png"
        run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{float(t):.3f}", "-i", str(work / m["video"]),
             "-frames:v", "1", str(target)])
        print(target)


# ---------------------------------------------------------------- review


def review_input(work: Path, script: Path) -> str:
    """Text input for the review pass (prompts/review.md): the script with estimated length and free
    room, the character register, speech with timestamps and pauses. The model does not see the video – it reads as a listener."""
    m = json.loads((work / "manifest.json").read_text())
    sc = json.loads(script.read_text())
    speech = load_speech(work)
    transcript = json.loads((work / "transcript.json").read_text())
    reg = work / "characters.json"
    blocks = sc["blocks"]
    r = [f"# Script: {sc.get('title', '')}", "",
         "Flag: \"freezes video\" = extended audio description (the video waits until the block finishes – it may be longer "
         "than the room); \"over speech\" = deliberately short over a line of dialogue.", "",
         "| # | time | est. s | room s | flag | confidence | text |", "|---|---|---|---|---|---|---|"]
    for i, b in enumerate(blocks):
        flag = "freezes video" if b.get("extended") else ("over speech" if b.get("over_speech") else "")
        r.append(f"| {i + 1} | {fmt(b['start'])} ({b['start']:.1f}) | {len(tts_text(b, sc)) / CHARS_PER_S:.1f} | "
                 f"{free_room(b, i, blocks, speech, m['duration']):.1f} | {flag} | {b.get('confidence', '-')} | {b['text']} |")
    r += ["", "# Characters", reg.read_text() if reg.exists() else "(none)", "",
          "# Speech with timestamps (the listener hears it from the film)", transcript_in_range(transcript, 0, m["duration"]), "",
          "# Speech gaps", ", ".join(f"{fmt(g['start'])}–{fmt(g['end'])}" for g in m["speech_gaps"])]
    return "\n".join(r)


def cmd_review(a) -> None:
    """Writes WORK/review_input.md. The agent (or an independent colleague) reads it with prompts/review.md
    and writes the result to WORK/review.json; its "segments" go to `detail`. With --api the model does it."""
    work = Path(a.work)
    inp = review_input(work, Path(a.script))
    (work / "review_input.md").write_text(inp)
    if a.api:
        o = _api_json(work, "review", a.model, load_prompt("review.md", work_language(work)), [{"type": "text", "text": inp}],
                      8000, key="findings")
        (work / "review.json").write_text(json.dumps(o, ensure_ascii=False, indent=1))
        print(f"Review: {len(o.get('findings', []))} findings, {len(o.get('segments', []))} segments to verify")
    else:
        print(f"Input for review: {work}/review_input.md (prompt: {PROMPTS}/review.md)")


# ---------------------------------------------------------------- auto (Anthropic API)


USAGE: list[dict] = []  # tokens of every API call (for the cost per video)


def backend() -> str:
    """Who does the model's work:
    "api"        – Anthropic API, key in ANTHROPIC_API_KEY (pay per use),
    "claude-cli" – Claude Code (`claude -p`) logged in with your Claude Pro/Max subscription; it is called
                   exactly as you use it in the terminal (no token extraction). Subscription limits
                   apply – a long video may stop at the 5-hour limit.
    Choice: VIDEO_BACKEND=api|claude-cli; otherwise api if the key is set, else claude-cli if `claude` is in PATH."""
    b = os.environ.get("VIDEO_BACKEND")
    if b:
        return b
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "api"
    if shutil.which("claude"):
        return "claude-cli"
    sys.exit("No model: set ANTHROPIC_API_KEY, or install and log in to Claude Code (`claude`), "
             "or use agent mode (without --api).")


# in this mode Claude Code may only read the grid images
FORBIDDEN_TOOLS = "Bash Edit Write MultiEdit NotebookEdit WebFetch WebSearch Task Glob Grep TodoWrite"

# Claude Code takes aliases; API model names are converted to an alias for the subscription
CLI_ALIASES = {"haiku": "haiku", "sonnet": "sonnet", "opus": "opus"}


def _cli_model(model: str) -> str:
    for k in CLI_ALIASES:
        if k in model:
            return CLI_ALIASES[k]
    return model


def _claude_cli(model: str, system: str, content: list) -> str:
    """One call via `claude -p` (headless Claude Code). Images are passed as file paths and it reads
    them with the Read tool (write, network and shell tools are forbidden, MCP servers off). The prompt goes
    via stdin, the answer comes as JSON. On a subscription limit it exits with a hint to use --resume."""
    import time
    texts = [c["text"] for c in content if c.get("type") == "text"]
    paths = [c["_path"] for c in content if c.get("type") == "image"]
    prompt = "\n\n".join(texts)
    if paths:
        prompt += ("\n\nIMAGES (read them all with the Read tool first, in this order):\n"
                   + "\n".join(paths))
    prompt += "\n\nAnswer only with the requested JSON, no other text."
    dirs = sorted({str(Path(c).parent) for c in paths})
    cmd = ["claude", "-p", "--output-format", "json", "--model", _cli_model(model),
           "--append-system-prompt", system, "--max-turns", str(4 + len(paths)),
           "--allowedTools", "Read", "--disallowedTools", FORBIDDEN_TOOLS, "--strict-mcp-config"]
    for d in dirs:
        cmd += ["--add-dir", d]
    # no API key in the environment – otherwise Claude Code would bill via the API, not the subscription
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    for attempt in range(4):
        try:
            r = subprocess.run(cmd, input=prompt, text=True, capture_output=True, timeout=900,
                               cwd=dirs[0] if dirs else None, env=env)
        except subprocess.TimeoutExpired:
            print(f"! claude -p did not answer within 15 min (attempt {attempt + 1})", file=sys.stderr)
            continue
        try:
            resp = json.loads(r.stdout)
        except json.JSONDecodeError:
            resp = {}
        if r.returncode == 0 and not resp.get("is_error") and resp.get("result") is not None:
            u = resp.get("usage", {})
            USAGE.append({"model": _cli_model(model), "backend": "claude-cli",
                          "input": u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                          + u.get("cache_creation_input_tokens", 0),
                          "output": u.get("output_tokens", 0), "turns": resp.get("num_turns"),
                          "api_cost_estimate_usd": resp.get("total_cost_usd")})
            return resp["result"]
        error = (resp.get("result") or resp.get("subtype") or r.stderr or r.stdout or "")[-400:]
        low = error.lower()
        if "usage limit" in low or "limit reached" in low or "resets" in low:
            sys.exit(f"Claude Code: subscription limit reached. Once it resets, run the same command "
                     f"with --resume (finished steps are skipped). ({error.strip()})")
        wait = 60 * (attempt + 1) if ("rate" in low or "overloaded" in low or "529" in low) else 10 * (attempt + 1)
        print(f"! claude -p failed (attempt {attempt + 1}, waiting {wait} s): {error.strip()}", file=sys.stderr)
        USAGE.append({"model": _cli_model(model), "backend": "claude-cli", "input": 0, "output": 0, "error": error[-120:]})
        time.sleep(wait)
    return ""


def _api(model: str, system: str, content: list, max_tokens: int = 4000) -> str:
    if backend() == "claude-cli":
        return _claude_cli(model, system, content)
    content = [{k: v for k, v in c.items() if k != "_path"} for c in content]
    import urllib.request
    key = os.environ["ANTHROPIC_API_KEY"]
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps({"model": model, "max_tokens": max_tokens, "system": system,
                         "messages": [{"role": "user", "content": content}]}).encode(),
        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    import time
    import urllib.error
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                resp = json.load(r)
            u = resp.get("usage", {})
            USAGE.append({"model": model, "input": u.get("input_tokens", 0), "output": u.get("output_tokens", 0)})
            return "".join(c.get("text", "") for c in resp["content"])
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 529) or attempt == 4:
                raise
            time.sleep(2 ** attempt * 5)
    return ""


def _image(p: Path) -> dict:
    return {"type": "image", "_path": str(Path(p).resolve()),
            "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(p.read_bytes()).decode()}}


def _json(text: str, key: str | None = None) -> dict:
    """JSON from a model answer (also inside a ```json``` fence or with text before/after it); returns {} on bad JSON.
    With `key`, takes the last top-level object that has it (a model sometimes sends a draft, then "correcting that" a second one)."""
    text = re.sub(r"```(?:json)?", "", text or "")
    # numbers with a leading zero ("start": 03.0) – the model copies them from the burned-in MM:SS time; invalid JSON
    text = re.sub(r'("\s*:\s*-?)0+(\d)', r"\1\2", text)
    dec = json.JSONDecoder()
    found, pos = [], 0
    while (i := text.find("{", pos)) >= 0:
        try:
            o, pos = dec.raw_decode(text, i)  # continue after the whole object, not inside it
        except json.JSONDecodeError:
            pos = i + 1
            continue
        if isinstance(o, dict):
            found.append(o)
    with_key = [o for o in found if key is None or key in o]
    return (with_key[-1] if key and with_key else (with_key or found or [{}])[0])


RESPONSES: dict[str, int] = {}  # step → number of saved answers (file numbering)


def _api_json(work: Path, step: str, model: str, system: str, content: list, max_tokens: int = 4000,
              key: str | None = None) -> dict:
    """A model call that must not fail silently: saves the raw answer to WORK/responses/<step>_<n>.txt
    and on invalid JSON (or without the key `key`) asks once more. On the second failure returns {} with a warning."""
    (work / "responses").mkdir(exist_ok=True)
    for attempt in range(2):
        if attempt:
            content = content + [{"type": "text", "text": "The previous answer was not valid JSON in the requested form. "
                                                          "Return ONLY valid JSON, no other text."}]
        text = _api(model, system, content, max_tokens)
        RESPONSES[step] = RESPONSES.get(step, 0) + 1
        file = work / "responses" / f"{step}_{RESPONSES[step]:03d}.txt"
        file.write_text(text or "")
        o = _json(text, key)
        if o and (key is None or key in o):
            return o
        print(f"! {step}: the model did not return valid JSON{f' with “{key}”' if key else ''} ({file.name})"
              + (" – trying again" if not attempt else " – step skipped"), file=sys.stderr)
    return {}


def select_probes(segments: list[dict], max_n: int, findings: list[dict] | None = None) -> list[dict]:
    """Segments for a targeted look by importance (1 = the listener will not understand the plot), not by time.
    Missing importance is filled in from the review findings (field range) or story questions (start, end)
    with an overlapping segment (else 2). Overlapping and adjacent
    segments (within 1 s) are merged if together they are not longer than 15 s."""
    def importance(s: dict) -> int:
        if s.get("importance"):
            return int(s["importance"])
        d = [n.get("importance") or 2 for n in findings or [] if (u := n.get("range") or n)
             and u.get("start", 0) < s["end"] and s["start"] < u.get("end", 0)]
        return min(d, default=2)
    merged: list[dict] = []
    for s in sorted(({**s, "importance": importance(s)} for s in segments), key=lambda s: (s["importance"], s["start"])):
        for z in merged:
            if s["start"] <= z["end"] + 1 and z["start"] <= s["end"] + 1 and max(z["end"], s["end"]) - min(z["start"], s["start"]) <= 15:
                z["start"], z["end"] = min(z["start"], s["start"]), max(z["end"], s["end"])
                z["suggested_fps"] = max(z.get("suggested_fps", 2), s.get("suggested_fps", 2))
                if s.get("what_to_find") and s["what_to_find"] not in z.get("what_to_find", ""):
                    z["what_to_find"] = "; ".join(filter(None, [z.get("what_to_find"), s["what_to_find"]]))
                break
        else:
            merged.append(dict(s))
    return merged[:max_n]


def cmd_auto(a) -> None:
    """The whole chain without a human, in layers: characters → perception (log) → story (text) → targeted look
    at the questions (back into the log) → story again → script (text) → review → targeted look at the findings → script again
    → audio. Model: Anthropic API or Claude Code with a subscription (see backend()). Usage → usage.json."""
    work = Path(a.o)
    print(f"Model: {backend()}  (perception: {a.model_perception}, other: {a.model_main})", file=sys.stderr)
    if a.reuse_prep and (work / "manifest.json").exists():
        print("Preparation: using the existing manifest, grids and transcript", file=sys.stderr)
    else:
        cmd_prepare(a)
    m = json.loads((work / "manifest.json").read_text())
    lang = m.get("language") or "sk"
    transcript = json.loads((work / "transcript.json").read_text())
    head = (work / "overview.md").read_text().split("\n### ")[0]
    state_p = work / "auto_state.json"
    if a.resume and state_p.exists():
        finished = json.loads(state_p.read_text())
        if (work / "usage.json").exists():
            USAGE.extend(json.loads((work / "usage.json").read_text()))
        print(f"Resuming, finished steps: {', '.join(finished) or '-'}", file=sys.stderr)
    else:
        finished = []
        stamp = __import__("time").strftime("%Y%m%d-%H%M%S")
        for f in ("log.jsonl", "characters.json", "story.json", "uncertain_segments.json", "auto_state.json",
                  "usage.json", "review.json", "review_round1.json", "script.json", "script_round1.json",
                  "probe_segments.json", "recheck_segments.json", "probe.md", "recheck.md", "responses"):
            if (work / f).exists():  # clean run – move old files aside, do not overwrite them
                (work / "previous").mkdir(exist_ok=True)
                (work / f).rename(work / "previous" / f"{stamp}_{f}")
        if [m.pop(k) for k in ("probe", "recheck") if k in m]:  # targeted-look grids from a previous run
            (work / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, indent=1))

    def step(name: str) -> bool:
        """True = the step must be done; with --resume finished steps are skipped."""
        (work / "usage.json").write_text(json.dumps(USAGE, indent=1))
        if name in finished:
            print(f"== {name}: finished, skipping ==", file=sys.stderr)
            return False
        print(f"\n== {name} ==", file=sys.stderr)
        return True

    def done(name: str) -> None:
        finished.append(name)
        state_p.write_text(json.dumps(finished, ensure_ascii=False))
        (work / "usage.json").write_text(json.dumps(USAGE, indent=1))

    def script_ok() -> None:
        sc = json.loads((work / "script.json").read_text())
        if not sc.get("blocks"):
            sys.exit("The model did not return a script (\"blocks\" missing) – see the output above and run again with --resume.")

    # 1) character register: all overview grids at once + speech
    if step("characters"):
        content = [{"type": "text", "text": f"{head}\n\nSPEECH TRANSCRIPT:\n{transcript_in_range(transcript, 0, m['duration'])}"}]
        content += [_image(work / g["file"]) for g in m["overview"][:20]]
        reg = _api_json(work, "characters", a.model_main, load_prompt("characters.md", lang), content,
                        key="characters")
        (work / "characters.json").write_text(json.dumps(reg or {"characters": []}, ensure_ascii=False, indent=1))
        done("characters")
    # 2) perception: log from the overview grids
    if step("perception"):
        if (work / "log.jsonl").exists():
            (work / "log.jsonl").unlink()  # unfinished perception – all over again
        cmd_perceive(argparse.Namespace(work=a.o, grids="overview", api=True, model=a.model_perception))
        done("perception")

    def story() -> dict:
        cmd_story(argparse.Namespace(work=a.o, api=True, model=a.model_main))
        return json.loads((work / "story.json").read_text())

    def look(segments: list[dict], prefix: str) -> None:
        if not segments:
            return
        # unfinished look from a previous run: remove its records from the log
        log = [z for z in load_log(work) if z.get("source") != prefix]
        (work / "log.jsonl").write_text("".join(json.dumps(z, ensure_ascii=False) + "\n" for z in log))
        (work / f"{prefix}_segments.json").write_text(json.dumps({"segments": segments}, ensure_ascii=False, indent=1))
        cmd_detail(argparse.Namespace(work=a.o, segments=str(work / f"{prefix}_segments.json"), prefix=prefix))
        cmd_perceive(argparse.Namespace(work=a.o, grids=prefix, api=True, model=a.model_main))

    # 3) story from text only → questions → targeted look → story again
    if step("story"):
        story()
        done("story")
    if step("probe"):
        st = json.loads((work / "story.json").read_text())
        print(f"targeted look at story questions: {len(st.get('segments', []))}", file=sys.stderr)
        look(select_probes(st.get("segments", []), a.max_probes, st.get("questions")), "probe")
        done("probe")
    if step("story2"):
        story()
        done("story2")
    # 4) script from text only → review → targeted look at the findings → script again
    if step("script"):
        cmd_script(argparse.Namespace(work=a.o, api=True, model=a.model_main, extra=None))
        script_ok()
        done("script")
    if step("review"):
        cmd_review(argparse.Namespace(work=a.o, script=str(work / "script.json"), api=True, model=a.model_main))
        (work / "review_round1.json").write_text((work / "review.json").read_text())
        done("review")
    rev = json.loads((work / "review_round1.json").read_text())
    if rev.get("segments") or rev.get("findings"):
        if step("recheck"):
            cmd_check(argparse.Namespace(work=a.o, script=str(work / "script.json")))
            targets = list(rev.get("segments", []))
            if (work / "uncertain_segments.json").exists():
                targets += json.loads((work / "uncertain_segments.json").read_text())["segments"]
            print(f"targeted look at review findings: {len(targets)}", file=sys.stderr)
            look(select_probes(targets, a.max_probes, rev.get("findings")), "recheck")
            done("recheck")
        if step("script2"):
            if not (work / "script_round1.json").exists():
                (work / "script_round1.json").write_text((work / "script.json").read_text())
            cmd_script(argparse.Namespace(
                work=a.o, api=True, model=a.model_main,
                extra="\n\nPREVIOUS SCRIPT:\n" + (work / "script_round1.json").read_text()
                + "\n\nREVIEW FINDINGS (fix them; answers are in the LOG, source \"recheck\"):\n"
                + json.dumps(rev.get("findings", []), ensure_ascii=False)))
            script_ok()
            done("script2")
        if step("review2"):
            cmd_review(argparse.Namespace(work=a.o, script=str(work / "script.json"), api=True, model=a.model_main))
            done("review2")
    cmd_check(argparse.Namespace(work=a.o, script=str(work / "script.json")))
    if not a.no_audio and step("audio"):
        cmd_audio(argparse.Namespace(work=a.o, script=str(work / "script.json"), voice=a.voice, tempo=a.tempo))
        done("audio")
    (work / "usage.json").write_text(json.dumps(USAGE, indent=1))
    counts = {}
    for k in ("review_round1.json", "review.json"):
        if (work / k).exists():
            n = json.loads((work / k).read_text()).get("findings", [])
            counts[k] = [sum(1 for x in n if x.get("importance") == d) for d in (1, 2, 3)]
    print(f"Model calls: {len(USAGE)}, tokens input {sum(x['input'] for x in USAGE)}, "
          f"output {sum(x['output'] for x in USAGE)} → {work}/usage.json")
    print("Review findings [importance 1, 2, 3]: " + ", ".join(f"{k}: {v}" for k, v in counts.items()))


# ---------------------------------------------------------------- CLI


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("source", help="YouTube URL or path to your own video")
        p.add_argument("-o", required=True, help="work folder")
        p.add_argument("--language", default="sk", help="ISO 639-1 code of the description language (sk, cs, en, de…)")
        p.add_argument("--threshold", type=float, default=0.1, help="ffmpeg scdet threshold 0–1 (only without PySceneDetect)")
        p.add_argument("--detector", default="content", choices=["content", "histogram", "adaptive"])
        p.add_argument("--no-vad", action="store_true", help="pauses from subtitles only, without Silero VAD")
        p.add_argument("--long-step", type=float, default=4.0, help="step in long scenes (s)")

    common(p := sp.add_parser("prepare")); p.set_defaults(f=cmd_prepare)
    p = sp.add_parser("detail"); p.add_argument("work"); p.add_argument("segments"); p.set_defaults(f=cmd_detail)
    p.add_argument("--prefix", default="detail", help="e.g. \"probe\" or \"recheck\" for a targeted pass")
    p = sp.add_parser("check"); p.add_argument("work"); p.add_argument("script"); p.set_defaults(f=cmd_check)
    p = sp.add_parser("perceive"); p.add_argument("work"); p.set_defaults(f=cmd_perceive)
    p.add_argument("--grids", default="overview", help="overview | probe | recheck | detail (prefix from `detail`)")
    p.add_argument("--api", action="store_true"); p.add_argument("--model", default=os.environ.get("MODEL_PERCEPTION", "claude-sonnet-5-5"))
    p = sp.add_parser("log-add"); p.add_argument("work"); p.add_argument("file"); p.set_defaults(f=cmd_log_add)
    p.add_argument("--source", default="overview")
    p = sp.add_parser("story"); p.add_argument("work"); p.set_defaults(f=cmd_story)
    p.add_argument("--api", action="store_true"); p.add_argument("--model", default=os.environ.get("MODEL_MAIN", "claude-sonnet-5-5"))
    p = sp.add_parser("script"); p.add_argument("work"); p.set_defaults(f=cmd_script, extra=None)
    p.add_argument("--api", action="store_true"); p.add_argument("--model", default=os.environ.get("MODEL_MAIN", "claude-sonnet-5-5"))
    p = sp.add_parser("frame"); p.add_argument("work"); p.add_argument("times", nargs="+"); p.set_defaults(f=cmd_frame)
    p = sp.add_parser("review"); p.add_argument("work"); p.add_argument("script"); p.set_defaults(f=cmd_review)
    p.add_argument("--api", action="store_true", help="the review is done by a model via the Anthropic API")
    p.add_argument("--model", default=os.environ.get("MODEL_MAIN", "claude-sonnet-5-5"))
    for name, f in (("audio", cmd_audio),):
        p = sp.add_parser(name); p.add_argument("work"); p.add_argument("script"); p.set_defaults(f=f)
        p.add_argument("--voice", default=None, help="piper voice (default by the manifest language, e.g. sk_SK-lili-medium)")
        p.add_argument("--tempo", type=float, default=1.0)
    common(p := sp.add_parser("auto")); p.set_defaults(f=cmd_auto)
    p.add_argument("--reuse-prep", action="store_true", help="do not download again – use the existing work folder")
    p.add_argument("--no-audio", action="store_true")
    p.add_argument("--max-probes", type=int, default=10, help="at most this many segments per targeted look")
    p.add_argument("--resume", action="store_true", help="after an interruption (subscription limit) skip finished steps")
    p.add_argument("--model-perception", default=os.environ.get("MODEL_PERCEPTION", "claude-sonnet-5-5"),
                   help="perception of the overview grids – the basis of all further layers (cheaper: claude-haiku-5-5)")
    p.add_argument("--model-main", default=os.environ.get("MODEL_MAIN", "claude-sonnet-5-5"))
    p.add_argument("--voice", default=None, help="piper voice (default by --language)")
    p.add_argument("--tempo", type=float, default=1.0)
    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
