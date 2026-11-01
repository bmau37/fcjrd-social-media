#!/usr/bin/env python3
"""Render shots from a shot-list JSON through a local ComfyUI server.

Reads a shot list (see SKILL.md / shot-list.example.json), submits one
ComfyUI job per renderable shot (talking, action) using a user-supplied
API-format workflow template, polls until each job finishes, and saves
each clip as <out-dir>/<shot id>.mp4. Photo and outro shots are NOT
rendered; they are held images handled later by assemble_video.py.

Template tokens (literal text in the exported API-format workflow):
  __IMAGE__   required  uploaded reference image filename on the server
  __PROMPT__  required  the shot's prompt (JSON-escaped, so quotes are safe)
  __FRAMES__  required  frame count: duration_sec * fps, rounded UP to a
                        length the model accepts (--frame-step 4 gives
                        Wan's 4n+1, 8 gives LTX's 8n+1, 1 gives exact)
  __SEED__    optional  sampler seed. Without it, every render of a shot
                        reuses the template's seed and gives the same clip,
                        so a failed take cannot be re-rolled.
A number token written as a quoted string ("__FRAMES__", "__SEED__")
becomes a bare JSON number.

Seeds: --seed wins, then the shot's "seed" field, else a random seed.
The seed used is printed and saved in manifest.json, so a good take can
be reproduced by putting it in the shot's "seed" field.

Re-roll one failed take without touching the others:
  python3 render_shots.py ... --only s03 --seed 4242

Standard library only. ffmpeg/ffprobe are used, when installed, to check
clip length and to convert non-MP4 outputs (WebM, GIF, MOV) to MP4.
No API keys, no cloud, no accounts.

Usage:
  python3 render_shots.py --shot-list shot-list.json \\
      --template workflows/wan22-i2v-api.json \\
      --out-dir renders/my-video/ [--dry-run] [--fps 24] [--frame-step 4] \\
      [--only s03,s05] [--seed 1234] [--comfy-url http://127.0.0.1:8188]
"""

import argparse
import json
import math
import mimetypes
import os
import random
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

RENDERABLE_TYPES = ("talking", "action")
DEFAULT_FPS = 24
# Best first. Animated WebP is last: many ffmpeg builds cannot decode it.
VIDEO_EXTS = [".mp4", ".webm", ".mov", ".mkv", ".avi", ".gif", ".webp"]
# Where save nodes list their files: core Save Video / SaveWEBM / SaveAnimatedWEBP
# use "images", VideoHelperSuite's Video Combine uses "gifs".
OUTPUT_KEYS = ("videos", "gifs", "images", "video")


def die(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def warn(msg):
    print(f"WARNING: {msg}", file=sys.stderr)


def http_json(method, url, payload=None, timeout=30):
    req = urllib.request.Request(url, method=method)
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data=data, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:800]
        die(f"{method} {url} -> HTTP {e.code}: {body}")
    except urllib.error.URLError as e:
        die(f"Cannot reach ComfyUI at {url}: {e.reason}. "
            "Is ComfyUI running on this machine?")
    except ValueError as e:
        die(f"{method} {url} did not return JSON: {e}")


def http_get_file(url, dest, timeout=300):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            with open(dest, "wb") as f:
                shutil.copyfileobj(resp, f)
    except urllib.error.HTTPError as e:
        die(f"Download {url} -> HTTP {e.code}")
    except urllib.error.URLError as e:
        die(f"Download failed: {e.reason}")


def upload_image(comfy_url, path):
    """Upload an image to ComfyUI's input folder. Returns server filename."""
    boundary = uuid.uuid4().hex
    with open(path, "rb") as f:
        blob = f.read()
    filename = os.path.basename(path)
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode("utf-8") + blob + (
        f"\r\n--{boundary}\r\n"
        f'Content-Disposition: form-data; name="type"\r\n\r\ninput\r\n'
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="overwrite"\r\n\r\ntrue\r\n'
        f"--{boundary}--\r\n"
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{comfy_url}/upload/image", method="POST", data=body)
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        die(f"Image upload -> HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")
    except urllib.error.URLError as e:
        die(f"Image upload failed: {e.reason}")
    name = result.get("name", filename)
    if result.get("subfolder"):
        name = f"{result['subfolder']}/{name}"
    return name


def frames_for(duration_sec, fps, step):
    """Smallest frame count >= duration*fps that the model accepts (step*n + 1)."""
    exact = float(duration_sec) * fps
    if step <= 1:
        return max(1, int(math.ceil(exact - 1e-6)))
    n = math.ceil((exact - 1) / step - 1e-9)
    return max(1, int(n) * step + 1)


def json_text(value):
    """Text safe to drop inside a JSON string (quotes, backslashes, newlines)."""
    return json.dumps(str(value), ensure_ascii=False)[1:-1]


def fill_template(template_text, image_name, prompt, frames, seed):
    text = template_text
    for token, value in (("__FRAMES__", frames), ("__SEED__", seed)):
        if value is not None:
            text = text.replace(f'"{token}"', str(value)).replace(token, str(value))
    text = text.replace("__IMAGE__", json_text(image_name))
    return text.replace("__PROMPT__", json_text(prompt))


def find_media(outputs):
    """Files a finished ComfyUI job reports: (videos best-first, everything)."""
    files = []
    for node_out in (outputs or {}).values():
        if not isinstance(node_out, dict):
            continue
        for key in OUTPUT_KEYS:
            items = node_out.get(key)
            if isinstance(items, dict):
                items = [items]
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, dict) and item.get("filename"):
                    files.append(item)

    def rank(item):
        ext = os.path.splitext(item["filename"])[1].lower()
        return VIDEO_EXTS.index(ext) if ext in VIDEO_EXTS else len(VIDEO_EXTS)

    files.sort(key=rank)
    return [f for f in files if rank(f) < len(VIDEO_EXTS)], files


def view_url(comfy_url, item):
    query = urllib.parse.urlencode({
        "filename": item["filename"],
        "subfolder": item.get("subfolder", ""),
        "type": item.get("type", "output"),
    })
    return f"{comfy_url}/view?{query}"


def tail(text, n=4):
    return " | ".join((text or "").strip().splitlines()[-n:])


def clip_duration(path):
    if not shutil.which("ffprobe"):
        return None
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", path],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        return float(r.stdout.strip())
    except ValueError:
        return None


def save_clip(comfy_url, item, out_dir, sid):
    """Download the clip as <sid>.mp4, converting other containers with ffmpeg."""
    final = os.path.join(out_dir, f"{sid}.mp4")
    ext = os.path.splitext(item["filename"])[1].lower()
    if ext == ".mp4":
        http_get_file(view_url(comfy_url, item), final)
        return final
    source = os.path.join(out_dir, f"{sid}.source{ext}")
    http_get_file(view_url(comfy_url, item), source)
    if not shutil.which("ffmpeg"):
        die(f"Shot {sid}: ComfyUI saved {item['filename']}, not MP4, and ffmpeg is not "
            "installed to convert it. Install ffmpeg or save the clip as MP4.")
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", source,
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16",
                        "-preset", "medium", "-c:a", "aac", "-b:a", "192k", final],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or not os.path.exists(final) or os.path.getsize(final) == 0:
        die(f"Shot {sid}: could not convert {item['filename']} to MP4 ({tail(r.stderr)}). "
            "Save clips as MP4: core Save Video node, or Video Combine with "
            "format video/h264-mp4.")
    print(f"  converted {item['filename']} -> {final} (original kept as {source})")
    return final


def load_manifest(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return {e["id"]: e for e in data if isinstance(e, dict) and "id" in e}
    except (OSError, ValueError, TypeError):
        warn(f"{path} is unreadable; starting a new manifest.")
        return {}


def write_manifest(path, manifest, shots):
    order = [s["id"] for s in shots]
    entries = sorted(manifest.values(),
                     key=lambda e: order.index(e["id"]) if e["id"] in order else len(order))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)


def validate_shot_list(data, shot_list_path):
    if "shots" not in data or not isinstance(data["shots"], list):
        die(f"{shot_list_path}: missing or invalid 'shots' list")
    base = os.path.dirname(os.path.abspath(shot_list_path))
    seen_ids = set()
    for shot in data["shots"]:
        sid = shot.get("id", "?")
        if sid in seen_ids:
            die(f"Duplicate shot id: {sid}")
        seen_ids.add(sid)
        if shot.get("type") not in ("talking", "action", "photo", "outro"):
            die(f"Shot {sid}: unknown type {shot.get('type')!r}")
        try:
            duration = float(shot.get("duration_sec") or 0)
        except (TypeError, ValueError):
            duration = 0
        if duration <= 0:
            die(f"Shot {sid}: missing or invalid duration_sec")
        ref = shot.get("ref_image")
        if not ref:
            die(f"Shot {sid}: missing ref_image")
        full = ref if os.path.isabs(ref) else os.path.join(base, ref)
        if not os.path.exists(full):
            die(f"Shot {sid}: ref_image not found: {ref}")
        shot["_ref_full"] = full
        if shot["type"] == "talking":
            if not shot.get("dialogue"):
                die(f"Shot {sid}: talking shot needs a dialogue line")
            if not shot.get("voice_file"):
                warn(f"Shot {sid}: talking shot has no voice_file yet; "
                     "assemble_video.py will refuse it until it has one.")
        elif shot.get("voice_file"):
            die(f"Shot {sid}: only talking shots may have a voice_file "
                "(no dialogue over action, photos or the outro).")
        if shot["type"] in RENDERABLE_TYPES and not shot.get("prompt"):
            die(f"Shot {sid}: renderable shot needs a prompt")
        if "seed" in shot:
            try:
                shot["seed"] = int(shot["seed"])
            except (TypeError, ValueError):
                die(f"Shot {sid}: seed must be a whole number")
    return data


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--shot-list", required=True)
    ap.add_argument("--template", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--comfy-url", default="http://127.0.0.1:8188")
    ap.add_argument("--fps", type=int, default=DEFAULT_FPS,
                    help="Frame rate the workflow renders at (Wan 2.2 5B: 24, 14B: 16).")
    ap.add_argument("--frame-step", type=int, default=4,
                    help="Frames are rounded up to step*n+1: 4 for Wan, 8 for LTX, 1 for exact.")
    ap.add_argument("--only", help="Comma-separated shot ids to (re-)render; others are left alone.")
    ap.add_argument("--seed", type=int,
                    help="Seed for every shot rendered in this run (needs __SEED__ in the template).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print what would be submitted; do not call the server.")
    ap.add_argument("--poll-sec", type=int, default=10)
    ap.add_argument("--timeout-min", type=int, default=60)
    args = ap.parse_args()

    comfy_url = args.comfy_url.rstrip("/")
    with open(args.shot_list, encoding="utf-8") as f:
        data = validate_shot_list(json.load(f), args.shot_list)
    with open(args.template, encoding="utf-8") as f:
        template_text = f.read()
    for token in ("__IMAGE__", "__PROMPT__", "__FRAMES__"):
        if token not in template_text:
            die(f"Template {args.template} is missing token {token}. "
                "See SKILL.md: the template must contain all three tokens.")
    has_seed_token = "__SEED__" in template_text
    if not has_seed_token:
        warn("Template has no __SEED__ token, so every render of a shot reuses the "
             "template's seed and a failed take cannot be re-rolled. Put __SEED__ in "
             "the sampler's seed (noise_seed) field.")
        if args.seed is not None:
            warn("--seed has no effect without __SEED__ in the template.")

    renderable = [s for s in data["shots"] if s["type"] in RENDERABLE_TYPES]
    held = [s for s in data["shots"] if s["type"] not in RENDERABLE_TYPES]
    if args.only:
        wanted = [x.strip() for x in args.only.split(",") if x.strip()]
        known = {s["id"] for s in data["shots"]}
        unknown = [w for w in wanted if w not in known]
        if unknown:
            die(f"--only: no shot with id {', '.join(unknown)}")
        not_renderable = [w for w in wanted if w not in {s['id'] for s in renderable}]
        if not_renderable:
            warn(f"--only: {', '.join(not_renderable)} are photo/outro shots; nothing to render.")
        renderable = [s for s in renderable if s["id"] in wanted]
    print(f"{len(renderable)} shots to render, "
          f"{len(held)} held-image shots skipped (photo/outro).")

    plan = []
    for shot in renderable:
        frames = frames_for(shot["duration_sec"], args.fps, args.frame_step)
        seed = None
        if has_seed_token:
            if args.seed is not None:
                seed = args.seed
            elif "seed" in shot:
                seed = shot["seed"]
            elif not args.dry_run:
                seed = random.SystemRandom().randrange(0, 2 ** 32)
        plan.append((shot, frames, seed))

    if args.dry_run:
        for shot, frames, seed in plan:
            seed_note = "random" if (has_seed_token and seed is None) else seed
            print(f"[dry-run] {shot['id']} ({shot['type']}, {shot['duration_sec']}s, "
                  f"{frames} frames at {args.fps} fps, seed {seed_note}): "
                  f"ref={shot['ref_image']}")
            print(f"          prompt: {shot['prompt'][:120]}")
            try:
                json.loads(fill_template(template_text, "ref.png", shot["prompt"], frames,
                                         seed if seed is not None else 0))
            except json.JSONDecodeError as e:
                die(f"Shot {shot['id']}: template is not valid JSON after token "
                    f"replacement: {e}")
        print("Dry run OK (template parses for every shot). No server calls made.")
        return

    # One cheap reachability check before any real work.
    http_json("GET", f"{comfy_url}/system_stats", timeout=15)

    os.makedirs(args.out_dir, exist_ok=True)
    manifest_path = os.path.join(args.out_dir, "manifest.json")
    manifest = load_manifest(manifest_path)
    for shot, frames, seed in plan:
        sid = shot["id"]
        print(f"Uploading ref for {sid} ...")
        server_image = upload_image(comfy_url, shot["_ref_full"])
        workflow_text = fill_template(template_text, server_image, shot["prompt"], frames, seed)
        try:
            workflow = json.loads(workflow_text)
        except json.JSONDecodeError as e:
            die(f"Shot {sid}: template broke after token replacement: {e}")
        client_id = f"fcjrd-{uuid.uuid4().hex[:12]}"
        result = http_json("POST", f"{comfy_url}/prompt",
                           {"prompt": workflow, "client_id": client_id})
        prompt_id = result.get("prompt_id")
        if not prompt_id:
            die(f"Shot {sid}: ComfyUI did not return a prompt_id: {result}")
        seed_note = f", seed {seed}" if seed is not None else ""
        print(f"Shot {sid} queued as {prompt_id} ({frames} frames{seed_note}), waiting ...")
        deadline = time.time() + args.timeout_min * 60
        clip_path = None
        while time.time() < deadline:
            hist = http_json("GET", f"{comfy_url}/history/{prompt_id}")
            entry = hist.get(prompt_id) or {}
            status = (entry.get("status") or {}).get("status_str")
            if status == "error":
                die(f"Shot {sid}: ComfyUI job failed: "
                    f"{json.dumps(entry.get('status', {}))[:400]}")
            videos, files = find_media(entry.get("outputs"))
            if videos:
                clip_path = save_clip(comfy_url, videos[0], args.out_dir, sid)
                break
            if entry and (status == "success" or (entry.get("status") or {}).get("completed")):
                names = ", ".join(f["filename"] for f in files) or "nothing"
                die(f"Shot {sid}: ComfyUI finished but saved no video (outputs: {names}). "
                    "End the workflow with a video save node that writes MP4 "
                    "(core Save Video, or Video Combine with format video/h264-mp4).")
            time.sleep(args.poll_sec)
        if clip_path is None:
            die(f"Shot {sid}: timed out after {args.timeout_min} min. "
                "Check the ComfyUI queue; do not re-submit blindly.")
        length = clip_duration(clip_path)
        if length is not None and length < float(shot["duration_sec"]) - 0.05:
            warn(f"Shot {sid}: clip is {length:.2f}s but the shot is {shot['duration_sec']}s; "
                 "assemble_video.py will refuse it. Check --fps against the workflow's "
                 "frame rate.")
        print(f"Shot {sid} done -> {clip_path}")
        manifest[sid] = {"id": sid, "type": shot["type"],
                         "clip": os.path.abspath(clip_path),
                         "dialogue": shot.get("dialogue", ""),
                         "duration_sec": shot["duration_sec"],
                         "frames": frames, "fps": args.fps, "seed": seed,
                         "prompt_id": prompt_id}
        write_manifest(manifest_path, manifest, data["shots"])

    print(f"All requested clips rendered. Manifest: {manifest_path}")
    print("Next: frame-check every talking clip, then assemble_video.py.")


if __name__ == "__main__":
    main()
