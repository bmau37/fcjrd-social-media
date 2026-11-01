# Install: FCJRD ComfyUI Video Skill for Claude Code

Everything here is free. No subscription, no account, no API key.
What it needs is a machine with a real graphics card. Read the GPU
note first — it decides whether local rendering is real for you.

## 0. GPU check (do this first)

- 12GB VRAM or more: Wan 2.2 runs at usable quality. Go.
- 8GB VRAM: the smallest Wan 2.2 variant with quantized /
  offloaded settings can work, slower and softer. Test with one
  shot before committing to a full video.
- Under 8GB: do NOT promise local rendering. Use hosted free
  daily credits (e.g. Google Flow) for prototyping only.

If you do not know the Lenovo's graphics card, find it before
installing anything else — it is the one fact this whole skill
depends on.

## 1. Install ComfyUI (on the rendering machine)

Follow ComfyUI's own install guide for your OS. When it runs,
open http://127.0.0.1:8188 in a browser — you should see the
ComfyUI canvas. Leave ComfyUI running while rendering; the
scripts talk to it over that local address only. Nothing goes
to the cloud.

## 2. Install the Wan 2.2 image-to-video model

In ComfyUI, load or build a Wan 2.2 image-to-video workflow
(ComfyUI ships Wan examples; the community Wan 2.2 I2V template
is the starting point). Download the Wan 2.2 model files it
asks for. Render one test clip by hand in the ComfyUI UI first
— if the UI cannot render, the skill cannot either, and the
problem is the setup, not the skill.

## 3. Save the workflow in API format (the one fiddly step)

1. In ComfyUI, open your working Wan 2.2 I2V workflow. Make sure
   it ends in a video save node that writes MP4 (core Save Video,
   or VideoHelperSuite Video Combine with `video/h264-mp4`).
2. In the input-image field, type the literal text `__IMAGE__`.
   In the prompt field, type the literal text `__PROMPT__`.
3. Save it as API format (ComfyUI: Workflow menu → Export (API))
   to `workflows/wan22-i2v-api.json` inside this skill folder.
4. Number fields (frame count, seed) will not take text, so set
   them in the saved file instead. Open
   `workflows/wan22-i2v-api.json` in a text editor:
   - frame count (`"length": 81` on WanImageToVideo): change the
     number to `"__FRAMES__"`
   - seed (`"seed": ...` on KSampler, or `"noise_seed": ...` on
     KSamplerAdvanced): change the number to `"__SEED__"`
   The render script turns those quoted tokens back into numbers.
   `__SEED__` is optional but strongly recommended: without it,
   re-rendering a failed take gives the exact same clip.
5. Put your real values back in the UI workflow afterwards so
   hand-rendering still works.

Why tokens: the render script never guesses your node layout.
It loads your exported workflow, swaps the tokens per shot, and
submits it. Any working Wan 2.2 I2V workflow works.

## 4. Install the skill into Claude Code

Copy this whole folder to:

```
~/.claude/skills/fcjrd-comfyui-video/
```

(or a project's `.claude/skills/fcjrd-comfyui-video/` for one
project only). Restart Claude Code, then ask it to render using
the skill. No API key, no login, no MCP server to register —
the skill drives local ComfyUI and local ffmpeg only.

## 5. Prepare one video's inputs

- `refs/` — canonical Patches image(s), Biscuit if used, outro
  image. Same character images for every shot of a video.
- `photos/` — real job photos (sharp, untouched; the assembler
  pads them with solid dark teal, never blur).
- `voices/` — one voice file per talking line, locked voice
  per character (record however you already record voices).
- `shot-list.json` — copy `shot-list.example.json` and fill it
  in, or paste a Claude-written package into that shape.
  Paths in it are relative to the shot-list file.
- `music/` — one upbeat track (optional).

## 6. Test with ONE shot first (do not skip this)

```
python3 scripts/render_shots.py --shot-list shot-list.json \
  --template workflows/wan22-i2v-api.json \
  --out-dir renders/test/ --dry-run
```

Dry run prints what would be submitted (frame counts, seeds),
checks the template still parses, and touches nothing. Add
`--fps 16` if your workflow is the Wan 2.2 14B model (the 5B
model renders at 24, the default). Then run it for real on a
one-shot test list, frame-check the talking clip (mouth moving
on the words?), and only then run a full video. This one test
answers the big open question: whether local Wan clips pass your
mouth-sync rule. If the talking clip fails, re-roll it with a
new seed (`--only s01 --seed 777`), try LTX for that shot, cut
the line, or hold the video — never ship dialogue over action.

## 7. Render and assemble a full video

```
python3 scripts/render_shots.py --shot-list shot-list.json \
  --template workflows/wan22-i2v-api.json \
  --out-dir renders/my-video/

python3 scripts/assemble_video.py --shot-list shot-list.json \
  --renders renders/my-video/ --out my-video.mp4 \
  --checks-dir checks/my-video/
```

The assembler takes the music and its volume from the shot
list's `video` block, burns every shot's `text_on_screen`, puts
each voice line at the start of its own shot, and checks the
finished file. If it prints `ERROR`, the video is not done.

Review the check frames for every talking line. Then the
video is yours to approve for posting — the skill itself never
posts, uploads, or sends anything anywhere.

## What this skill does NOT do

- No posting, publishing, or uploading (your approval, your tap).
- No spending, trials, subscriptions, or logins.
- No lip-sync magic: it renders motion and enforces the check
  gate. A talking clip that fails the gate gets cut or held.
- No GPU, no local rendering — see step 0.
