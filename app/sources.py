import numpy as np
import moderngl

try:
    import SpoutGL
    HAVE_SPOUT = True
except Exception:
    SpoutGL = None
    HAVE_SPOUT = False

GL_TEXTURE_2D = 0x0DE1


def _make_texture(ctx, w, h, data=None):
    tex = ctx.texture((w, h), 4, data)
    tex.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR) if data is not None else (moderngl.LINEAR, moderngl.LINEAR)
    tex.repeat_x = False
    tex.repeat_y = False
    if data is not None:
        tex.build_mipmaps()
        tex.anisotropy = 8.0
    return tex


def test_pattern(w=1920, h=1080):
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    u, v = x / w, y / h
    img = np.zeros((h, w, 3), np.float32)
    # soft colour field
    img[..., 0] = 0.15 + 0.55 * u
    img[..., 1] = 0.12 + 0.35 * (1 - v)
    img[..., 2] = 0.35 + 0.45 * (1 - u) * v
    # grid
    step = h / 12
    gx = np.minimum(x % step, step - x % step) < 1.5
    gy = np.minimum(y % step, step - y % step) < 1.5
    img[gx | gy] = 0.9
    # circles
    cx, cy = w / 2, h / 2
    r = np.hypot(x - cx, y - cy)
    for rad in (h * 0.15, h * 0.3, h * 0.45):
        img[np.abs(r - rad) < 2.5] = 1.0
    # centre cross + corner markers
    img[(np.abs(x - cx) < 2) | (np.abs(y - cy) < 2)] = 1.0
    m = int(h * 0.08)
    colors = [(1, 0.3, 0.2), (0.2, 0.8, 1), (0.6, 1, 0.3), (1, 0.85, 0.2)]
    for (sx, sy), c in zip(((0, 0), (w - m, 0), (w - m, h - m), (0, h - m)), colors):
        img[sy:sy + m, sx:sx + m] = c
    # colour bars along the bottom
    bars = [(1, 1, 1), (1, 1, 0), (0, 1, 1), (0, 1, 0), (1, 0, 1), (1, 0, 0), (0, 0, 1)]
    bw = w // 3 // len(bars)
    x0 = w // 3
    for i, c in enumerate(bars):
        img[int(h * 0.82):int(h * 0.88), x0 + i * bw:x0 + (i + 1) * bw] = c
    rgba = np.concatenate([img, np.ones((h, w, 1), np.float32)], axis=-1)
    return (np.clip(rgba, 0, 1) * 255).astype(np.uint8)


class Source:
    origin_bottom = False

    def __init__(self, ctx):
        self.ctx = ctx
        self.texture = None
        self.status = ""

    @property
    def size(self):
        return self.texture.size if self.texture else (0, 0)

    def update(self):
        pass

    def release(self):
        if self.texture:
            self.texture.release()
            self.texture = None


class TestPatternSource(Source):
    def __init__(self, ctx, w=1920, h=1080):
        super().__init__(ctx)
        self.texture = _make_texture(ctx, w, h, test_pattern(w, h).tobytes())
        self.status = f"Test pattern {w}x{h}"


class ImageSource(Source):
    def __init__(self, ctx, path):
        super().__init__(ctx)
        try:
            from PIL import Image
            img = Image.open(path).convert("RGBA")
            self.texture = _make_texture(ctx, img.width, img.height, img.tobytes())
            self.status = f"Image {img.width}x{img.height}"
        except Exception as e:
            self.texture = _make_texture(ctx, 4, 4, bytes(64))
            self.status = f"Image failed: {e}"


class SpoutSource(Source):
    origin_bottom = True

    def __init__(self, ctx, name=""):
        super().__init__(ctx)
        self.name = name
        self.texture = _make_texture(ctx, 16, 16)
        self.receiver = None
        self.connected = False
        if not HAVE_SPOUT:
            self.status = "SpoutGL not installed (pip install SpoutGL, Windows only)"
            return
        self.receiver = SpoutGL.SpoutReceiver()
        if name:
            self.receiver.setReceiverName(name)
        self.status = "Waiting for sender..."

    def update(self):
        if not self.receiver:
            return
        ok = self.receiver.receiveTexture(self.texture.glo, GL_TEXTURE_2D, False, 0)
        if self.receiver.isUpdated():
            w = self.receiver.getSenderWidth()
            h = self.receiver.getSenderHeight()
            if w > 0 and h > 0 and (w, h) != self.texture.size:
                self.texture.release()
                self.texture = _make_texture(self.ctx, w, h)
        if ok and self.texture.size != (16, 16):
            self.connected = True
            self.status = f"Spout: {self.sender_name() or 'active sender'} {self.texture.size[0]}x{self.texture.size[1]}"
        elif not ok:
            self.connected = False
            self.status = "Waiting for sender..."

    def sender_name(self):
        try:
            return self.receiver.getSenderName()
        except Exception:
            return self.name

    def release(self):
        if self.receiver:
            try:
                self.receiver.releaseReceiver()
            except Exception:
                pass
            self.receiver = None
        super().release()


class VideoSource(Source):
    """Decodes a video file on a background thread and streams frames into a texture."""

    def __init__(self, ctx, path, loop=True):
        super().__init__(ctx)
        import threading
        import queue
        self.path = path
        self.loop = loop
        self.playing = True
        self.duration = 0.0
        self.fps = 30.0
        self.position = 0.0          # seconds of the frame on screen
        self.frames = queue.Queue(maxsize=6)
        self._seek_to = None
        self._clock_origin = None    # perf_counter at position 0
        self._lock = threading.Lock()
        self._stop = False
        self._eof = False
        self._pending = None
        try:
            import av
            self.container = av.open(path)
            self.stream = self.container.streams.video[0]
            self.stream.thread_type = "AUTO"
            w, h = self.stream.codec_context.width, self.stream.codec_context.height
            if self.stream.duration and self.stream.time_base:
                self.duration = float(self.stream.duration * self.stream.time_base)
            elif self.container.duration:
                self.duration = self.container.duration / 1_000_000
            if self.stream.average_rate:
                self.fps = float(self.stream.average_rate)
            self.texture = _make_texture(ctx, w, h)
            self.status = f"Video {w}x{h}  {self.fps:.0f} fps"
            self.ok = True
        except Exception as e:
            self.texture = _make_texture(ctx, 4, 4, bytes(64))
            self.status = f"Video failed: {e}"
            self.ok = False
            return
        self.thread = threading.Thread(target=self._decode, daemon=True)
        self.thread.start()

    # ---------------------------------------------------------- decoding thread
    def _decode(self):
        import queue
        while not self._stop:
            with self._lock:
                seek = self._seek_to
                self._seek_to = None
            if seek is not None:
                try:
                    self.container.seek(int(seek / self.stream.time_base), stream=self.stream, backward=True)
                except Exception:
                    pass
                self._drain()
                self._eof = False
                skip_until = seek
            else:
                skip_until = None
            try:
                for frame in self.container.decode(self.stream):
                    if self._stop or self._seek_to is not None:
                        break
                    t = float(frame.pts * self.stream.time_base) if frame.pts is not None else 0.0
                    if skip_until is not None and t < skip_until - 0.5 / self.fps:
                        continue
                    skip_until = None
                    img = frame.to_ndarray(format="rgba")
                    while not self._stop and self._seek_to is None:
                        try:
                            self.frames.put((t, img), timeout=0.05)
                            break
                        except queue.Full:
                            continue
                else:
                    self._eof = True
                    if self.loop and not self._stop:
                        with self._lock:
                            if self._seek_to is None:
                                self._seek_to = 0.0
                    else:
                        while not self._stop and self._seek_to is None:
                            import time
                            time.sleep(0.02)
            except Exception as e:
                self.status = f"Decode error: {e}"
                import time
                time.sleep(0.1)

    def _drain(self):
        import queue
        try:
            while True:
                self.frames.get_nowait()
        except queue.Empty:
            pass

    # ---------------------------------------------------------- main thread
    def _now(self):
        import time
        return time.perf_counter()

    def update(self):
        if not self.ok:
            return
        import queue
        now = self._now()
        if self._clock_origin is None:
            self._clock_origin = now - self.position
        clock = (now - self._clock_origin) if self.playing else self.position
        half = 0.5 / self.fps
        while True:
            frame = self._pending
            self._pending = None
            if frame is None:
                try:
                    frame = self.frames.get_nowait()
                except queue.Empty:
                    break
            t, img = frame
            if self.playing and t < self.position - 0.5:
                # the decoder looped back to the start: restart the clock there
                self._clock_origin = now - t
                clock = t
            if self._show_one or (self.playing and t <= clock + half):
                self._upload(img, t)
                self._show_one = False
                continue
            self._pending = frame
            break

    _show_one = True

    def _upload(self, img, t):
        h, w = img.shape[:2]
        if (w, h) != self.texture.size:
            self.texture.release()
            self.texture = _make_texture(self.ctx, w, h)
        self.texture.write(img.tobytes())
        self.position = t

    def play(self, on=True):
        if on and not self.playing:
            self._clock_origin = self._now() - self.position
        self.playing = on

    def seek(self, seconds):
        seconds = max(0.0, min(seconds, max(self.duration - 0.05, 0.0)))
        with self._lock:
            self._seek_to = seconds
        self._pending = None
        self._drain()
        self.position = seconds
        self._clock_origin = self._now() - seconds
        self._show_one = True

    def release(self):
        self._stop = True
        if getattr(self, "thread", None):
            self.thread.join(timeout=1.0)
        try:
            self.container.close()
        except Exception:
            pass
        super().release()


def list_spout_senders():
    if not HAVE_SPOUT:
        return []
    try:
        r = SpoutGL.SpoutReceiver()
        names = []
        if hasattr(r, "getSenderList"):
            names = list(r.getSenderList())
        elif hasattr(r, "getSenderCount"):
            for i in range(r.getSenderCount()):
                names.append(r.getSender(i))
        r.releaseReceiver()
        return [n for n in names if n]
    except Exception:
        return []


def make_source(ctx, project):
    if project.source_kind == "spout":
        return SpoutSource(ctx, project.spout_name)
    if project.source_kind == "image" and project.image_path:
        return ImageSource(ctx, project.image_path)
    if project.source_kind == "video" and project.video_path:
        return VideoSource(ctx, project.video_path, project.video_loop)
    return TestPatternSource(ctx)
