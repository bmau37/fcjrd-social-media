#!/usr/bin/env python3
"""Build the source files for the "Real jobs: junk removal + demolition" video.

Run:  python3 build_sources.py
Writes next to this file:
  refs/outro-junk-and-demo.png   1080x1920 outro: logo + locked CTA facts
  captions.ass                   on-screen text for every photo shot (fonts embedded)
  music/junk-and-demo-bed.m4a    original music bed, synthesized here (120 BPM, 30 s)
  shot-list.json                 shot list in the FCJRD ComfyUI video skill format

The video itself is assembled by the skill's own scripts/assemble_video.py
(see README.md). Every shot is a real job photo or the outro, so nothing
needs ComfyUI or a GPU, and nothing in the video speaks.

Needs Python 3 with Pillow + numpy, and ffmpeg on PATH.
"""

import json
import math
import os
import struct
import subprocess
import sys
import wave

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
W, H = 1080, 1920

TEAL = (13, 59, 59)        # the skill's DARK_TEAL (#0d3b3b) photo padding
ORANGE = (241, 106, 29)    # sampled from the blitz clips' header boxes
NAVY = (12, 27, 59)        # sampled from the blitz clips' pills
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)

ANTON = os.path.join(HERE, "fonts", "Anton-Regular-subset.ttf")
MONT = os.path.join(HERE, "fonts", "Montserrat-ExtraBold-subset.ttf")
LOGO = os.path.join(HERE, "refs", "logo-with-cat.jpg")

# Locked business facts (SKILL.md). Never add anything beyond these.
BUSINESS = "Fort Collins Junk Removal and Demo"
PHONE = "970-694-7523"
SINCE = "2015"
WEBSITE = "fortcollinsjunkremovalanddemo.com"

IMG_DIR = "../../img/"     # repo img/ folder, relative to this file
SHOT_SEC = 3.0             # 6 beats at 120 BPM
OUTRO_SEC = 6.0            # 3 bars
OUTRO_PNG = "refs/outro-junk-and-demo.png"
MUSIC_M4A = "music/junk-and-demo-bed.m4a"

# Photo shots in order. caption: list of lines, each a list of
# (text, highlighted) segments. Lines reuse copy from the approved blitz clips.
SHOTS = [
    dict(id="s01", section="JUNK REMOVAL",
         image="fcjrd-original-junk-removal-and-yard-clean-up-in-fort-collins-before-and-after-f40c4590faa2.jpg",
         caption=[[("GOT A PILE", False)], [("OF JUNK?", True)]]),
    dict(id="s02", section="JUNK REMOVAL",
         image="fcjrd-approved-garage-clean-out-ce30a416ff7f.jpg",
         caption=[[("WE ", False), ("LOAD", True), (" IT.", False)]]),
    dict(id="s03", section="JUNK REMOVAL",
         image="fcjrd-original-furniture-junk-removal-in-fort-collins-before-and-after-70dbfc351d04.jpg",
         caption=[[("WE ", False), ("HAUL", True), (" IT.", False)]]),
    dict(id="s04", section="JUNK REMOVAL",
         image="fcjrd-approved-couch-junk-before-after-d18058513ff6.jpg",
         caption=[[("YOU ", False), ("RELAX.", True)]]),
    dict(id="s05", section="DEMOLITION",
         image="fcjrd-approved-kitchen-demolition-before-and-after-27bedf30fd32.jpg",
         caption=[[("THAT OLD KITCHEN", False)], [("HAS TO GO?", True)]]),
    dict(id="s06", section="DEMOLITION",
         image="fcjrd-original-berthoud-playhouse-demolition-before-and-after-b8f7f9796f04.jpg",
         caption=[[("WE ", False), ("TEAR", True), (" IT DOWN.", False)]]),
    dict(id="s07", section="DEMOLITION",
         image="fcjrd-original-wheelchair-ramp-demolition-in-fort-collins-before-and-after-d287585db414.jpg",
         caption=[[("WE ", False), ("HAUL", True), (" IT OFF.", False)]]),
    dict(id="s08", section="DEMOLITION",
         image="fcjrd-approved-shed-before-and-after-d0bec07749f6.jpg",
         caption=[[("SITE LEFT ", False), ("CLEAN.", True)]]),
]
PILL_TEXT = "REAL JOB • BEFORE & AFTER"


# ---------------------------------------------------------------- fonts

class Font:
    """Font metrics in the units libass uses.

    libass sizes a font so usWinAscent + usWinDescent == \\fs pixels, and
    centers lines (\\an5) on that box. Pillow (with raqm/HarfBuzz, like
    libass) measures widths at the matching em size.
    """

    def __init__(self, path, family):
        self.path, self.family = path, family
        with open(path, "rb") as f:
            data = f.read()
        tables = {}
        for i in range(struct.unpack(">H", data[4:6])[0]):
            tag, _, off, _ = struct.unpack(">4sIII", data[12 + 16 * i:28 + 16 * i])
            tables[tag.decode("latin-1")] = off
        head, os2 = tables["head"], tables["OS/2"]
        self.upem = struct.unpack(">H", data[head + 18:head + 20])[0]
        self.win_asc, self.win_desc = struct.unpack(">HH", data[os2 + 74:os2 + 78])
        self.cap = struct.unpack(">h", data[os2 + 88:os2 + 90])[0]
        self.win_h = self.win_asc + self.win_desc
        self._ref = ImageFont.truetype(path, 1000, layout_engine=ImageFont.Layout.RAQM)
        self.data = data

    def fs_for_cap(self, cap_px):
        return cap_px * self.win_h / self.cap

    def em_for_cap(self, cap_px):
        return cap_px * self.upem / self.cap

    def pil(self, cap_px):
        return ImageFont.truetype(self.path, max(1, int(round(self.em_for_cap(cap_px)))),
                                  layout_engine=ImageFont.Layout.RAQM)

    def ink(self, text, cap_px):
        """(advance, ink_left, ink_right) in px at the given cap height."""
        k = self.em_for_cap(cap_px) / 1000.0
        left, _, right, _ = self._ref.getbbox(text)
        return self._ref.getlength(text) * k, left * k, right * k

    def an5_y_for_cap_center(self, cy, fs):
        """\\pos y that puts the caps' ink center at cy with \\an5."""
        return cy - (self.win_asc / self.win_h - 0.5 - self.cap / self.win_h / 2) * fs


# ---------------------------------------------------------------- outro

def rounded_mask(size, radius):
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius, fill=255)
    return m


def draw_line(draw, cx, cy, segments, font, cap_px, spacing=0):
    """Draw (text, color) segments centered on cx, caps' ink centered on cy."""
    f = font.pil(cap_px)
    chars = [(ch, col) for text, col in segments for ch in text] if spacing else None
    if spacing:
        widths = [f.getlength(ch) for ch, _ in chars]
        total = sum(widths) + spacing * (len(chars) - 1)
    else:
        widths = [f.getlength(text) for text, _ in segments]
        total = sum(widths)
    x = cx - total / 2
    baseline = cy + cap_px / 2
    for (text, col), w in zip(chars or segments, widths):
        draw.text((x, baseline), text, font=f, fill=col, anchor="ls")
        x += w + spacing


def pill(draw, cx, cy, text, font, cap_px, fill, text_fill, pad_x, h, spacing=0):
    f = font.pil(cap_px)
    if spacing:
        width = sum(f.getlength(c) for c in text) + spacing * (len(text) - 1)
    else:
        width = f.getlength(text)
    w = width + 2 * pad_x
    draw.rounded_rectangle((cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2), h / 2, fill=fill)
    draw_line(draw, cx, cy, [(text, text_fill)], font, cap_px, spacing)


def build_outro(path, anton, mont):
    img = Image.new("RGB", (W, H), TEAL)

    # Logo tile with a soft shadow. The JPG has black corners outside its
    # own rounded tile (corner radius ~14% of the width), so trim the edge
    # and mask with a slightly larger radius.
    size, top = 470, 205
    src = Image.open(LOGO).convert("RGB")
    inset = max(2, src.width // 400)
    src = src.crop((inset, inset, src.width - inset, src.height - inset))
    logo = src.resize((size, size), Image.LANCZOS)
    mask = rounded_mask((size, size), int(size * 0.152))
    shadow = Image.new("L", (W, H), 0)
    sx = (W - size) // 2
    shadow.paste(mask, (sx, top + 16))
    shadow = shadow.filter(ImageFilter.GaussianBlur(20))
    img = Image.composite(Image.new("RGB", (W, H), (4, 22, 22)), img, shadow.point(lambda v: int(v * 0.7)))
    img.paste(logo, (sx, top), mask)

    d = ImageDraw.Draw(img)
    draw_line(d, W / 2, 752, [("CALL OR TEXT", WHITE)], mont, 31, spacing=7)
    draw_line(d, W / 2, 868, [(PHONE, ORANGE)], anton, 128)
    draw_line(d, W / 2, 1000, [("Text us 3 to 6 photos", WHITE)], mont, 40)
    draw_line(d, W / 2, 1066, [("for a ", WHITE), ("FREE estimate.", ORANGE)], mont, 40)
    draw_line(d, W / 2, 1140, [("or send us a message", (205, 222, 222))], mont, 28)
    pill(d, W / 2, 1245, WEBSITE, mont, 25, WHITE, TEAL, pad_x=34, h=74)
    pill(d, W / 2, 1345, "SINCE " + SINCE, mont, 25, ORANGE, WHITE, pad_x=30, h=62, spacing=4)
    d.rectangle((0, H - 36, W, H), fill=ORANGE)
    img.save(path, optimize=True)


# ---------------------------------------------------------------- captions (.ass)

def ass_time(t):
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def ass_rgb(rgb):
    r, g, b = rgb
    return f"&H{b:02X}{g:02X}{r:02X}&"


def ass_embed_font(name, data):
    """SSA/ASS [Fonts] encoding: 6-bit groups + 33, 80-char lines."""
    out = []
    i, n = 0, len(data)
    while i + 3 <= n:
        b0, b1, b2 = data[i], data[i + 1], data[i + 2]
        out += [b0 >> 2, ((b0 & 3) << 4) | (b1 >> 4), ((b1 & 15) << 2) | (b2 >> 6), b2 & 63]
        i += 3
    if n - i == 1:
        out += [data[i] >> 2, (data[i] & 3) << 4]
    elif n - i == 2:
        out += [data[i] >> 2, ((data[i] & 3) << 4) | (data[i + 1] >> 4), (data[i + 1] & 15) << 2]
    text = "".join(chr(v + 33) for v in out)
    return [f"fontname: {name}"] + [text[j:j + 80] for j in range(0, len(text), 80)]


def rrect(w, h, r):
    """ASS vector drawing of a w x h rounded rectangle (origin top-left)."""
    c = r * 0.5523
    p = [f"m {r:.1f} 0", f"l {w - r:.1f} 0",
         f"b {w - r + c:.1f} 0 {w:.1f} {r - c:.1f} {w:.1f} {r:.1f}", f"l {w:.1f} {h - r:.1f}",
         f"b {w:.1f} {h - r + c:.1f} {w - r + c:.1f} {h:.1f} {w - r:.1f} {h:.1f}", f"l {r:.1f} {h:.1f}",
         f"b {r - c:.1f} {h:.1f} 0 {h - r + c:.1f} 0 {h - r:.1f}", f"l 0 {r:.1f}",
         f"b 0 {r - c:.1f} {r - c:.1f} 0 {r:.1f} 0"]
    return " ".join(p)


def pop(delay_ms=0, start=45, peak=110):
    a, b, c = delay_ms, delay_ms + 150, delay_ms + 240
    return (f"\\fscx{start}\\fscy{start}\\t({a},{b},\\fscx{peak}\\fscy{peak})"
            f"\\t({b},{c},\\fscx100\\fscy100)")


def boxed_label(events, t0, t1, layer, cx, cy, text, font, cap_px, box_rgb, text_rgb,
                pad_x, pad_y, radius, rot=0.0, delay_ms=0, fade_ms=90):
    fs = font.fs_for_cap(cap_px)
    adv, ink_l, ink_r = font.ink(text, cap_px)
    w = (ink_r - ink_l) + 2 * pad_x
    h = cap_px + 2 * pad_y
    # \an5 centers the advance box; shift so the ink is centered instead.
    tx = cx - ((ink_l + ink_r) / 2 - adv / 2)
    ty = font.an5_y_for_cap_center(cy, fs)
    common = f"\\org({cx:.1f},{cy:.1f})\\frz{rot}\\fad({fade_ms},0){pop(delay_ms)}"
    alpha_in = f"\\alpha&HFF&\\t({delay_ms},{delay_ms + 1},\\alpha&H00&)" if delay_ms else ""
    events.append((layer, t0, t1,
                   f"{{\\an5\\pos({cx:.1f},{cy:.1f})\\bord0\\shad0\\c{ass_rgb(box_rgb)}"
                   f"{common}{alpha_in}\\p1}}{rrect(w, h, radius)}{{\\p0}}"))
    events.append((layer + 1, t0, t1,
                   f"{{\\an5\\pos({tx:.1f},{ty:.1f})\\fn{font.family}\\fs{fs:.1f}\\bord0\\shad0"
                   f"\\c{ass_rgb(text_rgb)}{common}{alpha_in}}}{text}"))


def build_captions(path, anton, mont, photo_bottoms, photo_tops):
    events = []
    t_photos_end = SHOT_SEC * len(SHOTS)

    # Small brand line, top safe area, for every photo shot.
    fs = mont.fs_for_cap(21)
    events.append((0, 0.0, t_photos_end,
                   f"{{\\an5\\pos(540,{mont.an5_y_for_cap_center(276, fs):.1f})\\fn{mont.family}"
                   f"\\fs{fs:.1f}\\fsp5\\bord0\\shad0\\c{ass_rgb(WHITE)}\\1a&H30&\\fad(250,0)}}"
                   f"{BUSINESS.upper()}"))

    # Section header + REAL JOB pill (pop in when the section starts).
    sections = []
    for i, shot in enumerate(SHOTS):
        if not sections or sections[-1][0] != shot["section"]:
            sections.append([shot["section"], i, i])
        sections[-1][2] = i
    for name, first, last in sections:
        t0, t1 = first * SHOT_SEC, (last + 1) * SHOT_SEC
        top = min(photo_tops[first:last + 1])
        boxed_label(events, t0, t1, 2, 540, top - 232, name, anton, 100, ORANGE, WHITE,
                    pad_x=46, pad_y=34, radius=14, rot=2.0)
        boxed_label(events, t0, t1, 4, 540, top - 84, PILL_TEXT, mont, 26, NAVY, WHITE,
                    pad_x=28, pad_y=17, radius=30, delay_ms=140)

    # Big caption under each photo (one event per line so spacing is exact).
    cap = 92
    fs = anton.fs_for_cap(cap)
    for i, shot in enumerate(SHOTS):
        t0, t1 = i * SHOT_SEC, (i + 1) * SHOT_SEC
        cy = photo_bottoms[i] + 46 + cap / 2
        for j, line in enumerate(shot["caption"]):
            text = "".join(s for s, _ in line)
            adv, ink_l, ink_r = anton.ink(text, cap)
            tx = 540 - ((ink_l + ink_r) / 2 - adv / 2)
            ty = anton.an5_y_for_cap_center(cy + j * (cap + 32), fs)
            body = "".join(f"{{\\c{ass_rgb(ORANGE if hi else WHITE)}}}{s}" for s, hi in line)
            delay = 120 * j
            alpha_in = f"\\alpha&HFF&\\t({delay},{delay + 1},\\alpha&H00&)" if delay else ""
            events.append((6, t0, t1,
                           f"{{\\an5\\pos({tx:.1f},{ty:.1f})\\fn{anton.family}\\fs{fs:.1f}"
                           f"\\bord7\\3c{ass_rgb(BLACK)}\\shad5\\4c{ass_rgb(BLACK)}\\4a&H70&"
                           f"\\fad(80,0){pop(delay, 40, 112)}{alpha_in}}}{body}"))

    lines = [
        "[Script Info]",
        "; On-screen text for the real-jobs junk removal + demolition video.",
        "; Generated by build_sources.py. Fonts are embedded below (SIL OFL 1.1).",
        "ScriptType: v4.00+",
        f"PlayResX: {W}",
        f"PlayResY: {H}",
        "ScaledBorderAndShadow: yes",
        "WrapStyle: 2",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{anton.family},100,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,"
        "0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1",
        "",
        "[Fonts]",
    ]
    lines += ass_embed_font("Anton-Regular-subset_0.ttf", anton.data)
    lines += ass_embed_font("Montserrat-ExtraBold-subset_0.ttf", mont.data)
    lines += ["", "[Events]",
              "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    for layer, t0, t1, text in events:
        lines.append(f"Dialogue: {layer},{ass_time(t0)},{ass_time(t1)},Default,,0,0,0,,{text}")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------- music

SR = 44100
BEAT = 0.5          # 120 BPM
BAR = 4 * BEAT


def tvec(dur):
    return np.arange(int(round(dur * SR))) / SR


def place(buf, t0, sig, gain=1.0, pan=0.0):
    i0 = int(round(t0 * SR))
    if i0 < 0 or i0 >= buf.shape[1]:
        return
    n = min(len(sig), buf.shape[1] - i0)
    a = (pan + 1) * math.pi / 4
    buf[0, i0:i0 + n] += sig[:n] * gain * math.cos(a) * math.sqrt(2)
    buf[1, i0:i0 + n] += sig[:n] * gain * math.sin(a) * math.sqrt(2)


def filt(x, lo=None, hi=None):
    """Zero-phase FFT filter with ~24 dB/oct skirts."""
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / SR)
    g = np.ones_like(f)
    if lo:
        g /= 1 + (lo / np.maximum(f, 1e-3)) ** 4
    if hi:
        g /= 1 + (f / hi) ** 4
    return np.fft.irfft(spec * g, len(x))


def presence(x, corner_hz, gain_db):
    """High shelf: +gain_db above corner_hz (zero-phase, FFT)."""
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / SR)
    g = 1 + (10 ** (gain_db / 20) - 1) / (1 + (corner_hz / np.maximum(f, 1e-3)) ** 2)
    return np.fft.irfft(spec * g, len(x))


def norm(x):
    return x / (np.max(np.abs(x)) + 1e-12)


def tail_fade(x, sec=0.012):
    n = min(len(x), int(sec * SR))
    x[-n:] *= np.linspace(1, 0, n)
    return x


def hz(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)


class Synth:
    def __init__(self, seed=2015):
        self.rng = np.random.default_rng(seed)

    def noise(self, n):
        return self.rng.standard_normal(n)

    def kick(self):
        t = tvec(0.42)
        f = 46 + 104 * np.exp(-t / 0.032)
        body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.14)
        click = filt(self.noise(len(t)), lo=1500) * np.exp(-t / 0.004) * 0.35
        return tail_fade(norm(np.tanh(1.8 * (body + click))), 0.03)

    def clap(self):
        t = tvec(0.32)
        n = filt(self.noise(len(t)), lo=900, hi=9000)
        env = np.zeros_like(t)
        for d in (0.0, 0.010, 0.021):
            env += (t >= d) * np.exp(-np.clip(t - d, 0, None) / 0.006) * 0.55
        env += (t >= 0.028) * np.exp(-np.clip(t - 0.028, 0, None) / 0.085)
        return tail_fade(norm(n * env))

    def snare(self):
        t = tvec(0.2)
        tone = np.sin(2 * np.pi * 190 * t) * np.exp(-t / 0.05)
        n = filt(self.noise(len(t)), lo=1200, hi=7000) * np.exp(-t / 0.06)
        return tail_fade(norm(0.6 * tone + n))

    def hat(self, open_=False):
        t = tvec(0.30 if open_ else 0.08)
        n = filt(self.noise(len(t)), lo=7500)
        return tail_fade(norm(n * np.exp(-t / (0.085 if open_ else 0.016))))

    def crash(self):
        t = tvec(2.4)
        n = filt(self.noise(len(t)), lo=3500, hi=14000)
        return tail_fade(norm(n * np.exp(-t / 0.8) * np.minimum(1, t / 0.002)), 0.2)

    def impact(self):
        t = tvec(1.4)
        f = 38 + 70 * np.exp(-t / 0.06)
        s = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.45)
        s += filt(self.noise(len(t)), hi=900) * np.exp(-t / 0.08) * 0.35
        return tail_fade(norm(np.tanh(1.5 * s)), 0.1)

    def sweep(self, dur, f0, f1, curve=2.2):
        """Rising filtered-noise whoosh that ends at full level."""
        t = tvec(dur)
        n = self.noise(len(t))
        centers = np.geomspace(f0, f1, 7)
        pos = t / dur * (len(centers) - 1)
        out = np.zeros_like(t)
        for i, c in enumerate(centers):
            out += filt(n, lo=c / 1.5, hi=c * 1.5) * np.clip(1 - np.abs(pos - i), 0, 1)
        env = (t / dur) ** curve
        env[-int(0.02 * SR):] *= np.linspace(1, 0, int(0.02 * SR))
        return norm(out * env)

    def riser(self, dur):
        t = tvec(dur)
        tone = np.sin(2 * np.pi * np.cumsum(220 * 2 ** (2 * t / dur)) / SR) * (t / dur) ** 2
        return norm(self.sweep(dur, 300, 8000, 1.5) + 0.35 * tone)

    def pluck(self, freq, dur, decay=0.3, hdecay=0.15, nh=24, attack=0.003):
        t = tvec(dur)
        s = np.zeros_like(t)
        for k in range(1, max(1, min(nh, int((SR / 2 - 500) / freq))) + 1):
            tau = decay / (1 + hdecay * (k - 1))
            s += np.sin(2 * np.pi * k * freq * t + self.rng.uniform(0, 2 * np.pi)) / k * np.exp(-t / tau)
        return tail_fade(s * np.minimum(1, t / attack))

    def mallet(self, freq, dur=0.7):
        t = tvec(dur)
        s = np.sin(2 * np.pi * freq * t) * np.exp(-t / 0.32)
        s += 0.22 * np.sin(2 * np.pi * freq * 4.0 * t) * np.exp(-t / 0.05)
        s += 0.06 * np.sin(2 * np.pi * freq * 9.9 * t) * np.exp(-t / 0.018)
        return tail_fade(s * np.minimum(1, t / 0.002))

    def pad(self, freqs, dur):
        t = tvec(dur)
        s = np.zeros_like(t)
        for f in freqs:
            for cents in (-7, 7):
                ff = f * 2 ** (cents / 1200)
                for k in range(1, 9):
                    s += np.sin(2 * np.pi * k * ff * t + self.rng.uniform(0, 2 * np.pi)) / k ** 1.3
        env = np.minimum(1, t / 0.35) * np.minimum(1, (dur - t) / 0.35)
        return norm(s * env)


# Chords as (bass root, three chord tones), one per bar.
CHORDS = {
    "C": (36, (60, 64, 67)), "G": (43, (59, 62, 67)), "Am": (45, (57, 60, 64)),
    "F": (41, (57, 60, 65)), "Em": (40, (55, 59, 64)),
}
# Bars 0-5 junk section, 6-11 demolition section, 12-14 outro.
PROGRESSION = ["C", "G", "Am", "F", "C", "G",
               "F", "G", "Em", "Am", "F", "G",
               "C", "G", "C"]


def build_music(wav_path, total_sec, cut_times, section_starts, outro_start):
    syn = Synth()
    n = int(round(total_sec * SR))
    drums, bass, keys, fx = (np.zeros((2, n)) for _ in range(4))
    kick, clap, snare = syn.kick(), syn.clap(), syn.snare()
    hat_c, hat_o, crash, impact = syn.hat(), syn.hat(True), syn.crash(), syn.impact()
    final_bar = len(PROGRESSION) - 1
    groove_end = final_bar * BAR            # last bar is a single final hit

    for bar, name in enumerate(PROGRESSION):
        t_bar = bar * BAR
        root, tones = CHORDS[name]
        section_b = 6 <= bar <= 11
        if bar == final_bar:
            place(drums, t_bar, kick, 0.72)
            place(bass, t_bar, syn.pluck(hz(root), 1.9, decay=0.6, hdecay=0.25, nh=16), 0.30)
            for k, m in enumerate(tones):
                for det, pan in ((-6, -0.5), (6, 0.5)):
                    place(keys, t_bar, syn.pluck(hz(m) * 2 ** (det / 1200), 1.9, decay=0.55,
                                                 hdecay=0.08, nh=30), 0.10, pan)
            place(keys, t_bar, syn.mallet(hz(tones[0] + 12), 1.9), 0.16, 0.2)
            continue
        for beat in range(4):
            tb = t_bar + beat * BEAT
            place(drums, tb, kick, 0.72)
            if beat in (1, 3):
                place(drums, tb, clap, 0.45)
            place(drums, tb + BEAT / 2, hat_c, 0.24, 0.25)
            if section_b:
                place(drums, tb + BEAT / 4, hat_c, 0.11, 0.3)
                place(drums, tb + 3 * BEAT / 4, hat_c, 0.11, 0.3)
            # Bass: root on the beat, octave on the off-beat.
            place(bass, tb, syn.pluck(hz(root), 0.22, decay=0.35, hdecay=0.16, nh=16), 0.30)
            place(bass, tb + BEAT / 2, syn.pluck(hz(root + 12), 0.2, decay=0.3, hdecay=0.16, nh=16), 0.22)
            # Off-beat chord stabs (house style), detuned pair for width.
            for m in tones:
                for det, pan in ((-6, -0.5), (6, 0.5)):
                    place(keys, tb + BEAT / 2,
                          syn.pluck(hz(m) * 2 ** (det / 1200), 0.24, decay=0.16, hdecay=0.08, nh=30),
                          0.11, pan)
        if section_b:
            place(drums, t_bar + 3.5 * BEAT, hat_o, 0.18, 0.3)
            place(keys, t_bar, syn.pad([hz(m) for m in tones], BAR + 0.05), 0.07)
        # Mallet arpeggio: 3rd, 5th, root+12, 5th, 3rd (8th-note grid).
        third, fifth, rootn = tones[1], tones[2], tones[0]
        pattern = [third, None, fifth, None, rootn + 12, fifth, third, None]
        if bar % 2 == 1:
            pattern[7] = fifth
        for step, m in enumerate(pattern):
            if m is not None:
                place(keys, t_bar + step * BEAT / 2, syn.mallet(hz(m + 12)), 0.24, 0.2)

    # Snare fill into each new section and into the outro.
    for t_hit in list(section_starts[1:]) + [outro_start]:
        for k in range(4):
            place(drums, t_hit - BEAT + k * BEAT / 4, snare, 0.10 + 0.06 * k)

    # FX: hits on section starts / outro, whoosh into every other cut.
    hits = sorted(set([0.0] + list(section_starts) + [outro_start, groove_end]))
    for t_hit in hits:
        place(fx, t_hit, crash, 0.16 if t_hit else 0.11)
        place(fx, t_hit, impact, 0.45 if t_hit else 0.3)
    for t_cut in cut_times:
        if t_cut not in hits:
            sw = syn.sweep(0.42, 600, 7000)
            place(fx, t_cut - len(sw) / SR, sw, 0.16)
    for t_sec in list(section_starts[1:]) + [outro_start]:
        r = syn.riser(1.5)
        place(fx, t_sec - len(r) / SR, r, 0.15)

    # Sidechain-style pump on the keys while the four-on-the-floor runs.
    t = np.arange(n) / SR
    since_beat = np.mod(t, BEAT)
    duck = 1 - 0.45 * np.exp(-since_beat / 0.11)
    duck[t >= groove_end] = 1.0
    keys *= duck

    mix = drums + bass + keys + fx
    mix = np.vstack([presence(filt(ch, lo=28), 2500, 3.0) for ch in mix])
    mix = np.tanh(1.15 * mix / np.max(np.abs(mix))) / np.tanh(1.15)
    fade = int(0.9 * SR)
    mix[:, -fade:] *= np.linspace(1, 0, fade) ** 1.5
    pcm = np.clip(mix.T * 0.89, -1, 1)
    with wave.open(wav_path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((pcm * 32767).astype("<i2").tobytes())


def loudnorm_to_aac(wav_path, out_path, target_i=-14.0, target_tp=-1.5):
    """Two-pass EBU R128 loudness normalization, then AAC 192k."""
    base = f"loudnorm=I={target_i}:TP={target_tp}:LRA=11"
    probe = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", wav_path, "-af",
                            base + ":print_format=json", "-f", "null", "-"],
                           capture_output=True, text=True, check=True)
    stats = json.loads(probe.stderr[probe.stderr.rindex("{"):probe.stderr.rindex("}") + 1])
    second = (f"{base}:measured_I={stats['input_i']}:measured_TP={stats['input_tp']}"
              f":measured_LRA={stats['input_lra']}:measured_thresh={stats['input_thresh']}"
              f":offset={stats['target_offset']}:linear=true")
    subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", "-i", wav_path, "-af", second,
                    "-ar", str(SR), "-c:a", "aac", "-b:a", "192k", out_path], check=True)


# ---------------------------------------------------------------- shot list

def scaled_photo_box(path):
    """Where assemble_video.py puts a photo: scale to fit 1080x1920, centered."""
    with Image.open(path) as im:
        iw, ih = im.size
    if iw * H >= ih * W:
        w, h = W, int(W * ih / iw + 0.5)
    else:
        w, h = int(H * iw / ih + 0.5), H
    top = (H - h) // 2
    return top, top + h


def main():
    anton = Font(ANTON, "Anton")
    mont = Font(MONT, "Montserrat ExtraBold")
    os.makedirs(os.path.join(HERE, "refs"), exist_ok=True)
    os.makedirs(os.path.join(HERE, "music"), exist_ok=True)

    tops, bottoms = [], []
    for shot in SHOTS:
        img_path = os.path.normpath(os.path.join(HERE, IMG_DIR, shot["image"]))
        if not os.path.exists(img_path):
            sys.exit(f"ERROR: photo for {shot['id']} not found: {img_path}")
        top, bottom = scaled_photo_box(img_path)
        tops.append(top)
        bottoms.append(bottom)

    build_outro(os.path.join(HERE, OUTRO_PNG), anton, mont)
    print("outro  ->", OUTRO_PNG)
    build_captions(os.path.join(HERE, "captions.ass"), anton, mont, bottoms, tops)
    print("captions -> captions.ass")

    photos_end = SHOT_SEC * len(SHOTS)
    total = photos_end + OUTRO_SEC
    cut_times = [SHOT_SEC * i for i in range(1, len(SHOTS) + 1)]
    section_starts = [i * SHOT_SEC for i, s in enumerate(SHOTS)
                      if i == 0 or s["section"] != SHOTS[i - 1]["section"]]
    wav_path = os.path.join(HERE, "music", "junk-and-demo-bed.wav")
    build_music(wav_path, total, cut_times, section_starts, photos_end)
    loudnorm_to_aac(wav_path, os.path.join(HERE, MUSIC_M4A))
    os.remove(wav_path)
    print("music  ->", MUSIC_M4A)

    shots = []
    for shot in SHOTS:
        shots.append({
            "id": shot["id"],
            "type": "photo",
            "ref_image": IMG_DIR + shot["image"],
            "duration_sec": SHOT_SEC,
            "header": shot["section"],
            "text_on_screen": " ".join("".join(s for s, _ in line) for line in shot["caption"]),
            "sound": "music only",
        })
    shots.append({
        "id": f"s{len(SHOTS) + 1:02d}",
        "type": "outro",
        "ref_image": OUTRO_PNG,
        "duration_sec": OUTRO_SEC,
        "text_on_screen": f"{BUSINESS} — since {SINCE} — {PHONE}",
        "sound": "music only",
    })
    shot_list = {
        "video": {
            "title": "Real jobs: junk removal + demolition (30s)",
            "track": "REAL JOBS (no character, no voice)",
            "length_sec": total,
            "music": MUSIC_M4A,
            "music_volume": 1.0,
        },
        "shots": shots,
    }
    with open(os.path.join(HERE, "shot-list.json"), "w", encoding="utf-8") as f:
        json.dump(shot_list, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print("shot list -> shot-list.json")


if __name__ == "__main__":
    main()
