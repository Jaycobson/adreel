"""Two stages: draw a storyboard (plan + images), then render the ad from the (edited) storyboard."""
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .director import plan_ad
from .engine import LEAD, OVERLAP, layout, prepare, render_video
from .media import build_audio, duration, generate_image, speak

MUSIC = Path("assets/music.mp3")  # optional royalty-free track, ducked under the voice


def _noop(stage, done, total):
    return None


def draw_storyboard(brief, api_key, out_dir, on_progress=_noop):
    """Plan the ad and generate one image per shot. Returns a project dict the user can edit."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    plan = plan_ad(brief, api_key)
    on_progress("planning", 1, 1)
    n = len(plan["shots"])

    def image(i):
        shot = plan["shots"][i]
        prompt = (f"{shot['image_prompt']} Product: {plan['product']}. Style: {plan['style']}. "
                  "Vertical 9:16 composition, empty calm space in the lower third, no text or lettering.")
        path = out / f"shot_{i + 1}.png"
        path.write_bytes(generate_image(prompt, api_key))
        return str(path)

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(image, i) for i in range(n)]
        images = []
        for k, f in enumerate(futures, 1):
            images.append(f.result())
            on_progress("images", k, n)
    return {"plan": plan, "images": images, "dir": str(out)}


def _voices(lines, out, api_key, on_progress):
    """One clip per unique line. Clips are cached by text, so unchanged lines cost nothing on re-render."""
    paths = {t: out / f"voice_{hashlib.sha1(t.encode()).hexdigest()[:10]}.mp3" for t in dict.fromkeys(lines)}
    todo = [t for t, p in paths.items() if not p.exists()]
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(speak, t, paths[t], api_key) for t in todo]
        for k, f in enumerate(futures, 1):
            f.result()
            on_progress("voice", k, len(futures))
    return [paths[t] for t in lines]


def render_ad(project, api_key, on_progress=_noop):
    plan, out, t0 = project["plan"], Path(project["dir"]), time.time()
    shots, brand = plan["shots"], plan["brand"]
    voices = _voices([s["voiceover"] for s in shots] + [plan["closing_line"]], out, api_key, on_progress)
    vds = [duration(v) for v in voices]
    lay = layout(vds[:-1], vds[-1])
    seg_lens = [s["dur"] - OVERLAP for s in lay[:-1]] + [lay[-1]["dur"]]
    audio = build_audio(voices, seg_lens, LEAD, out / "audio.m4a", MUSIC)

    specs = [{**lay[i], "kind": "shot", "image": project["images"][i],
              **{k: shots[i][k] for k in ("motion", "headline", "badge", "voiceover")}} for i in range(len(shots))]
    specs.append({**lay[-1], "kind": "end"})
    video = render_video([prepare(s, brand) for s in specs], audio, out / "ad.mp4",
                         lambda k, total: on_progress("render", k, total))
    (out / "plan.json").write_text(json.dumps(plan, indent=2))
    return {"video": str(video), "seconds": time.time() - t0, "length": lay[-1]["start"] + lay[-1]["dur"]}
