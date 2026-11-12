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

## 6. Assemble
Render overlays and captions from `storyboard.json` and `timing.json` as transparent frames
(HTML to image) and composite over the clips with `ffmpeg` at 1080x1920. Captions use the words
actually spoken. Rotate at least three visual treatments per account so posts do not look stamped.

## 7. Check with numbers, then hand over
Before saving: every caption on screen at its word's time; text at least 36 px on a phone-sized
frame; no stray text or brand marks in footage; no realistic generated people presenting advice;
required AI-content and brand disclosures noted in the caption fields. Review a contact sheet of
one frame per second. Then `save_draft(item_id=..., media=[{"url": ...}])` with the video and
`submit_for_review`. List known issues in `gaps` instead of hiding them.

## Do not
Use a realistic generated presenter for health, finance or legal advice. Invent a testimonial or a
first-person result. Post without the platform's AI-content label when the footage is generated.
