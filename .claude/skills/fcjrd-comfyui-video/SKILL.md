---
name: fcjrd-comfyui-video
description: Render a Fort Collins Junk Removal and Demo video from a shot list using free local ComfyUI video generation (Wan 2.2 image-to-video) and ffmpeg assembly. Use when the user wants to turn a Claude-written UGC video package into an actual MP4 without any paid video service, subscription, or Arcads-style platform. Handles talking clips, action clips, real photos, outro, captions, and music. Never posts, publishes, spends, or logs into any account.
---

# FCJRD ComfyUI Video Skill

## What this skill does

Turns a shot-list JSON file (the output of the Claude UGC video
creation prompt) into a finished vertical video, using only free
local tools:

1. **Render** — each shot becomes one short clip via a local
   ComfyUI server running an open video model (Wan 2.2
   image-to-video is the default; LTX is the alternative when
   native synced audio is needed).
2. **Check** — every talking clip gets a frame check before
   assembly. A failed check cuts the line or holds the video.
   Never fake it.
3. **Assemble** — clips + real photo + outro + captions + music
   stitched with ffmpeg into one 1080x1920 MP4.
4. **Deliver** — the finished file and a note. Nothing posts,
   publishes, or sends anywhere without the user's explicit
   approval of that exact video.

## Prerequisites (one-time, on the machine that renders)

- ComfyUI installed and running locally (default
  `http://127.0.0.1:8188`). See INSTALL.md.
- A Wan 2.2 image-to-video workflow saved from ComfyUI in API
  format, with these literal tokens in the right fields:
  `__IMAGE__` (input image filename), `__PROMPT__` (shot prompt),
  `__FRAMES__` (frame count), and ideally `__SEED__` (sampler
  seed; without it a failed take cannot be re-rolled, because
  every render reuses the template's seed). The render script
  replaces these tokens per shot. No node IDs are hardcoded in
  this skill, so any working Wan 2.2 I2V workflow can be the
  template. It must end in a video save node that writes MP4
  (core Save Video, or VideoHelperSuite Video Combine with
  `video/h264-mp4`).
- GPU guidance: 12GB VRAM is the realistic floor for Wan 2.2 at
  usable quality. The smallest Wan 2.2 variant can run on 8GB
  with quantized/offloaded settings, slower and softer. Below
  8GB, do not promise local rendering — use hosted free daily
  credits for prototyping instead.
- ffmpeg installed and on PATH (assembly + checks).
- Reference images on disk: canonical Patches, Biscuit, and any
  real job photos. Paths go in the shot list, never guessed.

## Inputs

### Shot-list JSON (the only input the skill needs)

```json
{
  "video": {
    "title": "Garage cleanout 30s",
    "track": "PATCHES",
    "length_sec": 30,
    "music": "upbeat.mp3",
    "music_volume": 0.15
  },
  "shots": [
    {
      "id": "s01",
      "type": "talking",
      "ref_image": "refs/patches-cap.png",
      "prompt": "Patches, gray-white tabby cat in blue PATCHES cap, talks to camera in a garage, mouth moving with speech, natural gestures",
      "dialogue": "Hi, I'm Patches. Let me tell you about this job.",
      "voice_file": "voices/s01.wav",
      "duration_sec": 5,
      "text_on_screen": "Hi, I'm Patches.",
      "sound": "upbeat music, no dead beats"
    },
    {
      "id": "s02",
      "type": "action",
      "ref_image": "refs/garage-mess.jpg",
      "prompt": "Cluttered garage, boxes and old furniture being carried out, fast tidy motion, no people facing camera",
      "duration_sec": 6,
      "text_on_screen": "Messy garage?",
      "sound": "work sounds + music"
    },
    {
      "id": "s03",
      "type": "photo",
      "ref_image": "photos/real-garage-after.jpg",
      "duration_sec": 3,
      "text_on_screen": "REAL JOB — clean garage",
      "sound": "music only"
    },
    {
      "id": "s04",
      "type": "outro",
      "ref_image": "refs/outro-clean.png",
      "duration_sec": 4,
      "text_on_screen": "Fort Collins Junk Removal and Demo — since 2015 — 970-694-7523",
      "sound": "music only"
    }
  ]
}
```

Shot types: `talking` (character speaks a line), `action`
(motion/B-roll, voice-free), `photo` (real photo held on screen),
`outro` (outro image held on screen, voice-free).

Optional shot fields:
- `"seed": 1234`: render this take with a fixed seed (needs
  `__SEED__` in the template). The seed of every render is saved
  in `renders/<video-slug>/manifest.json`.
- `"voice_file": "clip"` (talking shots only): use the rendered
  clip's own audio as the line, for a shot made with an
  audio-native model such as LTX.
- `"burn_text": false` to skip drawing a shot's `text_on_screen`,
  or `"burn_text": true` on an outro whose image has no text of
  its own (outro text is not drawn by default, because outro
  artwork normally carries it).

### Locked business facts (never invent beyond these)

- Business: **Fort Collins Junk Removal and Demo** — always the
  full name. NEVER "FCJRD".
- Serving since: **2015**. Phone: **970-694-7523**.
- Website: https://fortcollinsjunkremovalanddemo.com/
- Services here: junk removal and cleanouts, demolition.
- CTA every time: call/text 970-694-7523, or "send us a
  message". Photo-estimate line: "Text us 3 to 6 photos for a
  FREE estimate."
- Unknown prices, claims, numbers, or facts stay `[BLANK]`.
  Never invent them to fill a shot.

## Workflow

### Step 1 — Validate the shot list

- Every shot has an id, a type, a duration, and on-screen text.
- Every `talking` shot has a dialogue line AND a voice file.
- Every `ref_image` path exists on disk. Stop on the first
  missing file and say which one — never substitute another
  image, character, or photo.
- One consistent on-screen character across all shots. If the
  reference images show different characters, stop and ask —
  never render a video with a changing Patches, dog, or actor.

### Step 2 — Render (`scripts/render_shots.py`)

```
python3 scripts/render_shots.py \
  --shot-list shot-list.json \
  --template workflows/wan22-i2v-api.json \
  --out-dir renders/<video-slug>/ \
  --comfy-url http://127.0.0.1:8188
```

- Uploads each shot's reference image to ComfyUI, replaces the
  `__IMAGE__`, `__PROMPT__`, `__FRAMES__` (and `__SEED__`) tokens,
  submits the job, polls until done, downloads the clip as
  `<shot id>.mp4`. Clips saved as WebM, GIF or MOV are converted
  to MP4 with ffmpeg.
- Frame counts are rounded UP to a length the model accepts:
  4n+1 for Wan (default), `--frame-step 8` for LTX's 8n+1. Set
  `--fps` to the frame rate the workflow renders at (Wan 2.2 5B:
  24, the default; Wan 2.2 14B: 16).
- `photo` and `outro` shots are NOT rendered — they are held
  images in assembly.
- `--dry-run` prints exactly what would be submitted (frames,
  seeds) and checks the template still parses, without touching
  the server. Use it whenever the setup is unproven.
- Re-roll one failed take without touching the others:
  `--only s03 --seed <new number>`. A seed that gives a good take
  can be pinned in that shot's `"seed"` field.
- If ComfyUI is unreachable, the queue errors, or the GPU runs
  out of memory: stop, report the exact error, and suggest the
  documented fallback (smaller/quantized model variant, fewer
  frames, or hosted free credits for that shot). Never hammer a
  failing server with retries.

### Step 3 — Check every talking clip (the gate)

For each `talking` shot, look at its start/middle/end frames
(assemble_video.py writes them to `--checks-dir`, default
`<video-slug>_checks/` next to the video) and verify:

- The speaker on screen is the speaker of the line, mouth
  visibly moving on the words, closing on pauses.
- Same character, same outfit, same look as every other shot.
- No blur-filled background, no cropped heads/paws, no repeated
  scene from another shot.

**Mouth-sync rule (universal, no exceptions):** if you hear a
character, you see THAT SAME character talking, mouth moving,
for that line. One speaker at a time, back and forth, speaker
label every line. Fixed-portrait puppet/drawn mouths are BANNED.
No dialogue over action, no off-screen character voices, no
narration loophole. Real photos and outros are voice-free for
characters — the CTA is spoken by a talking clip FIRST, then the
outro runs as text only.

**Local-model honesty:** Wan 2.2 image-to-video generates
motion, not guaranteed lip-synced speech. A talking clip whose
mouth does not match the line FAILS the check. Options, in
order: (1) re-render that shot with a different seed/prompt,
(2) try an audio-native local model (LTX) for that shot,
(3) cut the line, (4) hold the video. Never ship dialogue over
action as a workaround, and never present a failed clip as a
new version.

### Step 4 — Assemble (`scripts/assemble_video.py`)

```
python3 scripts/assemble_video.py \
  --shot-list shot-list.json \
  --renders renders/<video-slug>/ \
  --out <video-slug>.mp4
```

- Normalizes every part to 1080x1920, 30fps, H.264 + AAC, each
  exactly its shot's length. A rendered clip shorter than its
  shot is refused (never frozen to fill time).
- Concatenates in shot order: talking clips, action clips, held
  real photo (sharp, full-width, untouched, on a solid dark-teal
  background with labels on the solid area — NEVER blurred fill),
  held outro.
- Each voice line starts when its talking shot starts. A line
  longer than its shot is refused (it would play over the next
  shot), and a `voice_file` on any non-talking shot is refused.
- Music from `--music` or `video.music`, mixed under everything
  at `--music-volume`: `video.music_volume` if set, else 0.15
  under voice lines or 1.0 for a music-only video. A video with
  no voice and no music is refused. Sound from second zero, no
  dead beats.
- Every shot's `text_on_screen` is burned in automatically: on
  the solid teal under a photo or padded clip, or in the lower
  third of a full-frame clip (see `burn_text` for outros).
  `--captions file.srt|file.ass` burns your own file instead;
  `--no-captions` burns none.
- Final verify: resolution 1080x1920, duration matches the shot
  list total, audio for the whole video, file decodes cleanly.
  Any failure exits non-zero; never deliver a video that failed.

### Step 5 — Deliver

Report: the finished file path, total length, shot count,
which shots were re-rendered or cut (if any), and what checks
passed. Then STOP. Posting, uploading, or sending is a separate
step that needs the user's explicit approval of that exact
video and channel.

## What this skill never does

- Never posts, publishes, uploads, or sends a video anywhere.
- Never spends money, starts a trial, or logs into any account.
- Never uses real crew faces, addresses, or license plates.
- Never invents prices, claims, numbers, or review counts.
- Never shortens the business name to "FCJRD".
- Never reuses a scrapped clip, failed take, or brown-tabby
  Patches render as a new video.
- Never moves production watches or scheduled jobs onto this
  tool — those stay on dumb cron + state files.

## Failure modes and fallbacks

| Problem | Do this |
|---|---|
| ComfyUI unreachable | Say so, give the exact URL tried, stop. |
| GPU out of memory | Smaller/quantized Wan 2.2 variant, fewer frames, shorter clip. |
| Talking clip mouth does not match | Re-roll with `--only <id> --seed <new>`, try LTX for that shot, cut the line, or hold. |
| Re-render gives the identical clip | The template has no `__SEED__` token. Add it to the sampler's seed field. |
| ComfyUI finished but saved no video | End the workflow with Save Video (MP4) or Video Combine (h264-mp4). |
| Voice line longer than its shot | Lengthen the shot and re-render it, or re-record the line shorter. Never let it spill. |
| Rendered clip shorter than its shot | Check `--fps` matches the workflow, then re-render. |
| Character changes between shots | Stop. Same refs every shot or no video. |
| Render queue stalls | One status check, then stop and report. Never loop retries. |
| No GPU on the machine | Hosted free daily credits for prototyping only. Say so plainly. |
