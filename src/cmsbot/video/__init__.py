"""Short vertical video: a storyboard, a brand kit and whatever footage exists in; a captioned
1080x1920 MP4, a contact sheet and a check report out.

Footage is the plate; every word is drawn by code on top. A shot with no footage becomes a branded
card, so a script can be previewed as video before any clip is generated or recorded.

Storyboard (the body of a `short_video` item):
  {"hook": "...", "beats": [{"narration": "...", "overlay_text": "...", "visual": "..."}],
   "ending": "...", "caption": "...", "on_screen_source": "..."}
Shots are numbered hook = 1, then the beats, then the ending. Assets are matched by role:
  shot-01 ... shot-NN   a video clip or still image for that shot
  voiceover             recorded narration (sets the timing)
  timing                JSON {"words": [{"word", "start", "end"}]} for exact caption timing
  music                 optional bed, mixed low under the voice
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from cmsbot import carousel
from cmsbot.carousel import esc

WIDTH, HEIGHT, FPS = 1080, 1920, 30
WORDS_PER_SECOND = 2.6
MIN_SHOT_SECONDS = 1.6
SILENT_SHOT_SECONDS = 2.5
CAPTION_MAX_WORDS, CAPTION_MAX_CHARS = 5, 30
CAPTION_FONT_PX, OVERLAY_FONT_PX = 64, 76
MIN_LEGIBLE_PX = 36
MIN_SECONDS, MAX_SECONDS = 15, 90
MUSIC_VOLUME = 0.12
VIDEO_SUFFIXES = {".mp4", ".mov", ".webm", ".m4v", ".mkv"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}


class VideoError(RuntimeError):
    pass


def can_render() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe") and carousel.can_render())


def _words(text: str) -> list[str]:
    return re.findall(r"\S+", carousel.strip_gap_notes(text or ""))


def normalize(body: dict[str, Any]) -> list[dict[str, Any]]:
    """Storyboard -> ordered shots. Accepts `shots` directly, or hook + beats + ending."""
    raw = body.get("shots")
    if not isinstance(raw, list) or not raw:
        raw = []
        if body.get("hook"):
            raw.append({"narration": body["hook"], "overlay_text": body.get("hook_overlay") or "", "kind": "hook"})
        raw += [b for b in body.get("beats") or [] if isinstance(b, dict)]
        if body.get("ending"):
            raw.append({"narration": body["ending"], "overlay_text": "", "kind": "ending"})
    shots = []
    for number, shot in enumerate(raw, start=1):
        shots.append({
            "number": number, "role": f"shot-{number:02d}", "kind": shot.get("kind") or "beat",
            "narration": carousel.strip_gap_notes(str(shot.get("narration") or "")),
            "overlay_text": carousel.strip_gap_notes(str(shot.get("overlay_text") or "")),
            "visual": str(shot.get("visual") or ""),
            "seconds": float(shot["seconds"]) if isinstance(shot.get("seconds"), (int, float)) else None,
        })
        # A hook with no separate on-screen line is shown large, so it is not captioned a second time.
        shots[-1]["captioned"] = not (shots[-1]["kind"] == "hook" and not shots[-1]["overlay_text"])
    if not shots:
        raise VideoError("The storyboard has no hook, beats or ending.")
    return shots


def storyboard_hash(body: dict[str, Any], asset_refs: dict[str, str] | None = None) -> str:
    """Identifies the words and footage a render was made from, so a stale video is detectable."""
    try:
        shots = [(s["narration"], s["overlay_text"], s["seconds"]) for s in normalize(body)]
    except VideoError:
        shots = []
    payload = [shots, body.get("on_screen_source") or "", body.get("theme") or "", sorted((asset_refs or {}).items())]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def plan_timing(shots: list[dict[str, Any]], *, audio_seconds: float | None = None,
                words: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Give every shot a start and end, and every spoken word a time.

    measured    word timestamps from the voiceover line up with the script
    proportional the voiceover's length is shared out by word count
    estimated   no voiceover: a normal speaking rate is assumed
    """
    counts = [len(_words(s["narration"])) for s in shots]
    spoken = sum(counts)
    notes: list[str] = []
    stamps = [w for w in (words or []) if isinstance(w, dict) and "start" in w and "end" in w]
    if stamps and spoken and abs(len(stamps) - spoken) / spoken > 0.15:
        notes.append(f"The timing file has {len(stamps)} words but the script has {spoken}; timing was shared out by word count instead.")
        stamps = []
    timed: list[dict[str, Any]] = []
    if stamps:
        source, cursor = "measured", 0
        firsts = []
        for count in counts:
            firsts.append(float(stamps[min(cursor, len(stamps) - 1)]["start"]) if count else None)
            cursor += count
        total = audio_seconds or float(stamps[-1]["end"]) + 0.4
        starts: list[float] = []
        for index, first in enumerate(firsts):
            if index == 0:
                starts.append(0.0)
            elif first is not None:
                starts.append(max(first - 0.08, starts[-1] + 0.2))
            else:
                starts.append(starts[-1] + SILENT_SHOT_SECONDS)
        cursor = 0
        for index, shot in enumerate(shots):
            end = starts[index + 1] if index + 1 < len(shots) else max(total, starts[index] + 0.5)
            shot_words = []
            for word in _words(shot["narration"]):
                stamp = stamps[min(cursor, len(stamps) - 1)]
                shot_words.append({"word": word, "start": float(stamp["start"]), "end": float(stamp["end"])})
                cursor += 1
            timed.append({**shot, "start": round(starts[index], 3), "end": round(end, 3), "words": shot_words})
    else:
        if audio_seconds:
            source = "proportional"
            weights = [max(count, 3) if count else 3 for count in counts]
            durations = [audio_seconds * weight / sum(weights) for weight in weights]
        else:
            source = "estimated"
            durations = [shot["seconds"] or (max(MIN_SHOT_SECONDS, count / WORDS_PER_SECOND + 0.4) if count else SILENT_SHOT_SECONDS)
                         for shot, count in zip(shots, counts)]
        clock = 0.0
        for shot, duration in zip(shots, durations):
            tokens = _words(shot["narration"])
            lead, span = 0.08, max(duration - 0.2, 0.2)
            sizes = [len(t) + 1 for t in tokens]
            shot_words, at = [], clock + lead
            for token, size in zip(tokens, sizes):
                length = span * size / sum(sizes)
                shot_words.append({"word": token, "start": round(at, 3), "end": round(at + length, 3)})
                at += length
            timed.append({**shot, "start": round(clock, 3), "end": round(clock + duration, 3), "words": shot_words})
            clock += duration
    return {"source": source, "shots": timed, "seconds": timed[-1]["end"], "notes": notes}


def caption_chunks(timed_shots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Captions of a few words each, on screen from their first word to their last. Never spans a cut."""
    chunks = []
    for shot in timed_shots:
        if not shot.get("captioned", True):
            continue
        group: list[dict[str, Any]] = []
        for index, word in enumerate(shot["words"]):
            text = " ".join(w["word"] for w in group)
            too_long = group and (len(group) >= CAPTION_MAX_WORDS or len(text) + 1 + len(word["word"]) > CAPTION_MAX_CHARS)
            sentence_end = group and re.search(r"[.!?:]$", group[-1]["word"])
            if too_long or sentence_end:
                chunks.append(group)
                group = []
            group.append(word)
            if index == len(shot["words"]) - 1:
                chunks.append(group)
                group = []
        for position, group in enumerate(chunks):
            if isinstance(group, list):
                chunks[position] = {"text": " ".join(w["word"] for w in group), "start": group[0]["start"],
                                    "end": min(group[-1]["end"] + 0.12, shot["end"]), "shot": shot["number"],
                                    "cut": shot["end"]}
    for current, following in zip(chunks, chunks[1:]):  # hold a caption until the next one, without overlap
        if 0 < following["start"] - current["end"] < 0.35 or following["start"] < current["end"]:
            current["end"] = min(following["start"], current["cut"])
    for chunk in chunks:
        del chunk["cut"]
    return chunks


# ---- overlay HTML ---------------------------------------------------------------------------

_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:1080px;height:1920px;background:transparent;-webkit-font-smoothing:antialiased}
.frame{position:relative;width:1080px;height:1920px;overflow:hidden;color:var(--ink);font-family:var(--font-body)}
.card{background:radial-gradient(ellipse 90% 45% at 50% 34%,var(--glow) 0%,transparent 72%),
  linear-gradient(180deg,color-mix(in srgb,var(--base) 82%,var(--glow)) 0%,var(--base) 100%)}
.scrim{background:linear-gradient(180deg,color-mix(in srgb,var(--base) 55%,transparent) 0%,transparent 22%,
  transparent 46%,color-mix(in srgb,var(--base) 78%,transparent) 78%,color-mix(in srgb,var(--base) 88%,transparent) 100%)}
/* Keep clear of platform UI: top 220px, bottom 420px, right 150px. */
.brand{position:absolute;top:232px;left:84px;font-family:var(--font-label);font-size:26px;letter-spacing:.22em;
  text-transform:uppercase;display:flex;align-items:center;gap:14px}
.brand img{height:40px}
.overlay{position:absolute;left:84px;right:170px;top:520px}
.overlay .text{font-family:var(--font-headline);font-size:@OVERLAY@px;line-height:1.08;letter-spacing:-.01em;text-wrap:balance;
  text-shadow:0 2px 24px color-mix(in srgb,var(--base) 80%,transparent)}
.overlay.hook .text{font-size:@HOOK@px}
.overlay .rule{width:96px;height:6px;background:var(--accent);margin-bottom:34px;border-radius:3px}
.source{position:absolute;left:84px;right:170px;bottom:452px;font-family:var(--font-label);font-size:@SMALL@px;letter-spacing:.04em;
  color:var(--muted)}
.caption{position:absolute;left:84px;right:170px;bottom:610px;display:flex}
.caption span{font-weight:700;font-size:@CAPTION@px;line-height:1.16;text-wrap:balance;padding:14px 26px;border-radius:18px;
  background:color-mix(in srgb,var(--base) 84%,transparent);box-decoration-break:clone;-webkit-box-decoration-break:clone}
"""
for _token, _px in (("OVERLAY", OVERLAY_FONT_PX), ("HOOK", OVERLAY_FONT_PX + 20), ("CAPTION", CAPTION_FONT_PX), ("SMALL", MIN_LEGIBLE_PX)):
    _CSS = _CSS.replace(f"@{_token}@", str(_px))


def _document(theme: dict[str, Any], classes: str, inner: str) -> str:
    variables = ";".join([*(f"--{k}:{v}" for k, v in theme["colors"].items()),
                          *(f"--font-{k}:{v}" for k, v in theme["fonts"].items())])
    return (f'<!DOCTYPE html><html><head><meta charset="utf-8"><link href="{esc(theme["font_css"])}" rel="stylesheet">'
            f"<style>:root{{{variables}}}{_CSS}</style></head><body><div class=\"frame {classes}\">{inner}</div></body></html>")


def overlay_html(shot: dict[str, Any], theme: dict[str, Any], *, source: str = "", card: bool = False) -> str:
    """Everything fixed for one shot: wordmark, the on-screen line, the source. Opaque card if there is no footage."""
    logo = f'<img src="{esc(theme["logo"])}" alt="">' if theme["logo"] else ""
    brand = f'<div class="brand">{logo}{esc(theme["wordmark"])}</div>' if theme["wordmark"] or logo else ""
    text = shot["overlay_text"] or (shot["narration"] if shot["kind"] == "hook" else "")
    block = (f'<div class="overlay {esc(shot["kind"])}"><div class="rule"></div><div class="text">{esc(text)}</div></div>'
             if text else "")
    foot = f'<div class="source">{esc(source)}</div>' if source else ""
    return _document(theme, "card" if card else "scrim", brand + block + foot)


def caption_html(text: str, theme: dict[str, Any]) -> str:
    return _document(theme, "", f'<div class="caption"><span>{esc(text)}</span></div>')


# ---- ffmpeg ---------------------------------------------------------------------------------

def _run(command: list[str], what: str) -> None:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise VideoError(f"ffmpeg failed while {what}: {result.stderr.strip()[-600:]}")


def probe_seconds(path: Path) -> float:
    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                            capture_output=True, text=True)
    try:
        return float(result.stdout.strip())
    except ValueError as exc:
        raise VideoError(f"Could not read the length of {path.name}.") from exc


def probe_video(path: Path) -> dict[str, Any]:
    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height", "-show_entries",
                             "format=duration", "-of", "json", str(path)], capture_output=True, text=True)
    data = json.loads(result.stdout or "{}")
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    return {"width": video.get("width"), "height": video.get("height"),
            "has_audio": any(s.get("codec_type") == "audio" for s in data.get("streams", [])),
            "seconds": round(float((data.get("format") or {}).get("duration") or 0), 2)}


def find_assets(folder: Path) -> dict[str, Path]:
    """shot-01.mp4, shot-02.png, voiceover.m4a, music.mp3, timing.json in a folder -> {role: path}."""
    found: dict[str, Path] = {}
    for path in sorted(Path(folder).iterdir()) if Path(folder).is_dir() else []:
        stem, suffix = path.stem.lower(), path.suffix.lower()
        if re.fullmatch(r"shot-\d{2}", stem) and suffix in VIDEO_SUFFIXES | IMAGE_SUFFIXES:
            found.setdefault(stem, path)
        elif stem in ("voiceover", "music") and suffix in AUDIO_SUFFIXES:
            found.setdefault(stem, path)
        elif stem == "timing" and suffix == ".json":
            found["timing"] = path
    return found


_ENCODE = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS), "-an"]
_FIT = f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,crop={WIDTH}:{HEIGHT},setsar=1"


def _segment(shot: dict[str, Any], footage: Path | None, overlay: Path, out: Path) -> None:
    seconds = f"{shot['end'] - shot['start']:.3f}"
    frames = max(1, round((shot["end"] - shot["start"]) * FPS))
    if footage is None:  # the overlay is an opaque branded card
        command = ["ffmpeg", "-y", "-loop", "1", "-framerate", str(FPS), "-t", seconds, "-i", str(overlay),
                   "-vf", f"{_FIT},format=yuv420p", *_ENCODE, "-t", seconds, str(out)]
    elif footage.suffix.lower() in IMAGE_SUFFIXES:  # slow push-in so a still does not read as a slide
        zoom = (f"scale={WIDTH * 2}:{HEIGHT * 2}:force_original_aspect_ratio=increase,crop={WIDTH * 2}:{HEIGHT * 2},"
                f"zoompan=z='1+0.08*on/{frames}':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={WIDTH}x{HEIGHT}:fps={FPS},setsar=1")
        command = ["ffmpeg", "-y", "-loop", "1", "-framerate", str(FPS), "-t", seconds, "-i", str(footage), "-i", str(overlay),
                   "-filter_complex", f"[0:v]{zoom}[bg];[bg][1:v]overlay=0:0[v]", "-map", "[v]", *_ENCODE, "-t", seconds, str(out)]
    else:
        command = ["ffmpeg", "-y", "-stream_loop", "-1", "-i", str(footage), "-i", str(overlay),
                   "-filter_complex", f"[0:v]{_FIT},fps={FPS}[bg];[bg][1:v]overlay=0:0[v]", "-map", "[v]", *_ENCODE,
                   "-t", seconds, str(out)]
    _run(command, f"building shot {shot['number']}")


def render(body: dict[str, Any], brand: dict[str, Any] | None, out_dir: Path, *, assets: dict[str, Path] | None = None,
           asset_refs: dict[str, str] | None = None) -> dict[str, Any]:
    """Writes video.mp4, contact-sheet.jpg and render.json to out_dir and returns the report."""
    if not can_render():
        raise VideoError("Rendering needs ffmpeg, ffprobe and a headless Chrome on this machine.")
    chrome = carousel.find_chrome() or ""
    assets = dict(assets or {})
    out_dir = Path(out_dir)
    work = out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)

    shots = normalize(body)
    voiceover = assets.get("voiceover")
    words = None
    if assets.get("timing"):
        try:
            words = json.loads(Path(assets["timing"]).read_text(encoding="utf-8")).get("words")
        except (ValueError, AttributeError) as exc:
            raise VideoError("timing.json must be {\"words\": [{\"word\", \"start\", \"end\"}]}.") from exc
    timing = plan_timing(shots, audio_seconds=probe_seconds(voiceover) if voiceover else None, words=words)
    captions = caption_chunks(timing["shots"])
    theme = carousel.resolve_theme(brand, body.get("theme") if isinstance(body.get("theme"), str) else None)
    source = str(body.get("on_screen_source") or "")

    jobs: list[tuple[Path, Path, bool]] = []  # (html, png, transparent)
    for shot in timing["shots"]:
        card = shot["role"] not in assets
        page = work / f"overlay-{shot['number']:02d}.html"
        page.write_text(overlay_html(shot, theme, source=source, card=card), encoding="utf-8")
        jobs.append((page, page.with_suffix(".png"), not card))
    for index, chunk in enumerate(captions):
        page = work / f"caption-{index:03d}.html"
        page.write_text(caption_html(chunk["text"], theme), encoding="utf-8")
        jobs.append((page, page.with_suffix(".png"), True))
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda job: carousel.screenshot(chrome, job[0], job[1], width=WIDTH, height=HEIGHT, transparent=job[2]), jobs))

    segments = []
    for shot in timing["shots"]:
        segment = work / f"segment-{shot['number']:02d}.mp4"
        _segment(shot, assets.get(shot["role"]), work / f"overlay-{shot['number']:02d}.png", segment)
        segments.append(segment)
    listing = work / "segments.txt"
    listing.write_text("".join(f"file '{s.resolve()}'\n" for s in segments), encoding="utf-8")
    silent = work / "silent.mp4"
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(silent)], "joining shots")

    total = f"{timing['seconds']:.3f}"
    command = ["ffmpeg", "-y", "-i", str(silent)]
    for index in range(len(captions)):
        command += ["-i", str(work / f"caption-{index:03d}.png")]
    audio_inputs = []
    for role in ("voiceover", "music"):
        if assets.get(role):
            audio_inputs.append((role, len(captions) + 1 + len(audio_inputs)))
            command += ["-i", str(assets[role])] if role == "voiceover" else ["-stream_loop", "-1", "-i", str(assets[role])]
    if not audio_inputs:  # platforms reject files with no audio track
        command += ["-f", "lavfi", "-t", total, "-i", "anullsrc=r=44100:cl=stereo"]
    graph, last = [], "0:v"
    for index, chunk in enumerate(captions):
        graph.append(f"[{last}][{index + 1}:v]overlay=0:0:enable='between(t,{chunk['start']:.3f},{chunk['end']:.3f})'[v{index}]")
        last = f"v{index}"
    if not graph:
        graph.append("[0:v]null[vout]")
        last = "vout"
    if len(audio_inputs) == 2:
        graph.append(f"[{audio_inputs[1][1]}:a]volume={MUSIC_VOLUME}[bed];[{audio_inputs[0][1]}:a][bed]amix=inputs=2:duration=first:normalize=0[aout]")
        audio_map = "[aout]"
    elif audio_inputs:
        graph.append(f"[{audio_inputs[0][1]}:a]volume={1.0 if audio_inputs[0][0] == 'voiceover' else 0.6}[aout]")
        audio_map = "[aout]"
    else:
        audio_map = f"{len(captions) + 1}:a"
    final = out_dir / "video.mp4"
    command += ["-filter_complex", ";".join(graph), "-map", f"[{last}]", "-map", audio_map, "-c:v", "libx264", "-preset", "medium",
                "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS), "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
                "-t", total, str(final)]
    _run(command, "adding captions and sound")

    sheet = out_dir / "contact-sheet.jpg"
    columns = 6
    rows = max(1, -(-int(timing["seconds"] // 2 + 1) // columns))
    _run(["ffmpeg", "-y", "-i", str(final), "-vf", f"fps=1/2,scale=216:384,tile={columns}x{rows}", "-frames:v", "1", "-q:v", "4",
          str(sheet)], "making the contact sheet")

    report = {
        "hash": storyboard_hash(body, asset_refs), "seconds": round(timing["seconds"], 2), "timing": timing["source"],
        "shots": [{"number": s["number"], "start": s["start"], "end": s["end"], "footage": s["role"] in assets,
                   "overlay_text": s["overlay_text"]} for s in timing["shots"]],
        "captions": captions, "file": probe_video(final),
        "checks": check(timing, captions, assets, probe_video(final)),
    }
    (out_dir / "render.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    shutil.rmtree(work, ignore_errors=True)
    return {**report, "video": final, "contact_sheet": sheet}


def check(timing: dict[str, Any], captions: list[dict[str, Any]], assets: dict[str, Path], file: dict[str, Any]) -> list[dict[str, Any]]:
    """Things a reviewer should know before watching. `ok: False` items are problems, not style notes."""
    shots = timing["shots"]
    missing = [s["number"] for s in shots if s["role"] not in assets]
    spoken = sum(len(s["words"]) for s in shots if s.get("captioned", True))
    captioned = sum(len(c["text"].split()) for c in captions)
    long_overlays = [s["number"] for s in shots if len(s["overlay_text"].split()) > 10]
    rushed = [s["number"] for s in shots if s["words"] and len(s["words"]) / max(s["end"] - s["start"], 0.1) > 3.6]
    out = [
        {"check": "size", "ok": (file["width"], file["height"]) == (WIDTH, HEIGHT), "detail": f"{file['width']}x{file['height']}"},
        {"check": "length", "ok": MIN_SECONDS <= timing["seconds"] <= MAX_SECONDS, "detail": f"{timing['seconds']:.1f}s"},
        {"check": "sound", "ok": "voiceover" in assets,
         "detail": "voiceover" if "voiceover" in assets else "No voiceover: silent preview, timing is assumed."},
        {"check": "caption timing", "ok": timing["source"] == "measured",
         "detail": {"measured": "Captions follow word timestamps.",
                    "proportional": "Captions are spread across the voiceover by word count; add timing.json for exact sync.",
                    "estimated": "No voiceover; captions assume a normal speaking rate."}[timing["source"]]},
        {"check": "every word captioned", "ok": spoken == captioned, "detail": f"{captioned} of {spoken} words"},
        {"check": "footage", "ok": not missing,
         "detail": "Every shot has footage." if not missing else f"Shots {', '.join(map(str, missing))} have no footage and show a branded card."},
        {"check": "on-screen text length", "ok": not long_overlays,
         "detail": "ok" if not long_overlays else f"Shots {', '.join(map(str, long_overlays))} have more than 10 words on screen."},
        {"check": "pace", "ok": not rushed,
         "detail": "ok" if not rushed else f"Shots {', '.join(map(str, rushed))} are spoken faster than 3.6 words a second."},
    ]
    out += [{"check": "timing file", "ok": False, "detail": note} for note in timing["notes"]]
    return out
