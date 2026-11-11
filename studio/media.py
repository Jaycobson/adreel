"""OpenAI image and voice generation, plus FFmpeg audio assembly."""
import base64
import os
import subprocess
import tempfile
from pathlib import Path

import requests

from .util import retry

VOICE_STYLE = "Speak like an upbeat, friendly Nigerian radio ad presenter: warm, energetic and clear."


@retry()
def generate_image(prompt, api_key, size="1024x1536", quality="low"):
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    res = client.images.generate(model=os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2"),
                                 prompt=prompt, size=size, quality=quality)
    item = res.data[0]
    return base64.b64decode(item.b64_json) if getattr(item, "b64_json", None) else requests.get(item.url, timeout=60).content


@retry()
def speak(text, out_path, api_key, voice="coral"):
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    try:
        resp = client.audio.speech.create(model="gpt-4o-mini-tts", voice=voice, input=text,
                                          instructions=VOICE_STYLE, response_format="mp3")
    except Exception:
        resp = client.audio.speech.create(model="tts-1", voice="nova", input=text, response_format="mp3")
    Path(out_path).write_bytes(resp.content)
    return Path(out_path)


def ff(*args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *map(str, args)], check=True)


def duration(path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                         capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def build_audio(voice_files, seg_lens, lead, out_path, music=None):
    """Place each voiceover at its shot, then optionally mix in music that ducks under the voice."""
    out_path = Path(out_path)
    with tempfile.TemporaryDirectory() as tmp:
        segs = []
        for i, (vf, n) in enumerate(zip(voice_files, seg_lens, strict=True)):
            seg = Path(tmp) / f"seg_{i}.wav"
            ff("-i", vf, "-af", f"adelay={int(lead * 1000)}:all=1,apad", "-t", f"{n:.3f}",
               "-ar", 44100, "-ac", 2, seg)
            segs.append(seg)
        listing = Path(tmp) / "list.txt"
        listing.write_text("".join(f"file '{s.as_posix()}'\n" for s in segs))
        voice = out_path.with_suffix(".voice.wav")
        ff("-f", "concat", "-safe", 0, "-i", listing, "-c", "copy", voice)
    if music and Path(music).exists():
        try:
            ff("-i", voice, "-stream_loop", -1, "-i", music, "-filter_complex",
               "[0:a]asplit=2[v1][v2];[1:a]volume=0.35[m];[m][v1]sidechaincompress=threshold=0.04:ratio=10:"
               "attack=20:release=500[d];[v2][d]amix=inputs=2:duration=first:dropout_transition=0,volume=2[out]",
               "-map", "[out]", "-ar", 44100, "-ac", 2, out_path)
            return out_path
        except subprocess.CalledProcessError:
            pass  # fall back to voice only
    ff("-i", voice, out_path)
    return out_path
