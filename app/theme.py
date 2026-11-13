import os
from contextlib import contextmanager
from imgui_bundle import imgui

FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")

ACCENT = (0.85, 0.95, 0.42)       # acid lime
INK = (0.93, 0.94, 0.92)          # primary text
DIM = (0.58, 0.60, 0.59)          # secondary text
FAINT = (0.36, 0.37, 0.37)        # rules, ticks
BG = (0.035, 0.037, 0.04)
PANEL = (0.072, 0.075, 0.08)
LIVE = (0.45, 0.95, 0.55)
WARN = (0.98, 0.70, 0.30)

FONT_FILES = {
    "ui": "SpaceGrotesk_400Regular.ttf",
    "ui_medium": "SpaceGrotesk_500Medium.ttf",
    "light": "SpaceGrotesk_300Light.ttf",
    "mono": "JetBrainsMono_400Regular.ttf",
    "mono_medium": "JetBrainsMono_500Medium.ttf",
}

_fonts = {}
_scale = 1.0


def load_fonts(scale):
    """Loads the bundled fonts. The first one becomes the default UI font."""
    global _scale
    _scale = scale
    io = imgui.get_io()
    for key, name in FONT_FILES.items():
        path = os.path.join(FONT_DIR, name)
        if os.path.exists(path):
            _fonts[key] = io.fonts.add_font_from_file_ttf(path, 14.5 * scale)
        else:
            _fonts[key] = None
    return _fonts


def font(name):
    return _fonts.get(name)


MONO_BOOST = 1.2     # mono labels read small next to Space Grotesk
TRACK = 0.55


def _px(name, size):
    return size * _scale * (MONO_BOOST if name.startswith("mono") else 1.0)


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


def draw_text(dl, name, size, pos, color, text, tracking=0.0):
    """Draw text with optional letter spacing (tracking in px)."""
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


def apply(scale=1.0):
    s = imgui.get_style()
    s.window_rounding = 0
    s.child_rounding = 10
    s.frame_rounding = 5
    s.popup_rounding = 8
    s.grab_rounding = 2
    s.tab_rounding = 5
    s.scrollbar_rounding = 2
    s.window_border_size = 0
    s.frame_border_size = 1
    s.child_border_size = 1
    s.popup_border_size = 1
    s.window_padding = imgui.ImVec2(18, 16)
    s.frame_padding = imgui.ImVec2(10, 6)
    s.item_spacing = imgui.ImVec2(6, 6)
    s.item_inner_spacing = imgui.ImVec2(8, 4)
    s.scrollbar_size = 6
    s.grab_min_size = 3
    s.selectable_text_align = imgui.ImVec2(0, 0.5)
    s.scale_all_sizes(scale)

    a = ACCENT
    C = imgui.Col_
    colors = {
        C.text: rgba(INK),
        C.text_disabled: rgba(DIM),
        C.window_bg: rgba(BG),
        C.child_bg: rgba(PANEL, 0.0),
        C.popup_bg: rgba((0.06, 0.063, 0.068), 0.98),
        C.border: rgba((1, 1, 1), 0.075),
        C.border_shadow: rgba((0, 0, 0), 0),
        C.frame_bg: rgba((1, 1, 1), 0.025),
        C.frame_bg_hovered: rgba((1, 1, 1), 0.05),
        C.frame_bg_active: rgba((1, 1, 1), 0.07),
        C.title_bg: rgba(BG),
        C.title_bg_active: rgba(BG),
        C.scrollbar_bg: rgba((0, 0, 0), 0),
        C.scrollbar_grab: rgba((1, 1, 1), 0.08),
        C.scrollbar_grab_hovered: rgba((1, 1, 1), 0.15),
        C.scrollbar_grab_active: rgba(a, 0.6),
        C.check_mark: rgba(a),
        C.slider_grab: rgba(INK, 0.75),
        C.slider_grab_active: rgba(a),
        C.button: rgba((1, 1, 1), 0.0),
        C.button_hovered: rgba((1, 1, 1), 0.06),
        C.button_active: rgba(a, 0.25),
        C.header: rgba((1, 1, 1), 0.05),
        C.header_hovered: rgba((1, 1, 1), 0.07),
        C.header_active: rgba((1, 1, 1), 0.09),
        C.separator: rgba((1, 1, 1), 0.07),
        C.text_selected_bg: rgba(a, 0.25),
        C.nav_cursor: rgba(a),
        C.modal_window_dim_bg: rgba((0, 0, 0), 0.6),
    }
    for k, v in colors.items():
        s.set_color_(k, v)
