import shutil


def ffmpeg_ok() -> bool:
    """True when both ffmpeg and ffprobe are on PATH."""
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
