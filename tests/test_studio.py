import pytest
from PIL import Image

from studio import engine
from studio.director import MOTIONS, normalize_plan

SHOT = {"image_prompt": "cake on a table", "motion": "push_in", "headline": "Fresh cakes", "voiceover": "Baked daily.", "badge": ""}


def test_plan_is_cleaned():
    plan = normalize_plan({"brand": {"name": "Tola", "primary": "not-a-colour", "accent": "#ff8800"},
                           "shots": [{**SHOT, "motion": "spin"}] * 9})
    assert len(plan["shots"]) == 6 and plan["brand"]["primary"] == "#4a1d96"
    assert all(s["motion"] in MOTIONS for s in plan["shots"])


def test_too_few_shots_raises():
    with pytest.raises(ValueError):
        normalize_plan({"shots": [SHOT]})


def test_layout_overlaps_shots():
    lay = engine.layout([2.0, 4.0], 2.0)
    assert lay[1]["start"] == pytest.approx(lay[0]["start"] + lay[0]["dur"] - engine.OVERLAP)
    assert lay[0]["dur"] >= 3.0 and len(lay) == 3


def test_easing_is_bounded():
    assert engine.ease_out(-1) == 0 and engine.ease_out(2) == 1
    assert engine.ease_back(1) == pytest.approx(1.0)


def test_renders_full_size_frames(tmp_path):
    img = tmp_path / "a.png"
    Image.new("RGB", (512, 768), (200, 120, 60)).save(img)
    brand = {"name": "Tola", "tagline": "Fresh daily", "primary": "#4a1d96", "accent": "#fb923c", "phone": "", "cta": "Order today"}
    lay = engine.layout([2.0], 1.5)
    shot = {**lay[0], "kind": "shot", "image": str(img), "motion": "pan_left", "headline": "Fresh cakes",
            "badge": "20% OFF", "voiceover": "Baked fresh every single morning"}
    preps = [engine.prepare(shot, brand), engine.prepare({**lay[1], "kind": "end"}, brand)]
    total = preps[-1]["start"] + preps[-1]["dur"]
    for T in (0.1, 1.0, preps[1]["start"] + 0.1, total - 0.05):
        assert engine.compose(preps, T, total, preps[0]["acc"]).size == (engine.W, engine.H)
