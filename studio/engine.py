"""Python motion-graphics engine: animated camera moves, text, badge, captions and transitions.
Frames are drawn with Pillow and streamed straight into FFmpeg (no Node.js needed)."""
import bisect
import functools
import math
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H, FPS = 720, 1280, 30
OVERLAP, LEAD, TAIL = 0.4, 0.25, 0.35

# zoom0, zoom1, pan_x0, pan_x1, pan_y0, pan_y1
MOTIONS = {"push_in": (1.0, 1.22, 0, 0, 0, 0), "pull_out": (1.22, 1.0, 0, 0, 0, 0),
           "pan_left": (1.15, 1.15, 1, -1, 0, 0), "pan_right": (1.15, 1.15, -1, 1, 0, 0),
           "tilt_up": (1.15, 1.15, 0, 0, 1, -1)}


def clamp(t):
    return min(max(t, 0.0), 1.0)


def lerp(a, b, t):
    return a + (b - a) * t


def smooth(t):
    t = clamp(t)
    return t * t * (3 - 2 * t)


def ease_out(t):
    return 1 - (1 - clamp(t)) ** 3


def ease_back(t):
    t = clamp(t)
    return 1 + 2.70158 * (t - 1) ** 3 + 1.70158 * (t - 1) ** 2


def hex_rgb(h):
    return tuple(int(h.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))


@functools.cache
def font(size):
    for name in ("arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf", "Helvetica.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def text_layer(text, size, color, max_w=W - 80, stroke=True):
    f, probe = font(size), ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if probe.textlength(t, font=f) <= max_w or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    lh = int(size * 1.22)
    img = Image.new("RGBA", (max_w + 20, lh * len(lines) + 20), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        d.text(((img.width - probe.textlength(line, font=f)) / 2, 10 + i * lh), line, font=f,
               fill=tuple(color) + (255,), stroke_width=max(2, size // 14) if stroke else 0, stroke_fill=(0, 0, 0, 255))
    return img


def blit(frame, layer, x, y, alpha=1.0):
    if alpha <= 0:
        return
    if alpha < 1:
        layer = layer.copy()
        layer.putalpha(layer.getchannel("A").point(lambda v: int(v * alpha)))
    frame.paste(layer, (int(x), int(y)), layer)


def gradient(c1, c2):
    mask = Image.linear_gradient("L").resize((W, H))
    return Image.composite(Image.new("RGB", (W, H), tuple(c2)), Image.new("RGB", (W, H), tuple(c1)), mask)


def shade():
    g = Image.linear_gradient("L").resize((W, int(H * 0.55))).point(lambda v: int(v * 0.72))
    layer = Image.new("RGBA", g.size, (0, 0, 0, 255))
    layer.putalpha(g)
    return layer


def badge_layer(text, acc, d=250):
    img = Image.new("RGBA", (d, d), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse((4, 4, d - 4, d - 4), fill=tuple(acc) + (255,), outline=(255, 255, 255, 255), width=8)
    t = text_layer(text.upper(), 44, (255, 255, 255), max_w=d - 60)
    img.alpha_composite(t, ((d - t.width) // 2, (d - t.height) // 2))
    return img


def pill(text, size, fg, bg=(255, 255, 255)):
    """A rounded call-to-action button."""
    t = text_layer(text, size, fg, stroke=False)
    box = t.getchannel("A").getbbox()
    if not box:
        return Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    t = t.crop(box)
    w, h = t.width + 90, t.height + 44
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle((0, 0, w - 1, h - 1), radius=h // 2, fill=tuple(bg) + (255,))
    img.alpha_composite(t, (45, 22))
    return img


def logo_layer(path, max_w, max_h):
    im = Image.open(path).convert("RGBA")
    im.thumbnail((max_w, max_h), Image.LANCZOS)
    return im


def caption_layer(words, active, acc):
    f, probe = font(40), ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    gap = 14
    widths = [probe.textlength(w, font=f) for w in words]
    img = Image.new("RGBA", (int(sum(widths) + gap * len(words)) + 20, 70), (0, 0, 0, 0))
    d, x = ImageDraw.Draw(img), 10
    for i, w in enumerate(words):
        d.text((x, 8), w, font=f, fill=(tuple(acc) if i == active else (255, 255, 255)) + (255,),
               stroke_width=3, stroke_fill=(0, 0, 0, 255))
        x += widths[i] + gap
    return img


def layout(voice_durs, end_voice):
    """Start time and duration of every shot; shots overlap by OVERLAP seconds for crossfades."""
    specs, t = [], 0.0
    for vd in voice_durs:
        dur = max(3.0, LEAD + vd + TAIL + OVERLAP)
        specs.append({"start": t, "dur": dur, "vd": vd})
        t += dur - OVERLAP
    specs.append({"start": t, "dur": LEAD + end_voice + 1.6, "vd": end_voice})
    return specs


def prepare(spec, brand):
    p = dict(spec)
    pri, acc = hex_rgb(brand["primary"]), hex_rgb(brand["accent"])
    p.update(acc=acc, cache={})
    if p["kind"] == "shot":
        im = Image.open(p["image"]).convert("RGB")
        s = max(W / im.width, H / im.height)
        p["img"] = im.resize((math.ceil(im.width * s), math.ceil(im.height * s)), Image.LANCZOS)
        p["shade"], p["words"] = shade(), p["voiceover"].split()
        weights = [len(w) + 1 for w in p["words"]]  # longer words are spoken for longer
        p["cum"] = [sum(weights[: i + 1]) / sum(weights) for i in range(len(weights))]
        p["logo"] = logo_layer(brand["logo"], 130, 90) if brand.get("logo") else None
        p["head"] = text_layer(p["headline"].upper(), 62, (255, 255, 255)) if p["headline"] else None
        p["badge_img"] = badge_layer(p["badge"], acc) if p["badge"] else None
    else:
        p["bg"] = gradient(pri, acc)
        white = (255, 255, 255)
        items = [(text_layer(brand["name"], 78, white), 0.27, 0.0),
                 (text_layer(brand["tagline"], 38, white), 0.40, 0.25),
                 (pill(brand["cta"], 50, pri), 0.53, 0.5),
                 (text_layer(brand["phone"], 62, white), 0.68, 0.75)]
        if brand.get("logo"):
            items.insert(0, (logo_layer(brand["logo"], 240, 190), 0.07, 0.0))
        p["items"] = [i for i in items if i[0].getchannel("A").getbbox()]
    return p


def shot_frame(p, t):
    k = smooth(t / p["dur"])
    z0, z1, x0, x1, y0, y1 = MOTIONS[p["motion"]]
    z, px, py = lerp(z0, z1, k), lerp(x0, x1, k), lerp(y0, y1, k)
    iw, ih = p["img"].size
    vw, vh = W / z, H / z
    cx, cy = iw / 2 + px * (iw - vw) / 2, ih / 2 + py * (ih - vh) / 2
    frame = p["img"].resize((W, H), Image.BILINEAR, box=(cx - vw / 2, cy - vh / 2, cx + vw / 2, cy + vh / 2))
    frame.paste(p["shade"], (0, H - p["shade"].height), p["shade"])
    if p["head"]:
        a = ease_out(t / 0.5)
        blit(frame, p["head"], (W - p["head"].width) / 2, H * 0.60 + (1 - a) * 45, a)
    if p["badge_img"] and t > 0.4:
        sd = max(int(250 * ease_back((t - 0.4) / 0.6)), 2)
        b = p["badge_img"].resize((sd, sd))
        blit(frame, b, W - 175 - sd // 2, 230 - sd // 2)
    if p["logo"]:
        blit(frame, p["logo"], 30, 50, 0.95)
    local, n = t - LEAD, len(p["words"])
    if n and 0 <= local <= p["vd"] + 0.3:
        idx = min(bisect.bisect_left(p["cum"], local / p["vd"]), n - 1)
        chunk = idx // 3
        key = (chunk, idx)
        if key not in p["cache"]:
            p["cache"][key] = caption_layer(p["words"][chunk * 3:chunk * 3 + 3], idx - chunk * 3, p["acc"])
        c = p["cache"][key]
        blit(frame, c, (W - c.width) / 2, H * 0.84)
    return frame


def end_frame(p, t):
    frame = p["bg"].copy()
    for layer, yf, delay in p["items"]:
        a = ease_out((t - delay) / 0.5)
        blit(frame, layer, (W - layer.width) / 2, H * yf + (1 - a) * 50, a)
    return frame


def compose(preps, T, total, acc):
    active = [p for p in preps if p["start"] <= T < p["start"] + p["dur"]] or [preps[-1]]
    frames = [(shot_frame if p["kind"] == "shot" else end_frame)(p, min(T - p["start"], p["dur"])) for p in active[:2]]
    frame = frames[0] if len(frames) == 1 else Image.blend(frames[0], frames[1], clamp((T - active[1]["start"]) / OVERLAP))
    ImageDraw.Draw(frame).rectangle((0, 0, int(W * T / total), 8), fill=tuple(acc))
    return frame


def render_video(preps, audio, out_path, progress=None):
    total = preps[-1]["start"] + preps[-1]["dur"]
    n = int(total * FPS)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(FPS), "-i", "-", "-i", str(audio), "-c:v", "libx264", "-pix_fmt", "yuv420p",
           "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-shortest", str(out_path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for i in range(n):
            proc.stdin.write(compose(preps, i / FPS, total, preps[0]["acc"]).tobytes())
            if progress:
                progress(i + 1, n)
    finally:
        proc.stdin.close()
        code = proc.wait()
    if code:
        raise RuntimeError("FFmpeg failed while encoding the video.")
    return Path(out_path)
