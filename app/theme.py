import os
from contextlib import contextmanager
from imgui_bundle import imgui

FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")

# dark glass palette
ACCENT = (0.58, 0.52, 1.00)       # violet
ACCENT_2 = (0.38, 0.86, 0.82)     # teal
INK = (0.95, 0.95, 0.98)
DIM = (0.64, 0.65, 0.72)
FAINT = (0.42, 0.43, 0.50)
BG = (0.035, 0.035, 0.06)
LIVE = (0.45, 0.92, 0.66)
WARN = (1.00, 0.74, 0.38)
SHADOW = (0.0, 0.0, 0.02)

FONT_FILES = {
    "ui": "SpaceGrotesk_400Regular.ttf",
    "ui_medium": "SpaceGrotesk_500Medium.ttf",
    "light": "SpaceGrotesk_300Light.ttf",
    "mono": "JetBrainsMono_400Regular.ttf",
    "mono_medium": "JetBrainsMono_500Medium.ttf",
}

_fonts = {}
_scale = 1.0
MONO_BOOST = 1.15
TRACK = 0.6


def load_fonts(scale):
    global _scale
    _scale = scale
    io = imgui.get_io()
    for key, name in FONT_FILES.items():
        path = os.path.join(FONT_DIR, name)
        _fonts[key] = io.fonts.add_font_from_file_ttf(path, 14.5 * scale) if os.path.exists(path) else None
    return _fonts


UI_BOOST = 1.12


def _px(name, size):
    return size * _scale * (MONO_BOOST if name.startswith("mono") else UI_BOOST)


@contextmanager
def use(name, size):
    f = _fonts.get(name)
    if f is not None:
        imgui.push_font(f, _px(name, size))
    try:
        yield
    finally:
        if f is not None:
            imgui.pop_font()


def text_size(name, size, text):
    with use(name, size):
        return imgui.calc_text_size(text)


def col32(r, g, b, a=1.0):
    return imgui.IM_COL32(int(r * 255), int(g * 255), int(b * 255), int(max(0, min(1, a)) * 255))


def rgba(c, a=1.0):
    return imgui.ImVec4(c[0], c[1], c[2], a)


def mix(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def draw_text(dl, name, size, pos, color, text, tracking=0.0):
    f = _fonts.get(name) or imgui.get_font()
    px = _px(name, size)
    tracking *= TRACK
    if tracking <= 0:
        dl.add_text(f, px, pos, color, text)
        return
    x = pos.x
    with use(name, size):
        for ch in text:
            dl.add_text(f, px, imgui.ImVec2(x, pos.y), color, ch)
            x += imgui.calc_text_size(ch).x + tracking * _scale


def tracked_width(name, size, text, tracking):
    with use(name, size):
        return sum(imgui.calc_text_size(c).x for c in text) + tracking * TRACK * _scale * max(len(text) - 1, 0)


# ------------------------------------------------------------------ glass primitives
def _vtx_count(dl):
    vb = dl.vtx_buffer
    try:
        return vb.size()
    except Exception:
        return len(vb)


def gradient_fill(dl, a, b, rounding, col_a, col_b, p0=None, p1=None):
    """Rounded rect filled with a linear gradient between two packed colours (same alpha)."""
    start = _vtx_count(dl)
    dl.add_rect_filled(a, b, col_a, rounding)
    end = _vtx_count(dl)
    imgui.internal.shade_verts_linear_color_gradient_keep_alpha(
        dl, start, end, p0 or a, p1 or imgui.ImVec2(a.x, b.y), col_a, col_b)


def shadow(dl, a, b, rounding, strength=1.0, color=SHADOW, offset=6.0):
    s = _scale
    for i, alpha in enumerate((0.16, 0.10, 0.06, 0.03)):
        g = (4 + i * 5) * s
        dl.add_rect_filled(imgui.ImVec2(a.x - g * 0.6, a.y - g * 0.3 + offset * s),
                           imgui.ImVec2(b.x + g * 0.6, b.y + g + offset * s),
                           col32(*color, alpha * strength), rounding + g)


def glass(dl, a, b, rounding, level=1.0, tint=None, border=True, drop=True):
    """Frosted dark glass panel: soft shadow, translucent gradient body, rim light."""
    if drop:
        shadow(dl, a, b, rounding, 0.9 * level)
    top = tint if tint else (0.80, 0.80, 0.95)
    bottom = (0.55, 0.55, 0.70) if not tint else mix(tint, (0.2, 0.2, 0.3), 0.5)
    alpha = 0.075 * level
    gradient_fill(dl, a, b, rounding, col32(*top, alpha * 1.25), col32(*bottom, alpha * 0.7))
    if border:
        dl.add_rect(a, b, col32(1, 1, 1, 0.09 + 0.03 * level), rounding, 1.0)
        r = rounding
        dl.add_line(imgui.ImVec2(a.x + r, a.y + 0.6), imgui.ImVec2(b.x - r, a.y + 0.6), col32(1, 1, 1, 0.22 * level), 1.0)


def glow(dl, centre, radius, color, strength=1.0):
    for i in range(6):
        k = 1.0 - i / 6.0
        dl.add_circle_filled(centre, radius * (0.35 + i * 0.13), col32(*color, 0.05 * strength * k), 32)


def apply(scale=1.0):
    s = imgui.get_style()
    s.window_rounding = 0
    s.child_rounding = 14
    s.frame_rounding = 15
    s.popup_rounding = 14
    s.grab_rounding = 10
    s.tab_rounding = 12
    s.scrollbar_rounding = 6
    s.window_border_size = 0
    s.frame_border_size = 1
    s.child_border_size = 0
    s.popup_border_size = 1
    s.window_padding = imgui.ImVec2(30, 22)
    s.frame_padding = imgui.ImVec2(13, 7)
    s.item_spacing = imgui.ImVec2(8, 8)
    s.item_inner_spacing = imgui.ImVec2(8, 4)
    s.scrollbar_size = 5
    s.grab_min_size = 14
    s.selectable_text_align = imgui.ImVec2(0, 0.5)
    s.scale_all_sizes(scale)

    a = ACCENT
    C = imgui.Col_
    colors = {
        C.text: rgba(INK),
        C.text_disabled: rgba(DIM),
        C.window_bg: rgba(BG, 0.0),
        C.child_bg: rgba(BG, 0.0),
        C.popup_bg: rgba((0.10, 0.10, 0.15), 0.97),
        C.border: rgba((1, 1, 1), 0.10),
        C.border_shadow: rgba((0, 0, 0), 0),
        C.frame_bg: rgba((1, 1, 1), 0.055),
        C.frame_bg_hovered: rgba((1, 1, 1), 0.085),
        C.frame_bg_active: rgba((1, 1, 1), 0.11),
        C.scrollbar_bg: rgba((0, 0, 0), 0),
        C.scrollbar_grab: rgba((1, 1, 1), 0.10),
        C.scrollbar_grab_hovered: rgba((1, 1, 1), 0.18),
        C.scrollbar_grab_active: rgba(a, 0.7),
        C.check_mark: rgba(INK),
        C.slider_grab: rgba((0.92, 0.92, 1.0), 0.95),
        C.slider_grab_active: rgba(a),
        C.button: rgba((1, 1, 1), 0.06),
        C.button_hovered: rgba((1, 1, 1), 0.10),
        C.button_active: rgba(a, 0.35),
        C.header: rgba((1, 1, 1), 0.07),
        C.header_hovered: rgba(a, 0.22),
        C.header_active: rgba(a, 0.32),
        C.separator: rgba((1, 1, 1), 0.08),
        C.text_selected_bg: rgba(a, 0.35),
        C.nav_cursor: rgba(a),
        C.modal_window_dim_bg: rgba((0, 0, 0), 0.5),
    }
    for k, v in colors.items():
        s.set_color_(k, v)


BACKDROP_FS = """
#version 330
uniform vec2 res;
uniform float t;
in vec2 v_uv;
out vec4 frag;

float blob(vec2 p, vec2 c, float r) {
    float d = length((p - c) * vec2(res.x / res.y, 1.0));
    return exp(-d * d / (r * r));
}

float hash(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }

void main() {
    vec2 p = vec2(v_uv.x, 1.0 - v_uv.y);
    vec3 col = vec3(0.030, 0.030, 0.052);
    float s = t * 0.035;
    col += vec3(0.42, 0.30, 0.95) * 0.30 * blob(p, vec2(0.12 + 0.05 * sin(s), 0.10 + 0.04 * cos(s * 1.3)), 0.55);
    col += vec3(0.25, 0.80, 0.78) * 0.20 * blob(p, vec2(0.92 + 0.04 * cos(s * 0.9), 0.92 + 0.05 * sin(s)), 0.60);
    col += vec3(0.90, 0.42, 0.78) * 0.12 * blob(p, vec2(0.62 + 0.06 * sin(s * 0.7), 0.05), 0.35);
    col += vec3(0.35, 0.45, 1.00) * 0.08 * blob(p, vec2(0.40, 0.75 + 0.05 * sin(s * 1.1)), 0.45);
    col += (hash(gl_FragCoord.xy) - 0.5) / 255.0 * 1.5;
    frag = vec4(col, 1.0);
}
"""
