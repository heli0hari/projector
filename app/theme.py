import os
from imgui_bundle import imgui

ACCENT = (0.98, 0.42, 0.20)

FONT_CANDIDATES = [
    "C:/Windows/Fonts/segoeui.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
FONT_BOLD_CANDIDATES = [
    "C:/Windows/Fonts/segoeuib.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def _first(paths):
    for p in paths:
        if os.path.exists(p):
            return p
    return None


def load_fonts(scale):
    io = imgui.get_io()
    fonts = {"regular": None, "bold": None}
    p = _first(FONT_CANDIDATES)
    if p:
        fonts["regular"] = io.fonts.add_font_from_file_ttf(p, 15.0 * scale)
    b = _first(FONT_BOLD_CANDIDATES)
    if b:
        fonts["bold"] = io.fonts.add_font_from_file_ttf(b, 15.0 * scale)
    return fonts


def rgba(r, g, b, a=1.0):
    return imgui.ImVec4(r, g, b, a)


def apply(scale=1.0):
    s = imgui.get_style()
    s.window_rounding = 0
    s.child_rounding = 8
    s.frame_rounding = 6
    s.popup_rounding = 8
    s.grab_rounding = 6
    s.tab_rounding = 6
    s.scrollbar_rounding = 8
    s.window_border_size = 0
    s.frame_border_size = 0
    s.child_border_size = 0
    s.window_padding = imgui.ImVec2(14, 12)
    s.frame_padding = imgui.ImVec2(9, 6)
    s.item_spacing = imgui.ImVec2(8, 7)
    s.item_inner_spacing = imgui.ImVec2(6, 5)
    s.indent_spacing = 14
    s.scrollbar_size = 10
    s.grab_min_size = 10
    s.scale_all_sizes(scale)

    a = ACCENT
    C = imgui.Col_
    c = {
        C.text: rgba(0.92, 0.93, 0.95),
        C.text_disabled: rgba(0.48, 0.50, 0.55),
        C.window_bg: rgba(0.075, 0.08, 0.095),
        C.child_bg: rgba(0.105, 0.112, 0.13),
        C.popup_bg: rgba(0.11, 0.115, 0.135, 0.98),
        C.border: rgba(1, 1, 1, 0.06),
        C.frame_bg: rgba(0.15, 0.16, 0.185),
        C.frame_bg_hovered: rgba(0.19, 0.20, 0.23),
        C.frame_bg_active: rgba(0.22, 0.23, 0.27),
        C.title_bg: rgba(0.075, 0.08, 0.095),
        C.title_bg_active: rgba(0.075, 0.08, 0.095),
        C.menu_bar_bg: rgba(0.075, 0.08, 0.095),
        C.scrollbar_bg: rgba(0, 0, 0, 0),
        C.scrollbar_grab: rgba(1, 1, 1, 0.10),
        C.scrollbar_grab_hovered: rgba(1, 1, 1, 0.18),
        C.scrollbar_grab_active: rgba(1, 1, 1, 0.25),
        C.check_mark: rgba(*a),
        C.slider_grab: rgba(*a),
        C.slider_grab_active: rgba(1.0, 0.55, 0.35),
        C.button: rgba(0.17, 0.18, 0.21),
        C.button_hovered: rgba(0.23, 0.24, 0.28),
        C.button_active: rgba(a[0], a[1], a[2], 0.8),
        C.header: rgba(a[0], a[1], a[2], 0.22),
        C.header_hovered: rgba(a[0], a[1], a[2], 0.30),
        C.header_active: rgba(a[0], a[1], a[2], 0.40),
        C.separator: rgba(1, 1, 1, 0.07),
        C.tab: rgba(0.13, 0.14, 0.16),
        C.tab_hovered: rgba(a[0], a[1], a[2], 0.45),
        C.tab_selected: rgba(a[0], a[1], a[2], 0.85),
        C.text_selected_bg: rgba(a[0], a[1], a[2], 0.35),
        C.nav_cursor: rgba(*a),
    }
    for k, v in c.items():
        s.set_color_(k, v)


def col32(r, g, b, a=1.0):
    return imgui.IM_COL32(int(r * 255), int(g * 255), int(b * 255), int(a * 255))
