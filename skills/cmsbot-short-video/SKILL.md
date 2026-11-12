---
name: cmsbot-short-video
description: Produce a 20-75 second vertical video (TikTok, Reels, Shorts) from an approved CMSBOT video script, with consistent look, generated b-roll, code-rendered text and captions. Use when a short_video item has an approved script and needs the actual video.
---

# Short video

Needs: a video model key (Seedance through Higgsfield or OpenRouter, or xAI Grok Imagine), an image
model key (Grok Imagine or Gemini), `ffmpeg`, and the `cmsbot-hub` tools. Voice is a recorded human
voiceover when the project provides one; otherwise say that the video is waiting on it.

The method is "footage is the plate; meaning is drawn on top". Generated clips carry mood and
motion. Every word, number, label and caption is rendered by code over them, never asked of the
video model.

## 1. Lock the audio first
Get the voiceover (or the approved narration text and its timing). Everything is timed to it.
Produce word-level timestamps and write `timing.json`. Do not storyboard before this exists.

## 2. Storyboard as data
Write `storyboard.json`: 5-15 shots. Each shot has `start`, `end`, `narration`, `overlay_text`,
`method` (`broll`, `screen_recording`, `diagram`, `still`), `prompt`, and `source` when it shows a
paper or page. One claim per video; the claim's source is on screen while it is spoken.

## 3. Consistency comes from fixed text, not luck
From the project's brand kit, write a `look.md` once per account and reuse it verbatim:
- an opener and a closer that go on every image prompt (medium, era, palette; "no text, no letters,
  no logos");
- a canon block for any recurring subject or presenter, in a long form (close and medium shots) and
  a short form (wide shots);
- one grade (palette and one accent colour) applied to every still before it becomes video.
Keep brand and product names out of image and video prompts; they produce garbled logos.

## 4. Stills, then motion
Generate a still per shot with the opener + shot + closer. Pick from a contact sheet. Then generate
motion from the chosen still as the first frame. Video prompts are blocked and timed: framing, the
one thing that happens, timestamped beats in clip time, then constraints ("locked camera", "one
continuous shot", "nothing else enters the frame", "no text"). Leave empty space where overlays go.

## 5. Spend discipline
Estimate cost before generating. Run a pilot of two or three shots and look at them before the full
run. Set a cap for the item; stop at the cap. One take per shot unless review flags it. Log each
paid call with `record_cost(category="video" | "image" | "audio")`.

## 6. Assemble: one command
Attach the files to the item by role, then render:

```bash
cmsbot hub upload <item_id> shot-02.mp4 --role shot-02
```

```bash
cmsbot hub upload <item_id> voiceover.m4a --role voiceover
```

```bash
cmsbot hub render <item_id>
```

(or the `render_video` tool). Roles: `shot-01` … `shot-NN` (hook is shot 1, then the beats, then the
ending), `voiceover`, `timing` (a JSON file of word timestamps, for exact caption sync), `music`.
The renderer fits footage to 1080x1920, draws the wordmark, on-screen lines, source and captions in
the brand's style, mixes the sound, and attaches the video and a contact sheet. A shot with no
footage becomes a branded card, so you can render the script alone to check pacing before spending
anything on clips. To work on files without the Hub: `cmsbot video render storyboard.json --assets
dir --out dir --project-file project.json`.

## 7. Read the checks, then hand over
The render returns checks (size, length, sound, caption timing, every word captioned, footage, on-
screen text length, pace). Fix anything not ok that you can fix; what you cannot (no voiceover yet)
goes in `gaps`. Look at the contact sheet for stray text or brand marks in generated footage and
for realistic generated people. Then `submit_for_review`.

## Two reviews
A video is reviewed twice. First the script: the reviewer approves it and the item comes back to
you as "needs revision" with the note "Script approved. Produce the video". Do not generate footage
before that. Then the finished video. If the reviewer edits words at the second review, the Hub
renders it again; you do nothing.

## Do not
Use a realistic generated presenter for health, finance or legal advice. Invent a testimonial or a
first-person result. Post without the platform's AI-content label when the footage is generated.
