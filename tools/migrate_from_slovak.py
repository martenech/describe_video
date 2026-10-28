#!/usr/bin/env python3
"""One-off migration of work folders created by the old Slovak version (popis_videa.py)
to the English names used by describe_video.py: file and folder names, JSON keys and sentinel values.

Usage: python3 tools/migrate_from_slovak.py WORK_DIR [WORK_DIR ...]   (renames in place; back up first)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

FILES = {
    "prepis.json": "transcript.json", "prehlad.md": "overview.md", "ciel.md": "probe.md", "red.md": "recheck.md",
    "register.json": "characters.json", "pribeh.json": "story.json", "pribeh_vstup.md": "story_input.md",
    "scenar.json": "script.json", "scenar_vstup.md": "script_input.md", "scenar_kolo1.json": "script_round1.json",
    "scenar_stary.json": "script_old.json", "scenar.md": "script.md", "redakcia.json": "review.json",
    "redakcia_vstup.md": "review_input.md", "redakcia_kolo1.json": "review_round1.json",
    "redakcia_porovnanie.json": "review_comparison.json", "neiste_useky.json": "uncertain_segments.json",
    "ciel_useky.json": "probe_segments.json", "red_useky.json": "recheck_segments.json", "prechod1.json": "pass1.json",
    "auto_stav.json": "auto_state.json", "spotreba.json": "usage.json", "zvuk_info.json": "audio_info.json",
    "hovorena_kniha.mp3": "narration_only.mp3", "stopa_popisu.mp3": "description_track.mp3",
    "audiodeskripcia.mp3": "audio_description_mix.mp3", "video_s_audiodeskripciou.mp4": "video_with_ad.mp4",
    "video_s_audiodeskripciou_rozsirena.mp4": "video_with_ad_extended.mp4", "popis.vtt": "descriptions.vtt",
    "popis_rozsirena.vtt": "descriptions_extended.vtt",
    "vnimanie_vstup_prehlad.md": "perception_input_overview.md", "vnimanie_vstup_ciel.md": "perception_input_probe.md",
    "vnimanie_vstup_red.md": "perception_input_recheck.md",
}
DIRS = {"gridy": "grids", "snimky": "frames", "snimky_plne": "frames_full", "zvuk": "audio", "odpovede": "responses",
        "predtym": "previous"}
AUDIO_FILES = {"ticho.wav": "silence.wav", "zoznam.txt": "list.txt", "video_rozsirene.mp4": "video_extended.mp4",
               "rozsirena_mix.mp3": "extended_mix.mp3"}
PREFIXES = {"prehlad": "overview", "ciel": "probe", "red": "recheck"}  # grid/frame file prefixes and log sources

KEYS = {
    "dlzka": "duration", "sceny": "scenes", "druh": "kind", "medzery_v_reci": "speech_gaps", "rec": "speech",
    "prehlad": "overview", "ciel": "probe", "red": "recheck", "subor": "file", "hlavicka": "header", "bunky": "cells",
    "poradie": "index", "cas": "time", "cas_text": "time_text", "scena": "scene", "co_zistit": "what_to_find",
    "dolezitost": "importance", "skutocne_fps": "actual_fps", "gridy": "grids", "zvuk": "sound", "postavy": "characters",
    "meno": "name", "vzhlad": "appearance", "prvy_vyskyt": "first_seen", "dokaz_mena": "name_source",
    "istota": "confidence", "hlas": "voice", "od": "start", "do": "end", "miesto": "place", "nove_postavy": "new_characters",
    "akcia": "action", "objekty": "objects", "text_na_obraze": "onscreen_text", "zaber": "shot", "nejasne": "unclear",
    "zdroj": "source", "odpoved": "answer", "otazka": "question", "pritomni": "present", "dej": "events",
    "priebeh": "timeline", "posledny_stav": "last_state", "otazky": "questions", "nazov": "title",
    "vyslovnost": "pronunciation", "bloky": "blocks", "koniec": "end", "rozsirena": "extended", "cez_rec": "over_speech",
    "preco_neiste": "uncertain_why", "nalezy": "findings", "typ": "type", "blok": "block",
    "otazka_pre_video": "video_question", "usek": "range", "vstup": "input", "vystup": "output", "kola": "turns",
    "cena_api_odhad_usd": "api_cost_estimate_usd", "chyba": "error", "stav": "state", "oznacenie": "label",
    "dej_doteraz": "story_so_far", "otvorene_otazky": "open_questions", "popis_gridu": "grid_notes",
    "popis_useku": "segment_notes", "co": "what", "zaznamy": "records",
}
# audio_info.json has its own meaning for "miesto" (free room before the next speech)
AUDIO_INFO_KEYS = {"blok": "block", "trvanie": "duration", "miesto": "room", "pretok": "overflow", "zastavit": "freeze"}
VALUES = {"nie je": "absent", "nevidno": "not visible", "nejasné": "unclear", "nečitateľné": "illegible",
          "(spev)": "(singing)", "začiatok": "start", "strih": "cut", "stmievačka": "fade"}
STEPS = {"register": "characters", "vnimanie": "perception", "pribeh": "story", "ciel": "probe", "pribeh2": "story2",
         "scenar": "script", "redakcia": "review", "red": "recheck", "scenar2": "script2", "redakcia2": "review2",
         "zvuk": "audio"}


def new_prefix(name: str) -> str:
    """prehlad_01.jpg → overview_01.jpg, ciel03_02.jpg → probe03_02.jpg (detail stays)."""
    m = re.match(r"(prehlad|ciel|red)(?=[\d_]|$)", name)
    return PREFIXES[m.group(1)] + name[m.end():] if m else name


def convert(o, keys=KEYS, parent=None):
    if isinstance(o, dict):
        out = {}
        for k, v in o.items():
            # the "characters" dict in a log record maps character names to status – keep its keys
            nk = k if parent == "postavy_dict" else keys.get(k, k)
            out[nk] = convert(v, keys, "postavy_dict" if k == "postavy" and isinstance(v, dict) else None)
            if k == "zdroj" and isinstance(v, str):
                out[nk] = PREFIXES.get(v, v)
        return out
    if isinstance(o, list):
        return [convert(x, keys) for x in o]
    if isinstance(o, str):
        if o in VALUES:
            return VALUES[o]
        if re.match(r"(gridy|snimky)/", o):  # paths to grids in the manifest
            d, f = o.split("/", 1)
            return f"{DIRS[d]}/{new_prefix(f)}"
        return re.sub(r"^(prehlad|ciel\d+|red\d+) (\d+): (.*)snímok", lambda m: f"{new_prefix(m.group(1))} {m.group(2)}: {m.group(3)}frames", o)
    return o


def migrate(work: Path) -> None:
    for old, new in DIRS.items():
        if (work / old).is_dir():
            (work / old).rename(work / new)
    for d in ("grids", "frames"):
        for p in (work / d).glob("*") if (work / d).is_dir() else []:
            if new_prefix(p.name) != p.name:
                p.rename(p.with_name(new_prefix(p.name)))
    audio = work / "audio"
    if audio.is_dir():
        for p in audio.glob("blok_*.wav"):
            p.rename(p.with_name(p.name.replace("blok_", "block_")))
        for old, new in AUDIO_FILES.items():
            if (audio / old).exists():
                (audio / old).rename(audio / new)
        if (audio / "list.txt").exists():
            (audio / "list.txt").write_text((audio / "list.txt").read_text()
                                            .replace("blok_", "block_").replace("ticho.wav", "silence.wav"))
    for old, new in FILES.items():
        if (work / old).exists():
            (work / old).rename(work / new)
    for p in work.glob("*.json"):
        if p.name.endswith(".info.json") or p.name == "review_comparison.json":
            continue  # yt-dlp metadata; free-form test notes
        data = json.loads(p.read_text() or "null")
        if p.name == "auto_state.json":
            data = [STEPS.get(s, s) for s in data]
        elif p.name == "audio_info.json":
            data = convert(data, AUDIO_INFO_KEYS)
        else:
            data = convert(data)
        if p.name == "manifest.json":
            data.setdefault("language", "sk")
        p.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    if (work / "log.jsonl").exists():
        lines = [json.loads(l) for l in (work / "log.jsonl").read_text().splitlines() if l.strip()]
        (work / "log.jsonl").write_text("".join(json.dumps(convert(z), ensure_ascii=False) + "\n" for z in lines))
    print(f"migrated {work}")


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        migrate(Path(arg))
