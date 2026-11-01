#!/usr/bin/env python3
"""Assemble a finished FCJRD video from rendered clips + held images.

Inputs: a shot-list JSON (the same file render_shots.py used) and the
renders directory render_shots.py filled (<shot id>.mp4 for every
talking or action shot).

Picture: one part per shot, in order, exactly the shot's length
(1080x1920, 30 fps, H.264):
  talking/action -> rendered clip, scaled/padded to 1080x1920
  photo          -> real photo, full-width and sharp, padded with solid
                    dark teal (the standing no-blur rule)
  outro          -> outro image held on screen

Sound: each talking shot's voice line starts when its shot starts and
must end before the shot ends, so a line never plays over another shot.
Shots without a line are silent. "voice_file": "clip" uses the rendered
clip's own audio (an audio-native model such as LTX). Music (--music or
video.music in the shot list) runs under the whole video at
--music-volume: default video.music_volume, else 0.15 under voice lines
or 1.0 for a music-only video. A video with no voice and no music is
refused (sound from second zero).

On-screen text: every shot's text_on_screen is burned in automatically,
on the solid teal under a photo or padded clip, or in the lower third of
a full-frame clip. Outro images normally carry their own text, so an
outro's text is only drawn with "burn_text": true; any other shot can opt
out with "burn_text": false. --captions FILE (.srt or .ass) burns your own
file instead; --no-captions burns none.

Check frames: start/middle/end stills of every talking clip go to
--checks-dir (default <out>_checks/) for the manual mouth-sync gate in
SKILL.md. The script does not judge lip-sync itself.

Verification: 1080x1920, duration equals the shot-list total, audio
present for the whole video, file decodes cleanly. Any failure exits
non-zero.

Requires ffmpeg + ffprobe on PATH. Standard library only otherwise.

Usage:
  python3 assemble_video.py --shot-list shot-list.json \\
      --renders renders/my-video/ --out my-video.mp4 \\
      [--music music.mp3] [--music-volume 0.15] \\
      [--captions captions.srt | --no-captions] [--checks-dir checks/my-video/]
"""

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile

WIDTH, HEIGHT, FPS = 1080, 1920, 30
DARK_TEAL = "0d3b3b"
RATE = 48000                      # 1600 samples per video frame
SAMPLES_PER_FRAME = RATE // FPS
VOICE_TOLERANCE = 0.05            # seconds a line may run past its shot (trailing room tone)
HEADROOM_DB = -1.0                # peak ceiling for the final mix
AFMT = f"aresample={RATE},aformat=sample_fmts=fltp:channel_layouts=stereo"


def die(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def warn(msg):
    print(f"WARNING: {msg}", file=sys.stderr)


def run(cmd, what, cwd=None):
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", cwd=cwd)
    if result.returncode != 0:
        tail = (result.stderr or "").strip().splitlines()[-5:]
        die(f"{what} failed: " + " | ".join(tail))
    return result


def check_tool(name):
    if shutil.which(name) is None:
        die(f"{name} not found on PATH. Install ffmpeg (provides ffmpeg + ffprobe).")


def resolve(base, path):
    return path if os.path.isabs(path) else os.path.join(base, path)


def resolve_cli(base, path):
    """CLI paths: relative to the current folder, else to the shot list's folder."""
    if os.path.isabs(path) or os.path.exists(path):
        return os.path.abspath(path)
    return os.path.abspath(os.path.join(base, path))


def probe(path):
    out = run(["ffprobe", "-v", "error", "-show_entries",
               "stream=codec_type,width,height,duration:format=duration",
               "-of", "json", path], f"reading {path}").stdout
    return json.loads(out or "{}")


def stream(info, kind):
    return next((s for s in info.get("streams", []) if s.get("codec_type") == kind), None)


def duration_of(info):
    for value in ((info.get("format") or {}).get("duration"),
                  (stream(info, "audio") or {}).get("duration"),
                  (stream(info, "video") or {}).get("duration")):
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def frames_of(shot):
    return max(1, int(round(float(shot["duration_sec"]) * FPS)))


def concat_line(path):
    """Concat-demuxer entry that survives Windows backslashes and quotes."""
    p = os.path.abspath(path).replace("\\", "/").replace("'", "'\\''")
    return f"file '{p}'\n"


def clip_path(renders, shot):
    return os.path.join(renders, f"{shot['id']}.mp4")


# ---------------------------------------------------------------- checks

def validate(shots, base, renders):
    if not isinstance(shots, list) or not shots:
        die("Shot list has no shots.")
    seen = set()
    for shot in shots:
        sid = shot.get("id")
        if not sid:
            die("A shot has no id.")
        if sid in seen:
            die(f"Duplicate shot id: {sid}")
        seen.add(sid)
        kind = shot.get("type")
        if kind not in ("talking", "action", "photo", "outro"):
            die(f"Shot {sid}: unknown type {kind!r}")
        try:
            ok = float(shot.get("duration_sec") or 0) > 0
        except (TypeError, ValueError):
            ok = False
        if not ok:
            die(f"Shot {sid}: missing or invalid duration_sec")
        if not str(shot.get("text_on_screen") or "").strip():
            warn(f"Shot {sid}: no text_on_screen.")
        voice = shot.get("voice_file")
        if kind == "talking":
            if not shot.get("dialogue"):
                die(f"Shot {sid}: talking shot needs a dialogue line.")
            if not voice:
                die(f"Shot {sid}: talking shot needs a voice_file "
                    "(or \"clip\" to use the rendered clip's own audio).")
        elif voice:
            article = "an" if kind[0] in "aeiou" else "a"
            die(f"Shot {sid}: only talking shots may have a voice_file. A voice over "
                f"{article} {kind} shot is dialogue without a matching mouth on screen.")
        if kind in ("photo", "outro"):
            img = resolve(base, shot.get("ref_image") or "")
            if not shot.get("ref_image") or not os.path.exists(img):
                die(f"Shot {sid}: image missing: {img}")
        else:
            clip = clip_path(renders, shot)
            if not os.path.exists(clip):
                die(f"Shot {sid}: rendered clip missing: {clip}. Run render_shots.py first.")
        if voice and voice != "clip" and not os.path.exists(resolve(base, voice)):
            die(f"Shot {sid}: voice file not found: {resolve(base, voice)}")


# ---------------------------------------------------------------- picture

def picture_box(shot, base, renders):
    """Top and bottom of the picture inside the 1080x1920 frame."""
    if shot["type"] in ("photo", "outro"):
        info = probe(resolve(base, shot["ref_image"]))
    else:
        info = probe(clip_path(renders, shot))
    v = stream(info, "video") or {}
    w, h = int(v.get("width") or WIDTH), int(v.get("height") or HEIGHT)
    ph = HEIGHT if w * HEIGHT < h * WIDTH else int(WIDTH * h / w + 0.5)
    top = (HEIGHT - ph) // 2
    return top, top + ph


def build_part(shot, renders, base, tmpdir, index):
    """One normalized part, exactly frames_of(shot) frames long."""
    sid = shot["id"]
    frames = frames_of(shot)
    part = os.path.join(tmpdir, f"part_{index:02d}_{sid}.mp4")
    vf = (f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
          f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=#{DARK_TEAL},"
          f"fps={FPS},format=yuv420p")
    encode = ["-vf", vf, "-frames:v", str(frames), "-an", "-c:v", "libx264",
              "-preset", "medium", "-crf", "18", part]
    if shot["type"] in ("talking", "action"):
        clip = clip_path(renders, shot)
        length = duration_of(probe(clip))
        need = frames / FPS
        if length is not None and length < need - 1.5 / FPS:
            die(f"Shot {sid}: rendered clip is {length:.2f}s but the shot is {need:.2f}s. "
                "Re-render it (render_shots.py rounds frames up) or shorten the shot. "
                "A clip is never frozen to fill time.")
        run(["ffmpeg", "-y", "-v", "error", "-i", clip] + encode, f"normalizing clip {sid}")
    else:
        img = resolve(base, shot["ref_image"])
        run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", img] + encode,
            f"holding image {sid}")
    return part


# ---------------------------------------------------------------- sound

def voice_track(shots, base, renders, tmpdir):
    """WAV as long as the video: each line at its shot's start, silence elsewhere."""
    segments, placed = [], []
    t = 0.0
    for i, shot in enumerate(shots):
        frames = frames_of(shot)
        samples = frames * SAMPLES_PER_FRAME
        dur = frames / FPS
        seg = os.path.join(tmpdir, f"voice_{i:02d}.wav")
        voice = shot.get("voice_file")
        if voice:
            src = clip_path(renders, shot) if voice == "clip" else resolve(base, voice)
            info = probe(src)
            if stream(info, "audio") is None:
                die(f"Shot {shot['id']}: {src} has no audio track.")
            length = duration_of(info) or 0.0
            if voice != "clip" and length > dur + VOICE_TOLERANCE:
                die(f"Shot {shot['id']}: voice line is {length:.2f}s but the shot is "
                    f"{dur:.2f}s, so it would play over the next shot. Lengthen the shot "
                    "(and re-render it) or re-record the line shorter.")
            run(["ffmpeg", "-y", "-v", "error", "-i", src, "-vn", "-af",
                 f"{AFMT},apad,atrim=end_sample={samples}", "-c:a", "pcm_f32le", seg],
                f"placing voice line {shot['id']}")
            placed.append((shot["id"], t, min(length, dur)))
        else:
            run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                 f"anullsrc=r={RATE}:cl=stereo", "-af", f"atrim=end_sample={samples}",
                 "-c:a", "pcm_f32le", seg], f"silence for {shot['id']}")
        segments.append(seg)
        t += dur
    listing = os.path.join(tmpdir, "voices.txt")
    with open(listing, "w", encoding="utf-8") as f:
        f.writelines(concat_line(s) for s in segments)
    out = os.path.join(tmpdir, "voices.wav")
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", listing,
         "-c", "copy", out], "joining voice track")
    return out, placed


def music_track(music, volume, total_samples, tmpdir):
    length = duration_of(probe(music))
    total = total_samples / RATE
    if length is not None and length < total - 0.05:
        warn(f"Music is {length:.1f}s but the video is {total:.1f}s; the last "
             f"{total - length:.1f}s will have no music. Use a longer track.")
    out = os.path.join(tmpdir, "music.wav")
    run(["ffmpeg", "-y", "-v", "error", "-i", music, "-vn", "-af",
         f"{AFMT},volume={volume},apad,atrim=end_sample={total_samples}",
         "-c:a", "pcm_f32le", out], "preparing music")
    return out


def peak_dbfs(wav):
    r = run(["ffmpeg", "-hide_banner", "-nostats", "-i", wav, "-af",
             "astats=metadata=0:reset=0", "-f", "null", "-"], "measuring peak level")
    peaks = [line.split("Peak level dB:")[1].strip()
             for line in r.stderr.splitlines() if "Peak level dB:" in line]
    try:
        return float(peaks[-1])
    except (IndexError, ValueError):
        return None


def mix(tracks, tmpdir):
    """Sum the tracks at unity gain, then keep peaks under HEADROOM_DB."""
    out = os.path.join(tmpdir, "mix.wav")
    if len(tracks) == 1:
        shutil.copyfile(tracks[0], out)
    else:
        inputs = []
        for t in tracks:
            inputs += ["-i", t]
        n = len(tracks)
        left = "+".join(f"c{2 * k}" for k in range(n))
        right = "+".join(f"c{2 * k + 1}" for k in range(n))
        run(["ffmpeg", "-y", "-v", "error"] + inputs + ["-filter_complex",
             f"amerge=inputs={n},pan=stereo|c0={left}|c1={right}",
             "-c:a", "pcm_f32le", out], "mixing voice and music")
    peak = peak_dbfs(out)
    if peak is not None and peak > HEADROOM_DB:
        gain = HEADROOM_DB - peak
        safe = os.path.join(tmpdir, "mix_safe.wav")
        run(["ffmpeg", "-y", "-v", "error", "-i", out, "-af", f"volume={gain:.2f}dB",
             "-c:a", "pcm_f32le", safe], "lowering mix peak")
        print(f"Mix peaked at {peak:+.1f} dBFS; lowered by {-gain:.1f} dB to avoid clipping.")
        return safe
    return out


# ---------------------------------------------------------------- captions

def ass_time(t):
    cs = int(math.floor(t * 100 + 1e-6))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def ass_escape(text):
    return (text.replace("\\", "/").replace("{", "(").replace("}", ")")
            .replace("\r", "").replace("\n", "\\N"))


def burns_text(shot):
    """Outro artwork carries its own text unless the shot says burn_text: true."""
    default = shot["type"] != "outro"
    return bool(shot.get("burn_text", default))


def auto_captions(shots, base, renders, path):
    """text_on_screen for every shot: on the solid area under the picture when
    there is room, else in the lower third (above the app's own buttons)."""
    lines = [
        "[Script Info]",
        "; Generated by assemble_video.py from each shot's text_on_screen.",
        "ScriptType: v4.00+",
        f"PlayResX: {WIDTH}",
        f"PlayResY: {HEIGHT}",
        "ScaledBorderAndShadow: yes",
        "WrapStyle: 0",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Caption,Arial,84,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,"
        "-1,0,0,0,100,100,0,0,1,6,3,8,90,90,0,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    count = 0
    t = 0.0
    for shot in shots:
        dur = frames_of(shot) / FPS
        text = str(shot.get("text_on_screen") or "").strip()
        if text and burns_text(shot):
            _, bottom = picture_box(shot, base, renders)
            if HEIGHT - bottom >= 300:
                tag = f"{{\\an8\\pos({WIDTH // 2},{bottom + 44})}}"
            else:
                tag = f"{{\\an2\\pos({WIDTH // 2},{HEIGHT - 430})}}"
            lines.append(f"Dialogue: 0,{ass_time(t)},{ass_time(t + dur)},Caption,,0,0,0,,"
                         f"{tag}{ass_escape(text)}")
            count += 1
        t += dur
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return count


def burn_captions(src, captions, out, tmpdir):
    """Burn subtitles with ffmpeg run inside tmpdir, so the filter only ever sees a
    plain relative filename (drive letters and backslashes break filter syntax)."""
    ext = os.path.splitext(captions)[1].lower() or ".srt"
    local_name = "captions" + ext
    local = os.path.join(tmpdir, local_name)
    if os.path.abspath(captions) != os.path.abspath(local):
        shutil.copyfile(captions, local)
    return subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", os.path.abspath(src),
         "-vf", f"subtitles={local_name}", "-c:v", "libx264", "-preset", "medium",
         "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart",
         os.path.abspath(out)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=tmpdir)


# ---------------------------------------------------------------- check frames

def extract_check_frames(shot, renders, checks_dir):
    clip = clip_path(renders, shot)
    dur = frames_of(shot) / FPS
    for label, frac in (("start", 0.1), ("mid", 0.5), ("end", 0.9)):
        out = os.path.join(checks_dir, f"{shot['id']}_{label}.jpg")
        run(["ffmpeg", "-y", "-v", "error", "-ss", f"{dur * frac:.3f}", "-i", clip,
             "-frames:v", "1", "-q:v", "3", out], f"check frame {shot['id']} {label}")


# ---------------------------------------------------------------- verify

def verify(out, total):
    info = probe(out)
    v, a = stream(info, "video"), stream(info, "audio")
    length = duration_of({"format": info.get("format")}) or 0.0
    problems = []
    if not v or (int(v.get("width") or 0), int(v.get("height") or 0)) != (WIDTH, HEIGHT):
        size = f"{v.get('width')}x{v.get('height')}" if v else "no video"
        problems.append(f"resolution is {size}, want {WIDTH}x{HEIGHT}")
    if abs(length - total) > 0.1:
        problems.append(f"length is {length:.2f}s, shot list total is {total:.2f}s")
    if a is None:
        problems.append("no audio track")
    else:
        try:
            alen = float(a.get("duration"))
        except (TypeError, ValueError):
            alen = length
        if alen < total - 0.1:
            problems.append(f"audio stops at {alen:.2f}s of {total:.2f}s")
    decode = subprocess.run(["ffmpeg", "-v", "error", "-i", out, "-f", "null", "-"],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    if decode.returncode != 0 or decode.stderr.strip():
        problems.append("decode errors: " + " | ".join(decode.stderr.strip().splitlines()[-3:]))
    return problems, v, length


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--shot-list", required=True)
    ap.add_argument("--renders", required=True,
                    help="Folder with <shot id>.mp4 clips from render_shots.py "
                         "(any folder when there are no talking/action shots).")
    ap.add_argument("--out", required=True)
    ap.add_argument("--music", help="Music file. Default: video.music in the shot list.")
    ap.add_argument("--music-volume", type=float,
                    help="Music level. Default: video.music_volume in the shot list, "
                         "else 0.15 under voice lines or 1.0 for a music-only video.")
    captions_group = ap.add_mutually_exclusive_group()
    captions_group.add_argument("--captions",
                                help="Burn this .srt/.ass file instead of the automatic "
                                     "text_on_screen captions.")
    captions_group.add_argument("--no-captions", action="store_true",
                                help="Burn no on-screen text.")
    ap.add_argument("--checks-dir",
                    help="Talking-clip check frames. Default: <out>_checks/ when there are "
                         "talking shots.")
    args = ap.parse_args()

    check_tool("ffmpeg")
    check_tool("ffprobe")
    base = os.path.dirname(os.path.abspath(args.shot_list))
    with open(args.shot_list, encoding="utf-8") as f:
        data = json.load(f)
    shots = data.get("shots")
    video = data.get("video") or {}
    renders = os.path.abspath(args.renders)
    validate(shots, base, renders)

    has_voice = any(s.get("voice_file") for s in shots)
    music = None
    if args.music:
        music = resolve_cli(base, args.music)
    elif video.get("music"):
        music = resolve(base, video["music"])
    if music and not os.path.exists(music):
        die(f"Music file not found: {music}")
    if args.music_volume is not None:
        music_volume = args.music_volume
    elif video.get("music_volume") is not None:
        music_volume = float(video["music_volume"])
    else:
        music_volume = 0.15 if has_voice else 1.0
    if not has_voice and not music:
        die("No sound: this shot list has no voice lines and no music. "
            "Pass --music (or set video.music in the shot list).")
    captions = resolve_cli(base, args.captions) if args.captions else None
    if captions and not os.path.exists(captions):
        die(f"Captions file not found: {captions}")

    out = os.path.abspath(args.out)
    out_dir = os.path.dirname(out)
    os.makedirs(out_dir, exist_ok=True)
    total_frames = sum(frames_of(s) for s in shots)
    total = total_frames / FPS
    tmpdir = tempfile.mkdtemp(prefix=".assemble_", dir=out_dir)
    try:
        parts = [build_part(s, renders, base, tmpdir, i) for i, s in enumerate(shots)]
        listing = os.path.join(tmpdir, "parts.txt")
        with open(listing, "w", encoding="utf-8") as f:
            f.writelines(concat_line(p) for p in parts)
        video_only = os.path.join(tmpdir, "video_only.mp4")
        run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", listing,
             "-c", "copy", video_only], "joining parts")

        tracks, placed = [], []
        if has_voice:
            voices, placed = voice_track(shots, base, renders, tmpdir)
            tracks.append(voices)
        if music:
            tracks.append(music_track(music, music_volume, total_frames * SAMPLES_PER_FRAME,
                                      tmpdir))
        audio = mix(tracks, tmpdir)

        staged = os.path.join(tmpdir, "staged.mp4")
        run(["ffmpeg", "-y", "-v", "error", "-i", video_only, "-i", audio,
             "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
             "-ar", str(RATE), "-t", f"{total:.6f}", "-movflags", "+faststart", staged],
            "adding sound")

        if args.no_captions:
            caption_note = "off (--no-captions)"
            os.replace(staged, out)
        else:
            if captions:
                caption_note = f"{captions} (your file)"
                source = captions
            else:
                source = os.path.join(tmpdir, "auto_captions.ass")
                count = auto_captions(shots, base, renders, source)
                caption_note = f"automatic from text_on_screen ({count} shots)"
            if not captions and count == 0:
                caption_note = "none (no shot has text to burn)"
                os.replace(staged, out)
            else:
                result = burn_captions(staged, source, out, tmpdir)
                if result.returncode != 0 or not os.path.exists(out) \
                        or os.path.getsize(out) == 0:
                    fallback = os.path.splitext(out)[0] + ".nocaptions.mp4"
                    if os.path.exists(out):
                        os.remove(out)
                    os.replace(staged, fallback)
                    tail = " | ".join((result.stderr or "").strip().splitlines()[-4:])
                    die(f"Burning captions failed ({tail}). The video WITHOUT on-screen "
                        f"text is at {fallback} for inspection only; do not post it.")

        talking = [s for s in shots if s["type"] == "talking"]
        checks_dir = None
        if talking:
            checks_dir = os.path.abspath(args.checks_dir or
                                         os.path.splitext(out)[0] + "_checks")
            os.makedirs(checks_dir, exist_ok=True)
            for shot in talking:
                extract_check_frames(shot, renders, checks_dir)

        problems, v, length = verify(out, total)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    counts = {k: sum(1 for s in shots if s["type"] == k)
              for k in ("talking", "action", "photo", "outro")}
    print(f"Done: {out}")
    print(f"  shots: {len(shots)} (" + ", ".join(f"{k} {n}" for k, n in counts.items()) + ")")
    print(f"  length: {length:.2f}s (shot list total {total:.2f}s)")
    if v:
        print(f"  resolution: {v.get('width')}x{v.get('height')}")
    print("  music: " + (f"{music} at volume {music_volume:g}" if music else "none"))
    if placed:
        for sid, start, dur in placed:
            print(f"  voice {sid}: {start:.2f}s to {start + dur:.2f}s")
    else:
        print("  voice lines: none")
    print(f"  on-screen text: {caption_note}")
    if not captions and not args.no_captions:
        skipped = [s["id"] for s in shots if s["type"] == "outro" and not burns_text(s)]
        if skipped:
            print(f"    outro text not drawn ({', '.join(skipped)}): the outro image should "
                  "show it. Add \"burn_text\": true to the shot to draw it.")
    if checks_dir:
        print(f"  check frames: {checks_dir}")
        print("  NOTE: review the check frames for every talking clip before approving. "
              "A failed mouth-sync line cuts the line or holds the video; never ship it.")
    if problems:
        die("Verification FAILED: " + "; ".join(problems))
    print("Verification passed: 1080x1920, full length, audio throughout, decodes cleanly.")


if __name__ == "__main__":
    main()
