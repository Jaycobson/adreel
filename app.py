import io
import os
import shutil
import time
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv
from PIL import Image

from studio.pipeline import draw_storyboard, render_ad

load_dotenv()
st.set_page_config(page_title="AdReel", page_icon="🎬", layout="wide")
st.markdown("""
<style>
.block-container {max-width: 1100px; padding-top: 2rem;}
.hero {padding: 2.2rem 2.4rem; border-radius: 18px; color: #fff; margin-bottom: 1.5rem;
       background: linear-gradient(120deg, #4a1d96 0%, #c026d3 50%, #fb923c 100%);}
.hero h1 {color: #fff; margin: 0 0 .3rem 0; font-size: 2.3rem;}
.hero p {color: #f6e8ff; margin: 0; font-size: 1.05rem; max-width: 70%;}
.stButton > button {height: 3rem; font-weight: 600; border-radius: 10px;}
.stButton > button[kind="primary"] {background: linear-gradient(120deg, #c026d3, #fb923c); color: #fff; border: none;}
</style>
<div class="hero">
  <h1>🎬 AdReel</h1>
  <p>Describe your business and get a 9:16 video ad.</p>
</div>
""", unsafe_allow_html=True)

EXAMPLE = ("BrightCare Home Nursing in Lagos. Professional, compassionate nurses who come to your home: "
           "post-surgery wound care, elderly and palliative care, newborn and mother support, injections and drips. "
           "Licensed, background-checked, available 24/7. "
           "Book a free first consultation this month. Call 0801 234 5678.")

api_key = os.getenv("OPENAI_API_KEY")


def preflight(brief):
    if not brief.strip():
        return "Please describe your business first."
    if len(brief) > 1500:
        return "Please keep the brief under 1,500 characters."
    if not api_key:
        return "OPENAI_API_KEY not found. Add it to your .env file."
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        return "FFmpeg/ffprobe not found. Install FFmpeg and make sure it is on your PATH."
    return None


WEIGHTS = {
    "planning": (0.00, 0.05, "Planning"),
    "images": (0.05, 0.45, "Creating visuals"),
    "voice": (0.50, 0.05, "Recording voiceover"),
    "render": (0.55, 0.45, "Rendering video"),
}


def on_progress_for(bar):
    def on_progress(stage, done, total):
        start, span, label = WEIGHTS[stage]
        bar.progress(min(start + span * done / max(total, 1), 1.0), text=f"{label} ({done}/{total})")
    return on_progress


brief = st.text_area("Your business brief", EXAMPLE, height=120)
logo = st.file_uploader("Logo (optional, PNG or JPG)", type=["png", "jpg", "jpeg"])

if st.button("Generate ad", type="primary", use_container_width=True):
    problem = preflight(brief)
    if problem:
        st.warning(problem)
    else:
        try:
            with st.status("Creating your ad...", expanded=True) as status:
                bar = st.progress(0.0, text="Planning...")
                on_progress = on_progress_for(bar)
                t0 = time.time()
                proj = draw_storyboard(brief, api_key, Path("outputs") / time.strftime("%Y%m%d_%H%M%S"),
                                       on_progress)
                if logo:
                    path = Path(proj["dir"]) / "logo.png"
                    Image.open(io.BytesIO(logo.getvalue())).convert("RGBA").save(path)
                    proj["plan"]["brand"]["logo"] = str(path)
                proj["ad"] = render_ad(proj, api_key, on_progress)
                status.update(label="Done", state="complete", expanded=False)
            st.session_state["ad"] = proj["ad"]
        except Exception as e:
            st.error(f"Generation failed: {e}")

ad = st.session_state.get("ad")
if ad:
    st.divider()
    _, mid, _ = st.columns([1, 1, 1])
    with mid:
        st.video(ad["video"])
        st.download_button("Download ad (MP4)", Path(ad["video"]).read_bytes(), "adreel.mp4",
                           "video/mp4", use_container_width=True)