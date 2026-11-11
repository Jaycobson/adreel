"""Creative director: an OpenAI model turns a short business brief into a shot list and brand kit."""
import json
import os
import re

MOTIONS = ["push_in", "pull_out", "pan_left", "pan_right", "tilt_up"]
MODELS = [os.getenv("OPENAI_PLANNER_MODEL"), "gpt-5.6", "gpt-4.1", "gpt-4o-mini"]

SYSTEM = """You are the creative director of a short vertical (9:16) social-media promo ad.
Reply with ONLY JSON:
{"brand": {"name": str, "tagline": str, "primary": "#RRGGBB", "accent": "#RRGGBB", "phone": str, "cta": str},
 "style": str, "product": str,
 "shots": [{"image_prompt": str, "motion": "push_in|pull_out|pan_left|pan_right|tilt_up",
            "headline": str, "voiceover": str, "badge": str}],
 "closing_line": str}
Rules:
- 4 or 5 shots. Each image_prompt describes one vertical scene; it MUST repeat the fixed `product` description so
  the product looks identical in every shot, and must leave calm empty space in the lower third. No text in images.
- headline: at most 5 words. voiceover: one short, energetic sentence. badge: a short offer such as "20% OFF" on
  at most one shot, otherwise "".
- primary and accent are bold, harmonious brand colours that suit the business.
- phone: only if the brief contains one, else "". cta: a short call to action. closing_line: the final spoken line.
- Be honest: never invent prices, awards or claims that are not in the brief."""


def _color(value, default):
    return value if isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value.strip()) else default


def normalize_plan(data: dict) -> dict:
    b = data.get("brand") or {}
    brand = {"name": str(b.get("name") or "Your Brand").strip(), "tagline": str(b.get("tagline") or "").strip(),
             "primary": _color(b.get("primary"), "#4a1d96"), "accent": _color(b.get("accent"), "#fb923c"),
             "phone": str(b.get("phone") or "").strip(), "cta": str(b.get("cta") or "Order today").strip()}
    shots = []
    for s in (data.get("shots") or [])[:6]:
        prompt = str(s.get("image_prompt", "")).strip()
        if prompt:
            motion = s.get("motion") if s.get("motion") in MOTIONS else MOTIONS[len(shots) % len(MOTIONS)]
            shots.append({"image_prompt": prompt, "motion": motion,
                          "headline": str(s.get("headline", "")).strip(),
                          "voiceover": str(s.get("voiceover", "")).strip() or brand["tagline"] or brand["name"],
                          "badge": str(s.get("badge", "")).strip()})
    if len(shots) < 2:
        raise ValueError("The director returned too few shots.")
    return {"brand": brand, "style": str(data.get("style") or "bright, clean commercial photography").strip(),
            "product": str(data.get("product") or "").strip(), "shots": shots,
            "closing_line": str(data.get("closing_line") or f"{brand['cta']} at {brand['name']}!").strip()}


def plan_ad(brief: str, api_key=None) -> dict:
    from openai import OpenAI

    client = OpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))
    last = None
    for model in [m for m in MODELS if m]:
        try:
            resp = client.chat.completions.create(
                model=model, response_format={"type": "json_object"},
                messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": brief}])
            return normalize_plan(json.loads(resp.choices[0].message.content))
        except Exception as e:
            last = e
    raise RuntimeError(f"Ad planning failed: {last}")
