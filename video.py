"""
Mobilograf-agent: kanalning o'z rasmlaridan qisqa videolar yasaydi (o'z kontentimiz - mualliflik muammosi yo'q).

Har bir slayd sekin yaqinlashadi (Ken Burns effekti), slaydlar orasida silliq o'tish bor.
1280x720, 25 kadr/soniya, H.264 - Telegramda kanalning o'zida o'ynaydi. Ovozsiz (musiqa ham mualliflik ostida).
ffmpeg imageio-ffmpeg paketidan olinadi (Windows va GitHub serverida bir xil ishlaydi).
"""

import io
import os
import subprocess
import tempfile

import imageio_ffmpeg
from PIL import Image

import rasm

W, H, FPS = 1280, 720, 25
FADE = 0.5  # soniya


def _fit(image: Image.Image) -> Image.Image:
    """Har qanday o'lchamdagi kartochkani 1280x720 kadrga joylaydi (ortiqcha joy kanal foni bilan to'ladi)."""
    image = image.convert("RGB")
    if abs(image.width / image.height - W / H) < 0.02:
        return image.resize((W, H))
    bg, _ = rasm.canvas(W, H)
    scale = min(W / image.width, H / image.height)
    im = image.resize((int(image.width * scale), int(image.height * scale)))
    bg.paste(im, ((W - im.width) // 2, (H - im.height) // 2))
    return bg


def _zoom(image: Image.Image, t: float) -> Image.Image:
    """t: 0..1 - kadr 1.0 dan 1.07 gacha sekin kattalashadi (markazdan)."""
    z = 1.0 + 0.07 * t
    w, h = W / z, H / z
    left, top = (W - w) / 2, (H - h) / 2
    return image.resize((W, H), Image.BILINEAR, box=(left, top, left + w, top + h))


def slideshow(slides: list, seconds: list[float] | float = 3.5) -> tuple[bytes, float]:
    """slides: PIL rasmlari yoki BytesIO/bayt. Natija: (mp4 bayt, davomiyligi soniyada)."""
    frames = []
    for s in slides:
        if isinstance(s, (bytes, bytearray)):
            s = io.BytesIO(s)
        if isinstance(s, io.BytesIO):
            s.seek(0)
            s = Image.open(s)
        frames.append(_fit(s))
    durations = seconds if isinstance(seconds, list) else [seconds] * len(frames)
    total = sum(durations)
    out = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    out.close()
    cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p",
           "-preset", "veryfast", "-crf", "24", "-movflags", "+faststart", out.name]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for i, (img, dur) in enumerate(zip(frames, durations)):
            n = int(dur * FPS)
            nxt = frames[i + 1] if i + 1 < len(frames) else None
            for k in range(n):
                frame = _zoom(img, k / max(1, n - 1))
                left = (n - k) / FPS
                if nxt is not None and left <= FADE:  # keyingi slaydga silliq o'tish
                    frame = Image.blend(frame, _zoom(nxt, 0), 1 - left / FADE)
                proc.stdin.write(frame.tobytes())
        proc.stdin.close()
        proc.wait(timeout=300)
        with open(out.name, "rb") as f:
            data = f.read()
    finally:
        os.unlink(out.name)
    return data, total


def to_upload(data: bytes, duration: float):
    """Telethon uchun: fayl va video atributlari (Telegram uni oqimli video sifatida ko'rsatadi)."""
    from telethon.tl.types import DocumentAttributeVideo
    buf = io.BytesIO(data)
    buf.name = "futbolpulsi.mp4"
    return buf, [DocumentAttributeVideo(duration=int(duration), w=W, h=H, supports_streaming=True)]
