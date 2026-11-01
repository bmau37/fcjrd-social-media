# Changes from the original upload

Every fix below was tested against a mock ComfyUI server and synthetic
talking/action/photo/outro videos. There was no GPU in the test
environment, so no real Wan render has been run.

## assemble_video.py

- **Voice timing.** Lines used to be joined back to back, so a 30 s video
  played all its dialogue in the first ~9 s, over the wrong shots
  (including action shots). Each line now starts when its own talking
  shot starts.
- **Truncated videos.** With voices plus music, the output was cut down
  (a 30 s shot list came out 20 s). The sound now spans the whole video.
- **On-screen text was never drawn.** `text_on_screen` was ignored unless
  you passed a captions file. It is now burned in automatically, on the
  solid teal under photos and padded clips, or in the lower third of
  full-frame clips. `burn_text` controls it per shot; outro text is
  opt-in because outro art normally carries it.
- **Music settings in the shot list were ignored.** `video.music` and
  `video.music_volume` are now used. The default volume is 0.15 under
  voice lines and 1.0 for music-only videos, which used to come out
  nearly silent.
- **New refusals.** The assembler now stops on a voice line longer than
  its shot, a voice on a non-talking shot, a clip shorter than its shot,
  and a video with no sound at all.
- **Verification now verifies.** Resolution, length, audio for the full
  length and a clean decode are checked, and any failure exits non-zero.
  The old check only printed numbers.
- **Windows paths.** Backslashes and drive letters used to break joining
  the parts and burning captions; the captions failure was reported as
  "subtitles filter unavailable".
- **Caption quality.** The caption pass re-encoded at the default quality
  (CRF 23) after every part had been encoded at CRF 18. It is now CRF 18.
  The output is also faststart, so it plays before it fully downloads.
- **LTX audio.** `"voice_file": "clip"` uses the rendered clip's own audio,
  so an audio-native model's lip-synced sound is kept instead of dropped.
- **Clean-up.** Temp files are removed even when a step fails, and check
  frames default to `<video>_checks/`.

## render_shots.py

- **Video outputs not found.** Only the `images` output key was read, so
  VideoHelperSuite's Video Combine (which reports under `gifs`) waited
  until the timeout. It now reads `gifs`, `videos` and `images`, prefers
  real video files, and converts WebM/GIF/MOV to MP4.
- **No video saved.** If a job finishes without saving any video, the
  script now stops at once with the reason instead of waiting out the
  timeout.
- **Quotes in prompts** (for example `blue "PATCHES" cap`) broke the
  workflow JSON. Prompts are now escaped.
- **Frame counts.** Wan needs 4n+1 frames; the script asked for exactly
  duration × fps (5 s → 120). Frame counts now round up (5 s → 121), with
  `--frame-step 8` for LTX's 8n+1.
- **Re-rolling a take.** Re-rendering a shot used to give the identical
  clip. The new optional `__SEED__` token, `--seed` and `--only s03` make
  it possible, and every render's seed is saved to `manifest.json`.
  Re-rendering one shot keeps the other shots' manifest entries.
- **Number tokens.** A quoted `"__FRAMES__"` or `"__SEED__"` becomes a real
  number, so number fields can be tokenized by editing the exported JSON.
- **Download links.** Filenames with spaces are now URL-encoded.
- **Dry run** now confirms the template still parses for every shot.
