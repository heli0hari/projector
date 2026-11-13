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
    return TestPatternSource(ctx)
