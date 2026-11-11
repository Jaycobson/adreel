import io
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from studio import pipeline
from studio.director import normalize_plan
from studio.media import ff
from studio.video_utils import ffmpeg_ok

pytestmark = pytest.mark.skipif(not ffmpeg_ok(), reason="FFmpeg not installed")

SHOT = {"image_prompt": "cake", "motion": "pan_left", "headline": "Fresh cakes", "voiceover": "Baked daily.", "badge": "20% OFF"}


def test_end_to_end_with_voice_cache_and_logo(tmp_path, monkeypatch):
    spoken = []

    def fake_speak(text, out, api_key, voice="coral"):
        spoken.append(text)
        ff("-f", "lavfi", "-i", "sine=frequency=300:duration=1", out)
        return Path(out)

    buf = io.BytesIO()
    Image.new("RGB", (512, 768), (180, 90, 40)).save(buf, "PNG")
    monkeypatch.setattr(pipeline, "plan_ad", lambda brief, key: normalize_plan({"brand": {"name": "Tola"}, "shots": [SHOT, SHOT]}))
    monkeypatch.setattr(pipeline, "generate_image", lambda prompt, key: buf.getvalue())
    monkeypatch.setattr(pipeline, "speak", fake_speak)

    proj = pipeline.draw_storyboard("brief", "key", tmp_path)
    logo = tmp_path / "logo.png"
    Image.new("RGBA", (200, 100), (255, 255, 255, 255)).save(logo)
    proj["plan"]["brand"]["logo"] = str(logo)

    result = pipeline.render_ad(proj, "key")
    assert Path(result["video"]).stat().st_size > 0 and len(proj["images"]) == 2
    first_run = len(spoken)
    assert first_run == 2  # the repeated shot line is only recorded once, plus the closing line

    proj["plan"]["shots"][0]["headline"] = "New headline"  # a visual-only edit
    pipeline.render_ad(proj, "key")
    assert len(spoken) == first_run  # voice cache: unchanged lines are not re-recorded
    subprocess.run(["ffprobe", "-v", "error", result["video"]], check=True)
