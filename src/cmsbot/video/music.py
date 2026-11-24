"""A calm instrumental music bed made in code: warm pads, a soft felt-piano line, a sub bass and, if
asked, a very light pulse, in a small synthetic room. No provider, no key, no cost, no licence question.

It is built to sit under narration (the renderer mixes it at MUSIC_VOLUME), not to stand on its own.
The prompt steers it with plain words: a tempo ("70-80 BPM"), the mood ("optimistic", "melancholic"),
what to include or leave out ("piano", "pads", "no drums", "light pulse"), and colour ("warm",
"bright"). The same prompt always gives the same key, chords and sounds, so every video on an account
that shares one prompt shares one sound; only the length changes.
"""

from __future__ import annotations

import hashlib
import io
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy import signal
from scipy.io import wavfile

RATE = 44100

# Chords as scale degrees (0 = the key's first degree), chosen to loop calmly.
MAJOR_PROGRESSIONS = ([0, 4, 5, 3], [0, 5, 3, 4], [3, 0, 4, 5], [0, 2, 3, 3], [5, 3, 0, 4], [0, 3, 5, 4])
MINOR_PROGRESSIONS = ([0, 5, 2, 6], [0, 3, 5, 4], [0, 6, 5, 6], [5, 3, 0, 4])
MAJOR_SCALE = (0, 2, 4, 5, 7, 9, 11)
MINOR_SCALE = (0, 2, 3, 5, 7, 8, 10)


@dataclass(frozen=True)
class Style:
    bpm: float
    minor: bool
    piano: bool
    pads: bool
    pulse: float        # 0 = none; otherwise the pulse's level
    cutoff: float       # pad low-pass, Hz: lower is warmer
    key: int            # semitones above C
    progression: tuple[int, ...]
    seed: int

    def describe(self) -> str:
        names = ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")
        parts = [f"{names[self.key]} {'minor' if self.minor else 'major'}", f"{self.bpm:g} BPM",
                 *(["pads"] if self.pads else []), *(["felt piano"] if self.piano else []),
                 "light pulse" if 0 < self.pulse < 0.3 else "pulse" if self.pulse else "no drums"]
        return ", ".join(parts)


def style_from_prompt(prompt: str) -> Style:
    text = prompt.lower()
    seed = int.from_bytes(hashlib.sha256(text.strip().encode("utf-8")).digest()[:4], "big")
    rng = np.random.default_rng(seed)
    tempo = re.search(r"(\d{2,3})\s*(?:-|–|to)\s*(\d{2,3})\s*bpm|(\d{2,3})\s*bpm", text)
    if tempo:
        bpm = (float(tempo.group(1)) + float(tempo.group(2))) / 2 if tempo.group(1) else float(tempo.group(3))
    else:
        bpm = 96.0 if re.search(r"\b(upbeat|energetic|driving|fast)\b", text) else 74.0
    bpm = float(min(max(bpm, 50.0), 140.0))
    minor = bool(re.search(r"\b(minor|melanchol\w*|sad|dark|somber|sombre|moody|tense)\b", text))
    no_drums = bool(re.search(r"\bno (drums|percussion|beat)\b", text))
    light = bool(re.search(r"\b(light|soft|gentle|subtle) (pulse|beat|kick|percussion)\b", text))
    pulse = 0.18 if light else (0.0 if no_drums or not re.search(r"\b(pulse|beat|drums|percussion|kick)\b", text) else 0.35)
    piano = "piano" in text or "keys" in text or not re.search(r"\b(pad|synth|drone)\w*\b", text)
    pads = bool(re.search(r"\b(pad|synth|ambient|drone|atmospher\w*)\b", text)) or not piano
    cutoff = 1400.0 if "warm" in text or "analog" in text else 2600.0 if re.search(r"\b(bright|airy|shimmer\w*)\b", text) else 1900.0
    progressions = MINOR_PROGRESSIONS if minor else MAJOR_PROGRESSIONS
    progression = tuple(progressions[int(rng.integers(len(progressions)))])
    key = int(rng.integers(12))
    return Style(bpm, minor, piano, pads, pulse, cutoff, key, progression, seed)


def _midi_hz(note: float) -> float:
    return 440.0 * 2 ** ((note - 69) / 12)


def _chord(style: Style, degree: int) -> list[int]:
    """A soft seventh chord on a scale degree, voiced from the octave below middle C up, plus its root an octave down."""
    scale = MINOR_SCALE if style.minor else MAJOR_SCALE
    base = 52 + (style.key % 12)   # the key's root between E3 and D#4
    if base > 58:
        base -= 12

    def tone(step: int) -> int:
        return base + scale[(degree + step) % 7] + 12 * ((degree + step) // 7)

    return [tone(0) - 12, tone(0), tone(2), tone(4), tone(6), tone(2) + 12]  # bass, root, 3rd, 5th, 7th, high 3rd


def _envelope(n: int, attack: float, release: float) -> np.ndarray:
    env = np.ones(n)
    a, r = min(int(attack * RATE), n // 2), min(int(release * RATE), n // 2)
    if a:
        env[:a] = np.sin(np.linspace(0, np.pi / 2, a)) ** 2
    if r:
        env[n - r:] = np.cos(np.linspace(0, np.pi / 2, r)) ** 2
    return env


def _pad_voice(freq: float, n: int, rng: np.random.Generator) -> np.ndarray:
    """Three slightly detuned saws with their top rolled off, breathing slowly: a warm analog pad."""
    t = np.arange(n) / RATE
    out = np.zeros(n)
    harmonics = max(1, min(10, int(4000 / freq)))
    for detune in (-0.08, 0.0, 0.07):
        f = freq * 2 ** (detune / 12)
        for h in range(1, harmonics + 1):
            out += np.sin(2 * np.pi * f * h * t + rng.uniform(0, 2 * np.pi)) / h ** 1.6
    breath = 1 + 0.18 * np.sin(2 * np.pi * rng.uniform(0.05, 0.11) * t + rng.uniform(0, 2 * np.pi))
    return out / 3 * breath


def _piano_note(freq: float, seconds: float, velocity: float) -> np.ndarray:
    """A felt piano: soft attack, a few slightly stretched partials, the upper ones dying first."""
    n = int(seconds * RATE)
    t = np.arange(n) / RATE
    out = np.zeros(n)
    for h, amp in ((1, 1.0), (2, 0.38), (3, 0.14), (4, 0.07), (5, 0.03)):
        partial = freq * h * (1 + 0.0004 * h * h)
        if partial > 9000:
            break
        out += amp * np.sin(2 * np.pi * partial * t) * np.exp(-t * (1.6 + 1.1 * h))
    attack = int(0.012 * RATE)
    out[:attack] *= np.linspace(0, 1, attack)
    thump = np.exp(-t * 60) * np.sin(2 * np.pi * freq * 0.5 * t) * 0.15  # the felt hammer
    return (out + thump) * velocity


def _kick(level: float) -> np.ndarray:
    n = int(0.35 * RATE)
    t = np.arange(n) / RATE
    pitch = 48 + 40 * np.exp(-t * 30)
    return np.sin(2 * np.pi * np.cumsum(pitch) / RATE) * np.exp(-t * 11) * level


def _room(n_seconds: float, rng: np.random.Generator) -> np.ndarray:
    """A stereo impulse response: decaying, darkening noise, a little different per side."""
    n = int(n_seconds * RATE)
    t = np.arange(n) / RATE
    sos = signal.butter(2, 4500, "low", fs=RATE, output="sos")
    sides = [signal.sosfilt(sos, rng.standard_normal(n)) * np.exp(-t * 6.9 / n_seconds) for _ in range(2)]
    ir = np.stack(sides, axis=1)
    return ir / np.sqrt((ir ** 2).sum(axis=0))


def synthesize(prompt: str, seconds: float) -> np.ndarray:
    """Stereo float samples in [-1, 1] at RATE, `seconds` long."""
    style = style_from_prompt(prompt)
    rng = np.random.default_rng(style.seed)
    total = int(seconds * RATE)
    tail = int(3.0 * RATE)  # room for the last chord to ring into the reverb
    beat = 60.0 / style.bpm
    bar = 4 * beat
    chord_len = 2 * bar
    mix = np.zeros((total + tail, 2))

    chords = int(np.ceil(seconds / chord_len)) + 1
    pad_sos = signal.butter(2, style.cutoff, "low", fs=RATE, output="sos")
    piano_sos = signal.butter(2, 3200, "low", fs=RATE, output="sos")
    for index in range(chords):
        start = int(index * chord_len * RATE)
        if start >= total:
            break
        notes = _chord(style, style.progression[index % len(style.progression)])
        span = int((chord_len + 1.2) * RATE)  # overlap the next chord so changes are seamless
        end = min(start + span, total + tail)
        n = end - start
        env = _envelope(n, 1.4, 1.6)
        # sub bass: a pure low root
        t = np.arange(n) / RATE
        bass = np.sin(2 * np.pi * _midi_hz(notes[0]) * t) * env * 0.07
        mix[start:end] += bass[:, None]
        if style.pads:
            for i, note in enumerate(notes[1:]):
                voice = signal.sosfilt(pad_sos, _pad_voice(_midi_hz(note), n, rng)) * env * 0.06
                pan = (i / (len(notes) - 2)) * 0.6 - 0.3
                mix[start:end, 0] += voice * (0.5 - pan)
                mix[start:end, 1] += voice * (0.5 + pan)
        if style.piano:
            pool = notes[1:] + [notes[2] + 12, notes[3] + 12]
            steps = round(chord_len / beat) * 2  # eighth notes
            for step in range(steps):
                chance = 0.55 if step == 0 else 0.22 if step % 2 == 0 else 0.07
                if rng.random() > chance:
                    continue
                note = pool[int(rng.integers(len(pool)))] + (12 if rng.random() < 0.25 else 0)
                at = start + int(step * beat / 2 * RATE)
                if at >= total - int(0.5 * RATE):
                    continue
                tone = signal.sosfilt(piano_sos, _piano_note(_midi_hz(note), 3.5, rng.uniform(0.16, 0.26)))
                stop = min(at + len(tone), total + tail)
                pan = rng.uniform(-0.25, 0.25)
                mix[at:stop, 0] += tone[:stop - at] * (0.5 - pan)
                mix[at:stop, 1] += tone[:stop - at] * (0.5 + pan)
    if style.pulse:
        kick = _kick(style.pulse)
        beats = int(seconds / beat)
        for b in range(beats):
            if b % 2:  # beats 1 and 3 only: a heartbeat, not a groove
                continue
            at = int(b * beat * RATE)
            stop = min(at + len(kick), total + tail)
            mix[at:stop] += kick[:stop - at, None]

    room = _room(2.8, rng)
    wet = np.stack([signal.fftconvolve(mix[:, 0], room[:, 0])[: len(mix)],
                    signal.fftconvolve(mix[:, 1], room[:, 1])[: len(mix)]], axis=1)
    out = (mix * 0.72 + wet * 0.45)[:total]
    out *= _envelope(total, min(1.5, seconds / 4), min(2.5, seconds / 3))[:, None]
    peak = float(np.max(np.abs(out))) or 1.0
    return out / peak * 0.85


def render_mp3(prompt: str, seconds: float) -> bytes:
    """The bed as an MP3 (44.1 kHz, 128 kbit/s), the same format the music API returns."""
    samples = synthesize(prompt, seconds)
    pcm = io.BytesIO()
    wavfile.write(pcm, RATE, (samples * 32767).astype(np.int16))
    with tempfile.TemporaryDirectory() as tmp:
        source, target = Path(tmp) / "music.wav", Path(tmp) / "music.mp3"
        source.write_bytes(pcm.getvalue())
        done = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(source), "-c:a", "libmp3lame", "-b:a", "128k",
                               str(target)], check=False, capture_output=True, text=True)
        if done.returncode != 0:
            raise RuntimeError(f"ffmpeg could not encode the music: {done.stderr[-300:]}")
        return target.read_bytes()
