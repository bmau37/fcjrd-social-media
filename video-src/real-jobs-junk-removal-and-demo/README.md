# Real jobs: junk removal + demolition (30 s, 9:16)

Finished video: [`v/real-jobs-junk-removal-and-demo-9x16.mp4`](../../v/real-jobs-junk-removal-and-demo-9x16.mp4)
(1080x1920, 30 fps, H.264 + AAC, 30.0 s, music −14 LUFS).

Assembled with the FCJRD ComfyUI video skill's own `scripts/assemble_video.py`.
Every shot is a real job photo or the outro, so there is no ComfyUI render,
no GPU, no on-screen character and no voice. The call to action is text-only
on the outro.

| Time | Shot | Real job photo | On screen |
|---|---|---|---|
| 0–3 s | s01 | Junk Removal and Yard Clean Up | JUNK REMOVAL · GOT A PILE / OF JUNK? |
| 3–6 s | s02 | Garage Clean Out | JUNK REMOVAL · WE LOAD IT. |
| 6–9 s | s03 | Furniture Junk Removal | JUNK REMOVAL · WE HAUL IT. |
| 9–12 s | s04 | Couch and Junk Removal | JUNK REMOVAL · YOU RELAX. |
| 12–15 s | s05 | Fort Collins Kitchen Demolition | DEMOLITION · THAT OLD KITCHEN / HAS TO GO? |
| 15–18 s | s06 | Berthoud Playhouse Demolition | DEMOLITION · WE TEAR IT DOWN. |
| 18–21 s | s07 | Wheelchair Ramp Demolition | DEMOLITION · WE HAUL IT OFF. |
| 21–24 s | s08 | Wind Damaged Shed Removal | DEMOLITION · SITE LEFT CLEAN. |
| 24–30 s | s09 | outro | logo · CALL OR TEXT 970-694-7523 · Text us 3 to 6 photos for a FREE estimate. · or send us a message · website · SINCE 2015 |

Photos are shown full-width and untouched on the skill's solid dark teal,
with every label on the solid area. The brand line on each photo shot is the
full business name. The alley junk photo was left out because a house number
is readable on the pole.

## Files

- `shot-list.json`: the skill's shot list (paths are relative to this folder).
- `captions.ass`: the on-screen text, generated from the shot list. It is an
  `.ass` file instead of `.srt` so the labels can be styled and placed on the
  teal; the skill's captions step burns it in as-is. The fonts are embedded, so
  it renders the same on any machine.
- `refs/outro-junk-and-demo.png`: the outro. `refs/logo-with-cat.jpg` is the
  logo from the website.
- `music/junk-and-demo-bed.m4a`: an original 120 BPM music bed synthesized from
  scratch by `build_sources.py` (no samples or loops, so there is nothing to license).
- `fonts/`: Anton and Montserrat ExtraBold, subset to basic Latin (SIL Open Font License 1.1).
- `build_sources.py`: regenerates all of the files above (needs Pillow, numpy and ffmpeg).

## Re-assemble (for example with your own music)

```
python3 ~/.claude/skills/fcjrd-comfyui-video/scripts/assemble_video.py \
  --shot-list shot-list.json --renders renders/ \
  --out real-jobs-junk-removal-and-demo-9x16.mp4 \
  --music music/junk-and-demo-bed.m4a --music-volume 1.0 \
  --captions captions.ass
```

- `--music-volume 1.0`: with no voice to duck under, the skill's 0.15 default
  would leave a music-only video almost silent.
- `--renders` can point at any folder. No rendered clips are used.
- The copy in `v/` was remuxed afterwards with `ffmpeg -c copy -movflags +faststart`
  so it starts playing before it fully downloads, like the blitz clips. This is
  lossless: the packets are identical.
