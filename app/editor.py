import numpy as np
from imgui_bundle import imgui

from . import geometry as geo
from . import theme as T
from .model import Surface, Mask, Project
from .theme import col32, ACCENT, ACCENT_2, INK, DIM, FAINT, LIVE, WARN

V2 = imgui.ImVec2
PICK_R = 11.0
MASK_COL = (0.98, 0.42, 0.38)


def file_dialog(save=False, kind="project"):
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        if kind == "project":
            types = [("ProjMap project", "*.projmap.json *.json"), ("All files", "*.*")]
        else:
            types = [("Images", "*.png *.jpg *.jpeg *.bmp *.tga *.webp"), ("All files", "*.*")]
        if save:
            path = filedialog.asksaveasfilename(defaultextension=".projmap.json", filetypes=types)
        else:
            path = filedialog.askopenfilename(filetypes=types)
        root.destroy()
        return path or None
    except Exception:
        return None


class Canvas:
    """Letterboxed, zoomable view of a normalised 0..1 image space."""

    def __init__(self):
        self.zoom = 1.0
        self.pan = np.zeros(2)
        self.origin = np.zeros(2)
        self.extent = np.ones(2)

    def layout(self, pos, size, aspect):
        avail = np.array([size.x, size.y])
        base = min(avail[0] / aspect, avail[1]) * 0.86
        self.extent = np.array([base * aspect, base]) * self.zoom
        centre = np.array([pos.x, pos.y]) + avail / 2 + self.pan
        self.origin = centre - self.extent / 2

    def to_screen(self, p):
        return self.origin + np.asarray(p) * self.extent

    def to_norm(self, s):
        return (np.asarray(s) - self.origin) / self.extent

    def v(self, p):
        s = self.to_screen(p)
        return V2(float(s[0]), float(s[1]))

    def zoom_at(self, mouse, factor, pos, size):
        before = self.to_norm(mouse)
        self.zoom = float(np.clip(self.zoom * factor, 0.2, 12.0))
        self.layout(pos, size, self.extent[0] / self.extent[1])
        self.pan += mouse - self.to_screen(before)

    def reset(self):
        self.zoom = 1.0
        self.pan[:] = 0


class Editor:
    def __init__(self, app):
        self.app = app
        self.sel_kind = "surface"
        self.sel_index = 0
        self.sel_point = None
        self.tab = "output"
        self.tab_request = None
        self.out_canvas = Canvas()
        self.in_canvas = Canvas()
        self.drag = None
        self.calib = False
        self.projector_guides = True
        self.spout_list = []
        self.toast = ""
        self.toast_time = -10.0

    # ================================================================ helpers
    @property
    def project(self) -> Project:
        return self.app.project

    @property
    def sc(self):
        return self.app.ui_scale

    def selected(self):
        p = self.project
        if self.sel_kind == "surface" and 0 <= self.sel_index < len(p.surfaces):
            return p.surfaces[self.sel_index]
        if self.sel_kind == "mask" and 0 <= self.sel_index < len(p.masks):
            return p.masks[self.sel_index]
        return None

    def select(self, kind, index, point=None):
        self.sel_kind, self.sel_index, self.sel_point = kind, index, point

    def notify(self, msg):
        self.toast = msg
        self.toast_time = imgui.get_time()

    def push(self):
        self.app.history.push()
        self.app.dirty = True

    def source_state(self):
        src = self.app.source
        kind = self.project.source_kind
        if kind == "spout":
            if getattr(src, "connected", False):
                return "LIVE", LIVE
            return "WAITING", WARN
        if kind == "image":
            return "STILL", INK
        return "TEST", ACCENT

    # ================================================================ actions
    def add_surface(self):
        self.push()
        p = self.project
        s = Surface(f"Surface {len(p.surfaces) + 1}", len(p.surfaces))
        s.points += 0.03 * (len(p.surfaces) % 5)
        s.output = p.active_output
        s.touch()
        p.surfaces.append(s)
        self.select("surface", len(p.surfaces) - 1)

    def duplicate(self):
        it = self.selected()
        if it is None:
            return
        self.push()
        p = self.project
        if isinstance(it, Surface):
            s = Surface.from_dict(it.to_dict())
            s.name = it.name + " copy"
            s.color_index = len(p.surfaces)
            s.points = s.points + 0.02
            s.touch()
            p.surfaces.append(s)
            self.select("surface", len(p.surfaces) - 1)
        else:
            m = Mask.from_dict(it.to_dict())
            m.name = it.name + " copy"
            m.points = m.points + 0.02
            p.masks.append(m)
            self.select("mask", len(p.masks) - 1)

    def add_mask(self):
        self.push()
        p = self.project
        m = Mask(f"Mask {len(p.masks) + 1}")
        m.output = p.active_output
        p.masks.append(m)
        self.select("mask", len(p.masks) - 1)

    def delete_selected(self):
        it = self.selected()
        if it is None:
            return
        self.push()
        if isinstance(it, Surface):
            self.project.surfaces.pop(self.sel_index)
            self.select("surface", min(self.sel_index, len(self.project.surfaces) - 1))
        else:
            self.project.masks.pop(self.sel_index)
            self.select("mask", min(self.sel_index, len(self.project.masks) - 1))

    def move_selected(self, d):
        lst = self.project.surfaces if self.sel_kind == "surface" else self.project.masks
        j = self.sel_index + d
        if 0 <= self.sel_index < len(lst) and 0 <= j < len(lst):
            self.push()
            lst[self.sel_index], lst[j] = lst[j], lst[self.sel_index]
            self.sel_index = j

    def nudge(self, dx, dy):
        it = self.selected()
        if it is None:
            return
        self.push()
        if self.tab == "input" and isinstance(it, Surface):
            w, h = self.app.source.size if self.app.source else (1, 1)
            d = np.array([dx / max(w, 1), dy / max(h, 1)])
            if isinstance(self.sel_point, int):
                it.source[self.sel_point] += d
            else:
                it.source += d
            it.touch()
            return
        d = np.array([dx / self.project.width, dy / self.project.height])
        if self.sel_point is None:
            it.points += d
        elif isinstance(it, Surface) and isinstance(self.sel_point, tuple):
            self._move_surface_point(it, self.sel_point, it.points[self.sel_point] + d, keep_mesh=True)
        elif isinstance(it, Mask) and isinstance(self.sel_point, int):
            it.points[self.sel_point] += d
        it.touch()

    def cycle_point(self, step=1):
        it = self.selected()
        if it is None:
            return
        if self.tab == "input" and isinstance(it, Surface):
            i = self.sel_point if isinstance(self.sel_point, int) else -1
            self.sel_point = (i + step) % 4
            return
        if isinstance(it, Surface):
            r, c = it.points.shape[:2]
            flat = [(i, j) for i in range(r) for j in range(c)]
            cur = flat.index(self.sel_point) if self.sel_point in flat else -1
            self.sel_point = flat[(cur + step) % len(flat)]
        else:
            cur = self.sel_point if isinstance(self.sel_point, int) else -1
            self.sel_point = (cur + step) % len(it.points)

    @staticmethod
    def _is_corner(s, rc):
        r, c = rc
        return r in (0, s.rows) and c in (0, s.cols)

    def _move_surface_point(self, s, rc, new, keep_mesh=True, base=None):
        pts = s.points if base is None else base
        if keep_mesh and self._is_corner(s, rc) and (s.rows > 1 or s.cols > 1):
            old_c = geo.corners_of_grid(pts)
            local = geo.apply_h(np.linalg.inv(geo.homography(geo.UNIT_SQUARE, old_c)), pts)
            new_c = old_c.copy()
            idx = {(0, 0): 0, (0, s.cols): 1, (s.rows, s.cols): 2, (s.rows, 0): 3}[tuple(rc)]
            new_c[idx] = new
            s.points = geo.apply_h(geo.homography(geo.UNIT_SQUARE, new_c), local)
        else:
            if base is not None:
                s.points = base.copy()
            s.points[rc] = new
        s.touch()

    # ================================================================ project io
    def new_project(self):
        self.push()
        self.app.close_all_outputs()
        self.project.load_dict(Project().to_dict())
        self.project.path = None
        self.select("surface", 0)
        self.app.rebuild_source()

    def open_project(self):
        path = file_dialog(False)
        if path:
            try:
                self.push()
                self.app.close_all_outputs()
                self.project.load(path)
                self.select("surface", 0)
                self.app.rebuild_source()
                self.notify("OPENED  " + path.replace("\\", "/").split("/")[-1])
            except Exception as e:
                self.notify(f"OPEN FAILED  {e}")

    def save_project(self, save_as=False):
        path = self.project.path
        if save_as or not path:
            path = file_dialog(True)
        if path:
            try:
                self.project.save(path)
                self.app.dirty = False
                self.notify("SAVED  " + path.replace("\\", "/").split("/")[-1])
            except Exception as e:
                self.notify(f"SAVE FAILED  {e}")

    def add_output(self):
        from .model import Output
        self.push()
        p = self.project
        o = Output(f"Output {len(p.outputs) + 1}")
        o.mode = "window"
        o.width, o.height = 960, 540
        o.x, o.y = 160 + 40 * len(p.outputs), 160 + 40 * len(p.outputs)
        o.borderless = False
        p.outputs.append(o)
        self.set_active_output(len(p.outputs) - 1)

    def delete_output(self):
        p = self.project
        if len(p.outputs) <= 1:
            self.notify("At least one output is needed")
            return
        self.push()
        self.app.close_all_outputs()
        p.remove_output(p.active_output)
        self.select("surface", -1)

    def set_active_output(self, idx):
        p = self.project
        if idx == p.active_output:
            return
        p.active_output = idx
        self.drag = None
        self.out_canvas.reset()
        it = self.selected()
        if it is None or it.output != idx:
            first = next((i for i, s in enumerate(p.surfaces) if s.output == idx), None)
            self.select("surface", first if first is not None else -1)

    def slider(self, label, obj, attr, lo, hi, fmt="%.2f"):
        self.label(label)
        ch, v = imgui.slider_float(f"##{attr}", getattr(obj, attr), lo, hi, fmt)
        if imgui.is_item_activated():
            self.push()
        if ch:
            setattr(obj, attr, v)
            obj.touch()

    def output_combo(self, it):
        p = self.project
        if len(p.outputs) < 2:
            return
        self.label("Output")
        names = [f"O{i + 1}  {o.name}" for i, o in enumerate(p.outputs)]
        ch, oi = imgui.combo("##sout", it.output, names)
        if ch and oi != it.output:
            self.push()
            it.output = oi
            it.touch()
            self.set_active_output(oi)
            self.select("surface" if isinstance(it, Surface) else "mask",
                        (p.surfaces if isinstance(it, Surface) else p.masks).index(it))

    def tile_combo(self, s):
        p = self.project
        n = p.input_cols * p.input_rows
        if n < 2:
            return
        current = 0
        for k in range(n):
            if np.allclose(s.source, p.tile_rect(k), atol=1e-6):
                current = k + 1
        self.label("Camera")
        items = ["Custom"] + [f"Tile {k + 1}" for k in range(n)]
        ch, ti = imgui.combo("##tile", current, items)
        if ch and ti > 0:
            self.push()
            s.source = p.tile_rect(ti - 1)
            s.touch()

    def surface_props(self, s):
        sc = self.sc
        self.label("Name")
        _, s.name = imgui.input_text("##name", s.name)
        self.output_combo(s)
        self.tile_combo(s)
        self.slider("Opacity", s, "opacity", 0, 1)
        self.slider("Bright", s, "brightness", 0, 2)
        self.slider("Feather", s, "feather", 0, 0.5)
        self.label("Grid")
        ch, v = imgui.slider_int2("##grid", [s.cols, s.rows], 1, 12, "%d")
        if imgui.is_item_activated():
            self.push()
        if ch:
            s.set_grid(v[1], v[0])
            self.sel_point = None
        self.label("Interp")
        ch, idx = imgui.combo("##interp", 1 if s.smooth else 0, ["Linear", "Smooth spline"])
        if ch:
            self.push()
            s.smooth = idx == 1
            s.touch()
        imgui.dummy(V2(0, 2 * sc))
        hit = self.button_row(["Flatten", "Fill out", "Full in"])
        if hit is not None:
            self.push()
            if hit == 0:
                s.reset_mesh()
            elif hit == 1:
                s.points = geo.lattice(s.rows, s.cols)
            else:
                s.source = geo.UNIT_SQUARE.copy()
            s.touch()
        hit = self.button_row(["Move up", "Move down"])
        if hit is not None:
            self.move_selected(-1 if hit == 0 else 1)

    def mask_props(self, m):
        self.label("Name")
        _, m.name = imgui.input_text("##name", m.name)
        self.output_combo(m)
        imgui.text_disabled("Double-click an edge to add a point")
        imgui.text_disabled("Right-click a point to remove it")

    # ================================================================ widgets
    def begin_card(self, title, info=None):
        sc = self.sc
        dl = imgui.get_window_draw_list()
        imgui.dummy(V2(0, 6 * sc))
        dl.channels_split(2)
        dl.channels_set_current(1)
        a = imgui.get_cursor_screen_pos()
        self._card = (dl, a)
        imgui.dummy(V2(0, 2 * sc))
        p = imgui.get_cursor_screen_pos()
        T.draw_text(dl, "ui_medium", 10.5, p, col32(*DIM), title.upper(), 2.2)
        if info:
            right = p.x + imgui.get_content_region_avail().x
            iw = T.text_size("ui", 11.5, info).x
            T.draw_text(dl, "ui", 11.5, V2(right - iw, p.y - 1 * sc), col32(*FAINT), info)
        imgui.dummy(V2(0, 16 * sc))

    def end_card(self):
        sc = self.sc
        dl, a = self._card
        imgui.dummy(V2(0, 2 * sc))
        pad = 15 * sc
        b = imgui.get_cursor_screen_pos()
        w = imgui.get_content_region_avail().x
        dl.channels_set_current(0)
        T.glass(dl, V2(a.x - pad, a.y - pad), V2(a.x + w + pad, b.y + pad - 8 * sc), 20 * sc)
        dl.channels_merge()
        imgui.dummy(V2(0, 14 * sc))

    def label(self, text, col_w=82):
        sc = self.sc
        p = imgui.get_cursor_screen_pos()
        h = imgui.get_frame_height()
        dl = imgui.get_window_draw_list()
        T.draw_text(dl, "ui", 12.5, V2(p.x, p.y + (h - 16 * sc) / 2), col32(*DIM), text)
        imgui.dummy(V2(col_w * sc - imgui.get_style().item_spacing.x, h))
        imgui.same_line()
        imgui.set_next_item_width(-1)

    def toggle(self, sid, value):
        """iOS-style glass switch. Returns (changed, value)."""
        sc = self.sc
        dl = imgui.get_window_draw_list()
        h = 22 * sc
        w = 40 * sc
        p = imgui.get_cursor_screen_pos()
        fh = imgui.get_frame_height()
        y = p.y + (fh - h) / 2
        imgui.invisible_button(sid, V2(w, fh))
        clicked = imgui.is_item_clicked()
        hov = imgui.is_item_hovered()
        if clicked:
            value = not value
        a, b = V2(p.x, y), V2(p.x + w, y + h)
        if value:
            T.gradient_fill(dl, a, b, h / 2, col32(*ACCENT, 0.95), col32(0.42, 0.40, 0.98, 0.95), a, V2(b.x, a.y))
            T.glow(dl, V2(b.x - h / 2, y + h / 2), h * 1.2, ACCENT, 0.8)
        else:
            dl.add_rect_filled(a, b, col32(1, 1, 1, 0.09 if hov else 0.06), h / 2)
        dl.add_rect(a, b, col32(1, 1, 1, 0.14), h / 2, 1)
        kx = b.x - h / 2 if value else a.x + h / 2
        dl.add_circle_filled(V2(kx, y + h / 2 + 1.5 * sc), h / 2 - 2 * sc, col32(0, 0, 0.05, 0.35), 24)
        dl.add_circle_filled(V2(kx, y + h / 2), h / 2 - 3 * sc, col32(0.97, 0.97, 1.0), 24)
        return clicked, value

    def button(self, text, width=0.0, kind="ghost", active=False):
        """kind: ghost | primary | danger"""
        sc = self.sc
        dl = imgui.get_window_draw_list()
        h = 32 * sc
        if width <= 0:
            width = imgui.get_content_region_avail().x if width < 0 else T.text_size("ui_medium", 12.5, text).x + 30 * sc
        p = imgui.get_cursor_screen_pos()
        clicked = imgui.invisible_button("##b" + text, V2(width, h))
        hov = imgui.is_item_hovered()
        down = imgui.is_item_active()
        a, b = p, V2(p.x + width, p.y + h)
        r = h / 2
        if kind == "primary" or active:
            T.shadow(dl, a, b, r, 0.8, (0.25, 0.15, 0.7), 4)
            c0 = (0.66, 0.58, 1.0) if not down else (0.52, 0.46, 0.92)
            c1 = (0.43, 0.38, 0.98) if not down else (0.36, 0.32, 0.86)
            if hov and not down:
                c0, c1 = T.mix(c0, (1, 1, 1), 0.12), T.mix(c1, (1, 1, 1), 0.12)
            T.gradient_fill(dl, a, b, r, col32(*c0), col32(*c1), a, V2(b.x, b.y))
            dl.add_rect(a, b, col32(1, 1, 1, 0.25), r, 1)
            dl.add_line(V2(a.x + r, a.y + 1), V2(b.x - r, a.y + 1), col32(1, 1, 1, 0.45), 1)
            tc = col32(1, 1, 1)
        else:
            fill = 0.12 if down else (0.09 if hov else 0.055)
            dl.add_rect_filled(a, b, col32(1, 1, 1, fill), r)
            dl.add_rect(a, b, col32(1, 1, 1, 0.13 if hov else 0.09), r, 1)
            dl.add_line(V2(a.x + r, a.y + 0.6), V2(b.x - r, a.y + 0.6), col32(1, 1, 1, 0.16), 1)
            tc = col32(*INK) if kind != "danger" else col32(1.0, 0.55, 0.6)
        ts = T.text_size("ui_medium", 12.5, text)
        T.draw_text(dl, "ui_medium", 12.5, V2(a.x + (width - ts.x) / 2, a.y + (h - ts.y) / 2), tc, text)
        return clicked

    def button_row(self, labels, kinds=None):
        n = len(labels)
        sp = imgui.get_style().item_spacing.x
        w = (imgui.get_content_region_avail().x - sp * (n - 1)) / n
        hit = None
        for i, l in enumerate(labels):
            if i:
                imgui.same_line()
            imgui.push_id(i)
            if self.button(l, w, kinds[i] if kinds else "ghost"):
                hit = i
            imgui.pop_id()
        return hit

    def pill(self, dl, pos, text, color, size=11.5, filled=True, backing=False):
        sc = self.sc
        tw = T.text_size("ui_medium", size, text).x
        h = 24 * sc
        w = tw + 34 * sc
        a, b = pos, V2(pos.x + w, pos.y + h)
        if backing:
            dl.add_rect_filled(a, b, col32(0.03, 0.03, 0.07, 0.82), h / 2)
        dl.add_rect_filled(a, b, col32(*color, 0.13), h / 2)
        dl.add_rect(a, b, col32(*color, 0.35), h / 2, 1)
        c = V2(a.x + 13 * sc, a.y + h / 2)
        T.glow(dl, c, 12 * sc, color, 1.2)
        dl.add_circle_filled(c, 3.5 * sc, col32(*color), 16)
        T.draw_text(dl, "ui_medium", size, V2(a.x + 23 * sc, a.y + (h - 15 * sc) / 2), col32(*color), text)
        return w

    def pill_width(self, text, size=11.5):
        return T.text_size("ui_medium", size, text).x + 34 * self.sc

    def logo(self, dl, p):
        """Glass app-icon tile with a corner-pinned plane inside."""
        sc = self.sc
        s = 36 * sc
        a, b = p, V2(p.x + s, p.y + s)
        r = 11 * sc
        T.shadow(dl, a, b, r, 1.0, (0.2, 0.1, 0.6), 4)
        T.gradient_fill(dl, a, b, r, col32(0.62, 0.52, 1.0), col32(0.30, 0.80, 0.82), a, b)
        dl.add_rect(a, b, col32(1, 1, 1, 0.35), r, 1)
        dl.add_line(V2(a.x + r, a.y + 1), V2(b.x - r, a.y + 1), col32(1, 1, 1, 0.6), 1)
        q = [V2(a.x + 9 * sc, a.y + 11 * sc), V2(b.x - 8 * sc, a.y + 8.5 * sc),
             V2(b.x - 10 * sc, b.y - 9 * sc), V2(a.x + 8 * sc, b.y - 11.5 * sc)]
        dl.add_convex_poly_filled(q, col32(1, 1, 1, 0.92))
        for v in q:
            dl.add_circle_filled(v, 2.2 * sc, col32(0.30, 0.26, 0.70), 12)
        return s

    # ================================================================ frame
    def draw(self, fb_w, fb_h):
        io = imgui.get_io()
        sc = self.sc
        side_w = 360 * sc
        status_h = 54 * sc
        m = 14 * sc
        flags = (imgui.WindowFlags_.no_title_bar | imgui.WindowFlags_.no_resize |
                 imgui.WindowFlags_.no_move | imgui.WindowFlags_.no_collapse |
                 imgui.WindowFlags_.no_bring_to_front_on_focus | imgui.WindowFlags_.no_background)
        w, h = io.display_size.x, io.display_size.y

        imgui.set_next_window_pos(V2(0, 0))
        imgui.set_next_window_size(V2(side_w, h - status_h))
        imgui.begin("##side", None, flags)
        self.draw_sidebar()
        imgui.end()

        imgui.set_next_window_pos(V2(side_w - 6 * sc, 0))
        imgui.set_next_window_size(V2(w - side_w + 6 * sc, h - status_h))
        imgui.push_style_var(imgui.StyleVar_.window_padding, V2(0, 0))
        imgui.begin("##view", None, flags | imgui.WindowFlags_.no_scrollbar | imgui.WindowFlags_.no_scroll_with_mouse)
        imgui.pop_style_var()
        self.draw_view(m)
        imgui.end()

        imgui.set_next_window_pos(V2(0, h - status_h))
        imgui.set_next_window_size(V2(w, status_h))
        imgui.push_style_var(imgui.StyleVar_.window_padding, V2(0, 0))
        imgui.begin("##status", None, flags | imgui.WindowFlags_.no_scrollbar)
        imgui.pop_style_var()
        self.draw_status(m)
        imgui.end()
        self.shortcuts()

    # ================================================================ sidebar
    def draw_sidebar(self):
        p = self.project
        app = self.app
        sc = self.sc
        dl = imgui.get_window_draw_list()

        # ---- brand
        o = imgui.get_cursor_screen_pos()
        s = self.logo(dl, V2(o.x - 4 * sc, o.y))
        T.draw_text(dl, "ui_medium", 19, V2(o.x + s + 8 * sc, o.y - 1 * sc), col32(*INK), "ProjMap")
        T.draw_text(dl, "ui", 12, V2(o.x + s + 8 * sc, o.y + 21 * sc), col32(*DIM), "Projection mapping studio")
        imgui.dummy(V2(0, s + 18 * sc))

        # ---- telemetry
        state, scol = self.source_state()
        src = app.source
        self.begin_card(f"Output {p.active_output + 1}", p.output.name)
        a = imgui.get_cursor_screen_pos()
        wcard = imgui.get_content_region_avail().x
        big = f"{p.width}"
        T.draw_text(dl, "light", 44, V2(a.x - 2 * sc, a.y - 8 * sc), col32(*INK), big)
        bw = T.text_size("light", 44, big).x
        T.draw_text(dl, "light", 20, V2(a.x + bw + 6 * sc, a.y + 13 * sc), col32(*DIM), f"/ {p.height}")
        pw = self.pill_width(state)
        self.pill(dl, V2(a.x + wcard - pw, a.y + 6 * sc), state, scol)
        imgui.dummy(V2(0, 50 * sc))
        # fps capsule
        a = imgui.get_cursor_screen_pos()
        fps = imgui.get_io().framerate
        bw = wcard * 0.58
        ch = 10 * sc
        dl.add_rect_filled(a, V2(a.x + bw, a.y + ch), col32(1, 1, 1, 0.07), ch / 2)
        fill = max(ch, bw * min(fps / 60.0, 1.0))
        T.gradient_fill(dl, a, V2(a.x + fill, a.y + ch), ch / 2, col32(*ACCENT_2, 0.9), col32(0.50, 0.62, 1.0, 0.9),
                        a, V2(a.x + fill, a.y))
        T.draw_text(dl, "ui", 12, V2(a.x + bw + 10 * sc, a.y - 3 * sc), col32(*DIM), f"{fps:.0f} / 60 fps")
        imgui.dummy(V2(0, ch + 10 * sc))
        srcname = {"spout": "Spout", "image": "Image", "test": "Test pattern"}[p.source_kind]
        if p.source_kind == "spout" and p.spout_name:
            srcname += " - " + p.spout_name[:16]
        sw, sh = src.size if src else (0, 0)
        a = imgui.get_cursor_screen_pos()
        T.draw_text(dl, "ui", 12, a, col32(*DIM), srcname)
        r = f"{sw} x {sh}"
        T.draw_text(dl, "ui", 12, V2(a.x + wcard - T.text_size("ui", 12, r).x, a.y), col32(*FAINT), r)
        imgui.dummy(V2(0, 16 * sc))
        gpu = getattr(app, "gpu_name", "")
        if gpu:
            a = imgui.get_cursor_screen_pos()
            T.draw_text(dl, "ui", 11, a, col32(*FAINT), "GPU  " + gpu[:42])
            imgui.dummy(V2(0, 14 * sc))
        self.end_card()

        hit = self.button_row(["New", "Open", "Save", "Save as"])
        if hit == 0:
            self.new_project()
        elif hit == 1:
            self.open_project()
        elif hit == 2:
            self.save_project()
        elif hit == 3:
            self.save_project(True)
        name = p.path.replace("\\", "/").split("/")[-1] if p.path else "Untitled project"
        a = imgui.get_cursor_screen_pos()
        T.draw_text(dl, "ui", 11.5, V2(a.x + 4 * sc, a.y), col32(*FAINT), name + ("  - unsaved" if app.dirty else ""))
        imgui.dummy(V2(0, 22 * sc))

        # ---- input
        self.begin_card("Input")
        kinds = ["test", "spout", "image"]
        labels = ["Test pattern", "Spout  (Blender / AE)", "Image file"]
        cur = kinds.index(p.source_kind) if p.source_kind in kinds else 0
        self.label("Source")
        ch, cur = imgui.combo("##srckind", cur, labels)
        if ch:
            p.source_kind = kinds[cur]
            app.rebuild_source()
            app.dirty = True
        if p.source_kind == "spout":
            self.label("Sender")
            imgui.set_next_item_width(-70 * sc)
            ch, p.spout_name = imgui.input_text_with_hint("##spname", "Any active sender", p.spout_name)
            if imgui.is_item_deactivated_after_edit():
                app.rebuild_source()
                app.dirty = True
            imgui.same_line()
            if self.button("List", -1):
                from .sources import list_spout_senders
                self.spout_list = list_spout_senders()
                imgui.open_popup("spout_list")
            if imgui.begin_popup("spout_list"):
                if not self.spout_list:
                    imgui.text_disabled("No senders found")
                for n in self.spout_list:
                    if imgui.selectable(n, n == p.spout_name)[0]:
                        p.spout_name = n
                        app.rebuild_source()
                imgui.end_popup()
        elif p.source_kind == "image":
            self.label("File")
            imgui.set_next_item_width(-84 * sc)
            imgui.input_text("##img", p.image_path, imgui.InputTextFlags_.read_only)
            imgui.same_line()
            if self.button("Browse", -1):
                path = file_dialog(False, "image")
                if path:
                    p.image_path = path
                    app.rebuild_source()
                    app.dirty = True
        self.label("Atlas")
        ch, v = imgui.slider_int2("##atlas", [p.input_cols, p.input_rows], 1, 6, "%d")
        if imgui.is_item_activated():
            self.push()
        if ch:
            p.input_cols, p.input_rows = max(1, v[0]), max(1, v[1])
        if imgui.is_item_hovered():
            imgui.set_tooltip("Camera atlas layout (columns x rows). Match the Blender add-on.")
        self.label("Flip")
        ch, p.flip_input = self.toggle("##flip", p.flip_input)
        if ch:
            app.dirty = True
        self.end_card()

        # ---- outputs
        self.begin_card("Outputs", f"{app.open_count()} live")
        self.output_list()
        live = app.is_open()
        hit = self.button_row(["+ Output", "Remove", "Show" if not live else "Hide"],
                              ["ghost", "ghost", "primary" if not live else "ghost"])
        if hit == 0:
            self.add_output()
        elif hit == 1:
            self.delete_output()
        elif hit == 2:
            app.toggle_output()
        imgui.dummy(V2(0, 2 * sc))
        self.output_props(p.output)
        self.label("Guides")
        _, self.projector_guides = self.toggle("##guides", self.projector_guides)
        imgui.same_line(0, 18 * sc)
        cp = imgui.get_cursor_screen_pos()
        T.draw_text(dl, "ui", 12.5, V2(cp.x, cp.y + (imgui.get_frame_height() - 16 * sc) / 2), col32(*DIM), "Calibrate")
        imgui.dummy(V2(T.text_size("ui", 12.5, "Calibrate").x + 4 * sc, imgui.get_frame_height()))
        imgui.same_line()
        _, self.calib = self.toggle("##calib", self.calib)
        self.end_card()

        # ---- layers
        self.begin_card("Layers")
        self.layer_list()
        hit = self.button_row(["+ Surface", "+ Mask", "Duplicate", "Delete"],
                              ["ghost", "ghost", "ghost", "danger"])
        if hit == 0:
            self.add_surface()
        elif hit == 1:
            self.add_mask()
        elif hit == 2:
            self.duplicate()
        elif hit == 3:
            self.delete_selected()
        self.end_card()

        # ---- properties
        it = self.selected()
        if it is not None and it.output == p.active_output:
            self.begin_card("Surface" if isinstance(it, Surface) else "Mask", it.name)
            if isinstance(it, Surface):
                self.surface_props(it)
            else:
                self.mask_props(it)
            self.end_card()
        imgui.dummy(V2(0, 6 * sc))

    def _row(self, dl, a, b, sel, hov, color, name, info, dim=False, live=None, eye=None, eye_hov=False):
        sc = self.sc
        r = (b.y - a.y) / 2
        alpha = 0.4 if dim else 1.0
        if sel:
            T.gradient_fill(dl, a, b, r, col32(*T.mix(color, (1, 1, 1), 0.6), 0.13), col32(1, 1, 1, 0.05), a, V2(b.x, a.y))
            dl.add_rect(a, b, col32(1, 1, 1, 0.16), r, 1)
        elif hov:
            dl.add_rect_filled(a, b, col32(1, 1, 1, 0.045), r)
        cy = a.y + r
        cx = a.x + 17 * sc
        if sel:
            T.glow(dl, V2(cx, cy), 16 * sc, color, 1.4)
        dl.add_circle_filled(V2(cx, cy), 4.5 * sc, col32(*color, alpha), 16)
        T.draw_text(dl, "ui_medium" if sel else "ui", 13, V2(a.x + 32 * sc, cy - 8.5 * sc), col32(*INK, alpha), name)
        iw = T.text_size("ui", 11.5, info).x
        T.draw_text(dl, "ui", 11.5, V2(b.x - 40 * sc - iw, cy - 7.5 * sc), col32(*DIM, alpha), info)
        ex = b.x - 18 * sc
        ring = 9 * sc
        if live is not None:
            if live:
                T.glow(dl, V2(ex, cy), 16 * sc, LIVE, 1.6)
                dl.add_circle_filled(V2(ex, cy), ring, col32(*LIVE, 0.16), 24)
                dl.add_circle(V2(ex, cy), ring, col32(*LIVE, 0.6), 24, 1.2)
                dl.add_circle_filled(V2(ex, cy), 3.5 * sc, col32(*LIVE), 16)
            else:
                dl.add_circle(V2(ex, cy), ring, col32(1, 1, 1, 0.30 if eye_hov else 0.16), 24, 1.2)
                dl.add_circle_filled(V2(ex, cy), 2.5 * sc, col32(*DIM, 0.8), 12)
        elif eye is not None:
            dl.add_circle(V2(ex, cy), ring, col32(1, 1, 1, 0.30 if eye_hov else 0.16), 24, 1.2)
            if eye:
                dl.add_circle_filled(V2(ex, cy), 3.5 * sc, col32(*INK, 0.9), 16)
            else:
                dl.add_line(V2(ex - 5 * sc, cy + 5 * sc), V2(ex + 5 * sc, cy - 5 * sc), col32(*DIM), 1.2)

    def output_list(self):
        sc = self.sc
        p = self.project
        app = self.app
        row_h = 36 * sc
        dl = imgui.get_window_draw_list()
        width = imgui.get_content_region_avail().x
        for i, o in enumerate(p.outputs):
            imgui.push_id(f"out{i}")
            a = imgui.get_cursor_screen_pos()
            b = V2(a.x + width, a.y + row_h)
            if imgui.invisible_button("row", V2(width - 36 * sc, row_h)):
                self.set_active_output(i)
            hov = imgui.is_item_hovered()
            imgui.same_line(0, 0)
            if imgui.invisible_button("live", V2(36 * sc, row_h)):
                app.toggle_output(i)
            live_hov = imgui.is_item_hovered()
            if live_hov:
                imgui.set_tooltip("Hide output" if app.is_open(i) else "Show output")
            info = ("Fullscreen " if o.mode == "fullscreen" else "Window ") + f"{o.width}x{o.height}"
            self._row(dl, a, b, p.active_output == i, hov, ACCENT_2, o.name, info,
                      live=app.is_open(i), eye_hov=live_hov)
            imgui.pop_id()
        imgui.dummy(V2(0, 2 * sc))

    def layer_list(self):
        sc = self.sc
        p = self.project
        ao = p.active_output
        rows = ([("surface", i, s) for i, s in enumerate(p.surfaces) if s.output == ao] +
                [("mask", i, m) for i, m in enumerate(p.masks) if m.output == ao])
        row_h = 36 * sc
        n = max(1, min(len(rows), 6))
        imgui.push_style_var(imgui.StyleVar_.item_spacing, V2(0, 3 * sc))
        imgui.begin_child("layers", V2(0, n * (row_h + 3 * sc) + 2 * sc), 0, imgui.WindowFlags_.no_background)
        dl = imgui.get_window_draw_list()
        width = imgui.get_content_region_avail().x
        if not rows:
            a = imgui.get_cursor_screen_pos()
            T.draw_text(dl, "ui", 12.5, V2(a.x + 4 * sc, a.y + 10 * sc), col32(*FAINT), "No surfaces on this output yet")
        for kind, i, it in rows:
            imgui.push_id(f"{kind}{i}")
            a = imgui.get_cursor_screen_pos()
            b = V2(a.x + width, a.y + row_h)
            if imgui.invisible_button("row", V2(width - 36 * sc, row_h)):
                self.select(kind, i)
            hov = imgui.is_item_hovered()
            imgui.same_line(0, 0)
            if imgui.invisible_button("vis", V2(36 * sc, row_h)):
                it.visible = not it.visible
                it.touch()
            eye_hov = imgui.is_item_hovered()
            color = it.color if isinstance(it, Surface) else MASK_COL
            info = (f"{it.cols} x {it.rows} mesh" if (it.cols > 1 or it.rows > 1) else "Corner pin") \
                if isinstance(it, Surface) else f"Mask - {len(it.points)} pts"
            self._row(dl, a, b, self.sel_kind == kind and self.sel_index == i, hov, color, it.name, info,
                      dim=not it.visible, eye=it.visible, eye_hov=eye_hov)
            imgui.pop_id()
        imgui.end_child()
        imgui.pop_style_var()

    def output_props(self, o):
        sc = self.sc
        app = self.app
        idx = self.project.active_output
        self.label("Name")
        ch, o.name = imgui.input_text("##oname", o.name)
        self.label("Mode")
        modes = ["fullscreen", "window"]
        ch, mi = imgui.combo("##omode", modes.index(o.mode), ["Fullscreen on display", "Free window"])
        if ch:
            self.push()
            o.mode = modes[mi]
            if o.mode == "fullscreen":
                r = app.monitor_resolution(o.monitor)
                if r:
                    o.width, o.height = r
            app.place_output(idx)
        if o.mode == "fullscreen":
            mons = app.monitor_names()
            if mons:
                self.label("Display")
                ch, mi = imgui.combo("##mon", min(o.monitor, len(mons) - 1), mons)
                if ch:
                    o.monitor = mi
                    app.place_output(idx)
                    app.dirty = True
            self.label("Canvas")
            imgui.set_next_item_width(-1)
            avail = imgui.get_content_region_avail().x
            imgui.push_item_width(avail - 72 * sc)
            self.int_pair("##cw", "##ch", o, "width", "height", 64, 8192, avail - 72 * sc)
            imgui.pop_item_width()
            imgui.same_line()
            if self.button("Match", -1):
                r = app.monitor_resolution(o.monitor)
                if r:
                    o.width, o.height = r
                    app.dirty = True
        else:
            self.label("Position")
            if self.int_pair("##ox", "##oy", o, "x", "y", -10000, 10000):
                app.place_output(idx)
            self.label("Size")
            if self.int_pair("##ow", "##oh", o, "width", "height", 64, 8192):
                app.place_output(idx)
            for key, text in (("borderless", "No frame"), ("lock", "Lock size"), ("on_top", "On top")):
                self.label(text)
                ch, v = self.toggle(f"##{key}", getattr(o, key))
                if ch:
                    setattr(o, key, v)
                    app.place_output(idx)
                    app.dirty = True

    def int_pair(self, id_a, id_b, obj, attr_a, attr_b, lo, hi, total=None):
        sc = self.sc
        avail = total if total else imgui.get_content_region_avail().x
        sp = imgui.get_style().item_spacing.x
        fw = (avail - 2 * sp - 8 * sc) / 2
        imgui.set_next_item_width(fw)
        ch1, a = imgui.input_int(id_a, int(getattr(obj, attr_a)), 0)
        imgui.same_line()
        imgui.text_disabled("x" if attr_a == "width" else ",")
        imgui.same_line()
        imgui.set_next_item_width(fw)
        ch2, b = imgui.input_int(id_b, int(getattr(obj, attr_b)), 0)
        if ch1 or ch2:
            setattr(obj, attr_a, int(np.clip(a, lo, hi)))
            setattr(obj, attr_b, int(np.clip(b, lo, hi)))
            self.app.dirty = True
            return True
        return False

    # ================================================================ view
    def draw_view(self, m=14.0):
        sc = self.sc
        if self.tab_request:
            self.tab = self.tab_request
            self.tab_request = None
        dl = imgui.get_window_draw_list()
        wp = imgui.get_window_pos()
        ww = imgui.get_window_width()
        wh = imgui.get_window_height()
        a = V2(wp.x + 6 * sc, wp.y + m)
        b = V2(wp.x + ww - m, wp.y + wh - 4 * sc)
        T.glass(dl, a, b, 22 * sc, 0.9)
        bar_h = 60 * sc

        # ---- top bar
        seg_w = self.segmented(V2(a.x + 16 * sc, a.y + 14 * sc))
        p = self.project
        o = p.output
        x0 = a.x + 16 * sc + seg_w + 18 * sc
        T.draw_text(dl, "ui_medium", 14, V2(x0, a.y + 22 * sc), col32(*INK), o.name)
        x0 += T.text_size("ui_medium", 14, o.name).x + 12 * sc
        live = self.app.is_open()
        self.pill(dl, V2(x0, a.y + 19 * sc), ("Live  " if live else "Hidden  ") + f"{o.width} x {o.height}",
                  LIVE if live else DIM)
        canvas = self.out_canvas if self.tab == "output" else self.in_canvas
        y = a.y + 14 * sc
        x = b.x - 16 * sc
        x = self.top_toggle("guides", "Guides", self.projector_guides, x, y)
        if x[1]:
            self.projector_guides = not self.projector_guides
        x = self.top_toggle("calib", "Calibrate", self.calib, x[0] - 8 * sc, y)
        if x[1]:
            self.calib = not self.calib
        x = self.top_toggle("fit", "Fit", False, x[0] - 8 * sc, y)
        if x[1]:
            canvas.reset()
        z = f"{canvas.zoom * 100:.0f}%"
        zw = T.text_size("ui", 12.5, z).x
        T.draw_text(dl, "ui", 12.5, V2(x[0] - 14 * sc - zw, y + 7 * sc), col32(*DIM), z)

        # ---- canvas well
        pos = V2(a.x + 12 * sc, a.y + bar_h)
        size = V2(b.x - a.x - 24 * sc, b.y - a.y - bar_h - 12 * sc)
        r = 16 * sc
        dl.add_rect_filled(pos, V2(pos.x + size.x, pos.y + size.y), col32(0.012, 0.012, 0.022, 0.92), r)
        dl.add_rect(pos, V2(pos.x + size.x, pos.y + size.y), col32(1, 1, 1, 0.06), r, 1)
        dl.push_clip_rect(pos, V2(pos.x + size.x, pos.y + size.y), True)
        if self.tab == "output":
            self.output_view(dl, pos, size)
        else:
            self.input_view(dl, pos, size)
        dl.pop_clip_rect()

    def segmented(self, p):
        sc = self.sc
        dl = imgui.get_window_draw_list()
        h = 34 * sc
        items = [("output", "Output"), ("input", "Input")]
        widths = [T.text_size("ui_medium", 13, l).x + 40 * sc for _, l in items]
        total = sum(widths) + 8 * sc
        dl.add_rect_filled(p, V2(p.x + total, p.y + h), col32(0, 0, 0, 0.25), h / 2)
        dl.add_rect(p, V2(p.x + total, p.y + h), col32(1, 1, 1, 0.10), h / 2, 1)
        x = p.x + 4 * sc
        for (key, lab), w in zip(items, widths):
            imgui.set_cursor_screen_pos(V2(x, p.y + 4 * sc))
            if imgui.invisible_button("seg_" + key, V2(w, h - 8 * sc)):
                self.tab = key
                self.drag = None
            on = self.tab == key
            a, b = V2(x, p.y + 4 * sc), V2(x + w, p.y + h - 4 * sc)
            if on:
                T.glass(dl, a, b, (h - 8 * sc) / 2, 1.6, drop=False)
                dl.add_rect_filled(V2(x + w / 2 - 9 * sc, b.y - 3 * sc), V2(x + w / 2 + 9 * sc, b.y - 1.5 * sc),
                                   col32(*ACCENT), 2)
            elif imgui.is_item_hovered():
                dl.add_rect_filled(a, b, col32(1, 1, 1, 0.05), (h - 8 * sc) / 2)
            ts = T.text_size("ui_medium", 13, lab)
            T.draw_text(dl, "ui_medium", 13, V2(x + (w - ts.x) / 2, p.y + (h - ts.y) / 2 - 1 * sc),
                        col32(*(INK if on else DIM)), lab)
            x += w
        return total

    def top_toggle(self, sid, text, on, right_x, y):
        sc = self.sc
        dl = imgui.get_window_draw_list()
        h = 32 * sc
        w = T.text_size("ui_medium", 12.5, text).x + 30 * sc
        a = V2(right_x - w, y + 1 * sc)
        b = V2(right_x, y + 1 * sc + h)
        imgui.set_cursor_screen_pos(a)
        clicked = imgui.invisible_button("tt_" + sid, V2(w, h))
        hov = imgui.is_item_hovered()
        if on:
            T.gradient_fill(dl, a, b, h / 2, col32(*ACCENT, 0.32), col32(0.40, 0.38, 0.95, 0.20), a, V2(b.x, b.y))
            dl.add_rect(a, b, col32(*T.mix(ACCENT, (1, 1, 1), 0.3), 0.6), h / 2, 1)
        else:
            dl.add_rect_filled(a, b, col32(1, 1, 1, 0.08 if hov else 0.045), h / 2)
            dl.add_rect(a, b, col32(1, 1, 1, 0.11), h / 2, 1)
        ts = T.text_size("ui_medium", 12.5, text)
        T.draw_text(dl, "ui_medium", 12.5, V2(a.x + (w - ts.x) / 2, a.y + (h - ts.y) / 2),
                    col32(*(INK if (on or hov) else DIM)), text)
        return a.x, clicked

    def _frame(self, dl, canvas, tex, uv_flip):
        a = canvas.v((0, 0))
        b = canvas.v((1, 1))
        T.shadow(dl, a, b, 0, 1.2, offset=8)
        dl.add_rect_filled(a, b, col32(0, 0, 0))
        if tex is not None:
            uv0, uv1 = (V2(0, 1), V2(1, 0)) if uv_flip else (V2(0, 0), V2(1, 1))
            dl.add_image(imgui.ImTextureRef(tex.glo), a, b, uv0, uv1)
        dl.add_rect(V2(a.x - 0.5, a.y - 0.5), V2(b.x + 0.5, b.y + 0.5), col32(1, 1, 1, 0.14), 0, 1)

    def rulers(self, dl, pos, size, cv, w_px, h_px, mouse, hovered):
        sc = self.sc
        t = 22 * sc
        tl = 40 * sc
        strip = col32(1, 1, 1, 0.025)
        dl.add_rect_filled(pos, V2(pos.x + size.x, pos.y + t), strip)
        dl.add_rect_filled(V2(pos.x, pos.y + t), V2(pos.x + tl, pos.y + size.y), strip)
        dl.add_line(V2(pos.x, pos.y + t), V2(pos.x + size.x, pos.y + t), col32(1, 1, 1, 0.07), 1)
        dl.add_line(V2(pos.x + tl, pos.y), V2(pos.x + tl, pos.y + size.y), col32(1, 1, 1, 0.07), 1)
        ppx = cv.extent[0] / w_px
        step = 10
        for s_ in (10, 20, 50, 100, 200, 250, 500, 1000):
            step = s_
            if s_ * ppx >= 9 * sc:
                break
        major = step * 5 if step != 250 else 1000
        tick = col32(1, 1, 1, 0.14)
        tick_m = col32(1, 1, 1, 0.34)
        lab = col32(*DIM, 0.85)
        v = 0
        while v <= w_px:
            x = cv.origin[0] + v * ppx
            if pos.x + tl <= x <= pos.x + size.x:
                mj = v % major == 0
                dl.add_line(V2(x, pos.y + t - (7 if mj else 3.5) * sc), V2(x, pos.y + t), tick_m if mj else tick, 1)
                if mj:
                    T.draw_text(dl, "mono", 7.5, V2(x + 3 * sc, pos.y + 4 * sc), lab, f"{v}")
            v += step
        ppy = cv.extent[1] / h_px
        v = 0
        while v <= h_px:
            y = cv.origin[1] + v * ppy
            if pos.y + t <= y <= pos.y + size.y:
                mj = v % major == 0
                dl.add_line(V2(pos.x + tl - (7 if mj else 3.5) * sc, y), V2(pos.x + tl, y), tick_m if mj else tick, 1)
                if mj:
                    T.draw_text(dl, "mono", 7.5, V2(pos.x + 7 * sc, y + 2 * sc), lab, f"{v}")
            v += step
        if hovered:
            mx, my = mouse
            dl.add_triangle_filled(V2(mx - 4 * sc, pos.y + t - 6 * sc), V2(mx + 4 * sc, pos.y + t - 6 * sc),
                                   V2(mx, pos.y + t), col32(*ACCENT))
            dl.add_triangle_filled(V2(pos.x + tl - 6 * sc, my - 4 * sc), V2(pos.x + tl - 6 * sc, my + 4 * sc),
                                   V2(pos.x + tl, my), col32(*ACCENT))
        T.draw_text(dl, "mono", 7.5, V2(pos.x + 8 * sc, pos.y + 5 * sc), col32(*FAINT), "px")

    def cursor_readout(self, dl, pos, size, cv, w_px, h_px, mouse):
        sc = self.sc
        n = cv.to_norm(mouse)
        if not (0 <= n[0] <= 1 and 0 <= n[1] <= 1) or self.drag:
            return
        mx, my = mouse
        c = col32(1, 1, 1, 0.06)
        dl.add_line(V2(pos.x, my), V2(pos.x + size.x, my), c, 1)
        dl.add_line(V2(mx, pos.y), V2(mx, pos.y + size.y), c, 1)
        txt = f"{n[0] * w_px:.0f}, {n[1] * h_px:.0f}"
        tw = T.text_size("mono", 9, txt).x
        a = V2(mx + 14 * sc, my + 14 * sc)
        b = V2(a.x + tw + 20 * sc, a.y + 24 * sc)
        T.glass(dl, a, b, 12 * sc, 1.4)
        dl.add_rect_filled(a, b, col32(0.05, 0.05, 0.09, 0.55), 12 * sc)
        T.draw_text(dl, "mono", 9, V2(a.x + 10 * sc, a.y + 5 * sc), col32(*INK), txt)

    def callout(self, dl, anchor, num, name, info, color, strong):
        sc = self.sc
        al = 1.0 if strong else 0.6
        ax, ay = anchor.x, anchor.y
        kx, ky = ax + 20 * sc, ay - 22 * sc
        dl.add_circle_filled(anchor, 3 * sc, col32(*color, al), 12)
        dl.add_bezier_quadratic(anchor, V2(ax, ky), V2(kx, ky), col32(*color, al * 0.7), 1.2)
        tw = T.text_size("ui_medium", 12, name).x
        iw = T.text_size("ui", 11, info).x
        h = 26 * sc
        w = tw + iw + 44 * sc
        r0, r1 = V2(kx, ky - h / 2), V2(kx + w, ky + h / 2)
        T.glass(dl, r0, r1, h / 2, 1.5 if strong else 1.0, drop=strong)
        dl.add_rect_filled(r0, r1, col32(0.04, 0.04, 0.08, 0.55 if strong else 0.45), h / 2)
        cx = r0.x + 13 * sc
        dl.add_circle_filled(V2(cx, ky), 4 * sc, col32(*color, al), 16)
        T.draw_text(dl, "ui_medium", 12, V2(cx + 10 * sc, ky - 8 * sc), col32(*INK, al), name)
        T.draw_text(dl, "ui", 11, V2(cx + 18 * sc + tw, ky - 7.5 * sc), col32(*DIM, al), info)

    def handle(self, dl, c, color, hot, corner, label=None):
        sc = self.sc
        r = (6.0 if corner else 4.5) * sc
        if hot:
            T.glow(dl, c, 26 * sc, ACCENT, 2.0)
            dl.add_circle_filled(V2(c.x, c.y + 1.5 * sc), r + 3 * sc, col32(0, 0, 0.05, 0.4), 24)
            dl.add_circle_filled(c, r + 2.5 * sc, col32(1, 1, 1), 24)
            dl.add_circle_filled(c, r - 0.5 * sc, col32(*ACCENT), 24)
            if label:
                tw = T.text_size("mono", 8.5, label).x
                a = V2(c.x + 12 * sc, c.y - 28 * sc)
                b = V2(a.x + tw + 16 * sc, a.y + 20 * sc)
                dl.add_rect_filled(a, b, col32(*ACCENT, 0.9), 10 * sc)
                T.draw_text(dl, "mono", 8.5, V2(a.x + 8 * sc, a.y + 3.5 * sc), col32(1, 1, 1), label)
        else:
            dl.add_circle_filled(V2(c.x, c.y + 1 * sc), r + 1.5 * sc, col32(0, 0, 0.05, 0.45), 20)
            dl.add_circle_filled(c, r, col32(0.96, 0.96, 1.0, 0.95), 20)
            dl.add_circle(c, r, col32(*color), 20, 2.0 * sc)

    def loupe(self, dl, cv, mouse, tex, uv_flip):
        if tex is None:
            return
        sc = self.sc
        size = 140 * sc
        n = cv.to_norm(mouse)
        rad = 0.025 / max(cv.zoom, 1)
        ar = cv.extent[1] / cv.extent[0]
        u0, u1 = n[0] - rad * ar, n[0] + rad * ar
        v0, v1 = n[1] - rad, n[1] + rad
        if uv_flip:
            v0, v1 = 1 - v0, 1 - v1
        a = V2(mouse[0] + 30 * sc, mouse[1] - size - 30 * sc)
        b = V2(a.x + size, a.y + size)
        pad = 6 * sc
        T.glass(dl, V2(a.x - pad, a.y - pad), V2(b.x + pad, b.y + 26 * sc), 18 * sc, 1.6)
        dl.add_image_rounded(imgui.ImTextureRef(tex.glo), a, b, V2(u0, v0), V2(u1, v1), col32(1, 1, 1), 13 * sc)
        cx, cy = (a.x + b.x) / 2, (a.y + b.y) / 2
        ac = col32(*ACCENT)
        dl.add_circle(V2(cx, cy), 7 * sc, ac, 24, 1.4)
        dl.add_line(V2(cx - 16 * sc, cy), V2(cx - 9 * sc, cy), ac, 1.4)
        dl.add_line(V2(cx + 9 * sc, cy), V2(cx + 16 * sc, cy), ac, 1.4)
        dl.add_line(V2(cx, cy - 16 * sc), V2(cx, cy - 9 * sc), ac, 1.4)
        dl.add_line(V2(cx, cy + 9 * sc), V2(cx, cy + 16 * sc), ac, 1.4)
        T.draw_text(dl, "ui", 11, V2(a.x + 4 * sc, b.y + 5 * sc), col32(*DIM), "Loupe")
        z = f"{1 / (2 * rad):.0f}x"
        T.draw_text(dl, "ui_medium", 11, V2(b.x - T.text_size("ui_medium", 11, z).x - 4 * sc, b.y + 5 * sc), col32(*ACCENT), z)

    def draw_status(self, m=14.0):
        sc = self.sc
        io = imgui.get_io()
        dl = imgui.get_window_draw_list()
        wp = imgui.get_window_pos()
        ww = imgui.get_window_width()
        a = V2(wp.x + m, wp.y + 6 * sc)
        b = V2(wp.x + ww - m, wp.y + 6 * sc + 36 * sc)
        T.glass(dl, a, b, 18 * sc, 0.9)
        y = a.y + 10 * sc
        x = a.x + 16 * sc
        state, scol = self.source_state()

        def seg(text, color, dot=None):
            nonlocal x
            if dot:
                T.glow(dl, V2(x + 4 * sc, y + 8 * sc), 12 * sc, dot, 1.2)
                dl.add_circle_filled(V2(x + 4 * sc, y + 8 * sc), 3.5 * sc, col32(*dot), 16)
                x += 14 * sc
            T.draw_text(dl, "ui", 12, V2(x, y), col32(*color), text)
            x += T.text_size("ui", 12, text).x + 14 * sc
            dl.add_circle_filled(V2(x, y + 8 * sc), 1.5 * sc, col32(1, 1, 1, 0.25), 8)
            x += 14 * sc

        seg(state.capitalize(), scol, scol)
        seg(f"{io.framerate:.0f} fps", DIM)
        seg(f"{self.project.width} x {self.project.height}", DIM)
        n_open = self.app.open_count()
        seg(f"{n_open} of {len(self.project.outputs)} outputs live", LIVE if n_open else DIM, LIVE if n_open else None)
        it = self.selected()
        if it is not None:
            pt = ""
            if isinstance(self.sel_point, tuple):
                pt = f"  - point {self.sel_point[1]},{self.sel_point[0]}"
            elif isinstance(self.sel_point, int):
                pt = f"  - point {self.sel_point}"
            seg(it.name + pt, INK)
        if self.toast and imgui.get_time() - self.toast_time < 4:
            msg, col = self.toast, ACCENT
        else:
            msg = "Drag to move  -  Shift fine  -  Alt corner only  -  Arrows nudge  -  Tab next point  -  Wheel zoom  -  Space pan"
            col = FAINT
        mw = T.text_size("ui", 11.5, msg).x
        if b.x - 16 * sc - mw > x:
            T.draw_text(dl, "ui", 11.5, V2(b.x - 16 * sc - mw, y + 1 * sc), col32(*col), msg)

    def _canvas_input(self, canvas, pos, size):
        imgui.set_cursor_screen_pos(pos)
        imgui.invisible_button("canvas", size, imgui.ButtonFlags_.mouse_button_left |
                               imgui.ButtonFlags_.mouse_button_right | imgui.ButtonFlags_.mouse_button_middle)
        io = imgui.get_io()
        hovered = imgui.is_item_hovered()
        mouse = np.array([io.mouse_pos.x, io.mouse_pos.y])
        if hovered and io.mouse_wheel != 0:
            canvas.zoom_at(mouse, 1.15 ** io.mouse_wheel, pos, size)
        panning = imgui.is_mouse_down(2) or (imgui.is_key_down(imgui.Key.space) and imgui.is_mouse_down(0))
        if imgui.is_item_active() and panning:
            canvas.pan += np.array([io.mouse_delta.x, io.mouse_delta.y])
            return hovered, mouse, True
        return hovered, mouse, False

    def dashed(self, dl, pts, color, thick, dash=7.0, gap=5.0):
        sc = self.sc
        n = len(pts)
        for k in range(n):
            a = np.array([pts[k].x, pts[k].y])
            b = np.array([pts[(k + 1) % n].x, pts[(k + 1) % n].y])
            L = np.linalg.norm(b - a)
            if L < 1e-3:
                continue
            d = (b - a) / L
            t = 0.0
            while t < L:
                e = min(t + dash * sc, L)
                p0, p1 = a + d * t, a + d * e
                dl.add_line(V2(*p0), V2(*p1), color, thick)
                t = e + gap * sc

    def output_view(self, dl, pos, size):
        p = self.project
        cv = self.out_canvas
        sc = self.sc
        io = imgui.get_io()
        cv.layout(V2(pos.x + 40 * sc, pos.y + 22 * sc), V2(size.x - 40 * sc, size.y - 22 * sc), p.width / p.height)
        hovered, mouse, panning = self._canvas_input(cv, pos, size)
        self._frame(dl, cv, self.app.renderer.tex(self.project.active_output), True)

        ao = p.active_output
        for i, s in enumerate(p.surfaces):
            if not s.visible or s.output != ao:
                continue
            sel = self.sel_kind == "surface" and self.sel_index == i
            b = geo.boundary(s.points, 20, s.smooth)
            dl.add_polyline([cv.v(q) for q in b], col32(*s.color, 1.0 if sel else 0.5),
                            (1.6 if sel else 1.0) * sc, imgui.ImDrawFlags_.closed)
            if sel:
                g = s.points
                for r in range(g.shape[0]):
                    for cc in range(g.shape[1]):
                        if cc + 1 < g.shape[1]:
                            dl.add_line(cv.v(g[r, cc]), cv.v(g[r, cc + 1]), col32(*s.color, 0.25), 1)
                        if r + 1 < g.shape[0]:
                            dl.add_line(cv.v(g[r, cc]), cv.v(g[r + 1, cc]), col32(*s.color, 0.25), 1)
        for i, m in enumerate(p.masks):
            if not m.visible or m.output != ao:
                continue
            sel = self.sel_kind == "mask" and self.sel_index == i
            self.dashed(dl, [cv.v(q) for q in m.points], col32(*MASK_COL, 1.0 if sel else 0.55), (1.4 if sel else 1.0) * sc)
        # callouts on top of outlines
        for i, s in enumerate(p.surfaces):
            if not s.visible or s.output != ao:
                continue
            sel = self.sel_kind == "surface" and self.sel_index == i
            c = cv.v(geo.eval_grid(s.points, [0.5], [0.5], s.smooth)[0, 0])
            self.callout(dl, c, f"{i + 1:02d}", s.name, f"{s.cols}x{s.rows}", s.color, sel)
        for i, m in enumerate(p.masks):
            if not m.visible or m.output != ao:
                continue
            sel = self.sel_kind == "mask" and self.sel_index == i
            c = cv.v(m.points.mean(axis=0))
            self.callout(dl, c, f"{len(p.surfaces) + i + 1:02d}", m.name, "MASK", MASK_COL, sel)
        # handles
        it = self.selected()
        if it is not None and it.output != ao:
            it = None
        if isinstance(it, Surface) and it.visible:
            g = it.points
            for r in range(g.shape[0]):
                for cc in range(g.shape[1]):
                    hot = self.sel_point == (r, cc)
                    lab = f"{g[r, cc, 0] * p.width:.0f}, {g[r, cc, 1] * p.height:.0f}" if hot else None
                    self.handle(dl, cv.v(g[r, cc]), it.color, hot, self._is_corner(it, (r, cc)), lab)
        elif isinstance(it, Mask) and it.visible:
            for k, q in enumerate(it.points):
                hot = self.sel_point == k
                lab = f"{q[0] * p.width:.0f}, {q[1] * p.height:.0f}" if hot else None
                self.handle(dl, cv.v(q), MASK_COL, hot, True, lab)

        if hovered:
            self.cursor_readout(dl, pos, size, cv, p.width, p.height, mouse)
        self.rulers(dl, pos, size, cv, p.width, p.height, mouse, hovered)
        if panning:
            return

        # ---- interaction
        if hovered and imgui.is_mouse_clicked(0) and not imgui.is_key_down(imgui.Key.space):
            hit = self._pick_point(cv, mouse)
            if hit is not None:
                self.push()
                self.sel_point = hit
                self.drag = ("point", mouse, self._snapshot(), hit)
            else:
                kind, idx = self._pick_body(cv, mouse)
                if kind:
                    self.push()
                    self.select(kind, idx)
                    self.drag = ("body", mouse, self._snapshot(), None)
                else:
                    self.sel_point = None
        if hovered and imgui.is_mouse_double_clicked(0) and isinstance(it, Mask):
            self._insert_mask_point(cv, mouse, it)
        if hovered and imgui.is_mouse_clicked(1) and isinstance(it, Mask):
            hit = self._pick_point(cv, mouse)
            if hit is not None and len(it.points) > 3:
                self.push()
                it.points = np.delete(it.points, hit, axis=0)
                it.touch()
                self.sel_point = None

        if self.drag and imgui.is_mouse_down(0):
            it = self.selected()
            mode, start, base, hit = self.drag
            d = (mouse - start) / cv.extent * (0.25 if io.key_shift else 1.0)
            if it is not None and base is not None:
                if mode == "body":
                    it.points = base + d
                elif isinstance(it, Surface):
                    self._move_surface_point(it, hit, base[hit] + d, keep_mesh=not io.key_alt, base=base)
                else:
                    it.points = base.copy()
                    it.points[hit] = base[hit] + d
                it.touch()
                self.app.dirty = True
        elif self.drag and not imgui.is_mouse_down(0):
            self.drag = None

        if self.drag and self.drag[0] == "point":
            self.loupe(dl, cv, mouse, self.app.renderer.tex(self.project.active_output), True)

    def _snapshot(self):
        it = self.selected()
        if it is None:
            return None
        if self.tab == "input" and isinstance(it, Surface):
            return it.source.copy()
        return it.points.copy()

    def _pick_point(self, cv, mouse):
        it = self.selected()
        if it is None or (self.tab == "output" and it.output != self.project.active_output):
            return None
        best, best_d = None, PICK_R * self.sc
        if isinstance(it, Surface):
            g = it.points
            for r in range(g.shape[0]):
                for c in range(g.shape[1]):
                    d = np.linalg.norm(cv.to_screen(g[r, c]) - mouse)
                    if d < best_d:
                        best, best_d = (r, c), d
        else:
            for k, q in enumerate(it.points):
                d = np.linalg.norm(cv.to_screen(q) - mouse)
                if d < best_d:
                    best, best_d = k, d
        return best

    def _pick_body(self, cv, mouse):
        n = cv.to_norm(mouse)
        p = self.project
        ao = p.active_output
        for i in range(len(p.masks) - 1, -1, -1):
            m = p.masks[i]
            if m.visible and m.output == ao and geo.point_in_poly(n, m.points):
                return "mask", i
        if self.sel_kind == "surface" and 0 <= self.sel_index < len(p.surfaces):
            s = p.surfaces[self.sel_index]
            if s.visible and s.output == ao and geo.point_in_poly(n, geo.boundary(s.points, 12, s.smooth)):
                return "surface", self.sel_index
        for i in range(len(p.surfaces) - 1, -1, -1):
            s = p.surfaces[i]
            if s.visible and s.output == ao and geo.point_in_poly(n, geo.boundary(s.points, 12, s.smooth)):
                return "surface", i
        return None, None

    def _insert_mask_point(self, cv, mouse, m):
        best, best_d = None, 14 * self.sc
        for k in range(len(m.points)):
            a = cv.to_screen(m.points[k])
            b = cv.to_screen(m.points[(k + 1) % len(m.points)])
            d = geo.dist_to_segment(mouse, a, b)
            if d < best_d:
                best, best_d = k, d
        if best is not None:
            self.push()
            m.points = np.insert(m.points, best + 1, cv.to_norm(mouse), axis=0)
            m.touch()
            self.sel_point = best + 1
            self.drag = None

    def input_view(self, dl, pos, size):
        src = self.app.source
        tex = src.texture if src else None
        w, h = src.size if src else (16, 9)
        cv = self.in_canvas
        sc = self.sc
        io = imgui.get_io()
        cv.layout(V2(pos.x + 40 * sc, pos.y + 22 * sc), V2(size.x - 40 * sc, size.y - 22 * sc), w / max(h, 1))
        hovered, mouse, panning = self._canvas_input(cv, pos, size)
        flip = bool(src.origin_bottom ^ self.project.flip_input) if src else False
        self._frame(dl, cv, tex, flip)
        p = self.project
        cols, rows = p.input_cols, p.input_rows
        if cols * rows > 1:
            gc = col32(*ACCENT, 0.7)
            for c in range(1, cols):
                dl.add_line(cv.v((c / cols, 0)), cv.v((c / cols, 1)), gc, 1)
            for r in range(1, rows):
                dl.add_line(cv.v((0, r / rows)), cv.v((1, r / rows)), gc, 1)
            for k in range(cols * rows):
                a = cv.v(((k % cols) / cols, (k // cols) / rows))
                self.pill(dl, V2(a.x + 8 * sc, a.y + 8 * sc), f"Camera {k + 1}", (0.78, 0.74, 1.0), backing=True)
        ao = p.active_output
        for i, s in enumerate(p.surfaces):
            if s.output != ao:
                continue
            sel = self.sel_kind == "surface" and self.sel_index == i
            dl.add_polyline([cv.v(q) for q in s.source], col32(*s.color, 1.0 if sel else 0.45),
                            (1.6 if sel else 1.0) * sc, imgui.ImDrawFlags_.closed)
            self.callout(dl, cv.v(s.source.mean(axis=0)), f"{i + 1:02d}", s.name, "SRC", s.color, sel)
        s = self.selected()
        if s is not None and s.output != ao:
            s = None
        if isinstance(s, Surface):
            for k, q in enumerate(s.source):
                hot = self.sel_point == k
                lab = f"{q[0] * w:.0f}, {q[1] * h:.0f}" if hot else None
                self.handle(dl, cv.v(q), s.color, hot, True, lab)
        if hovered:
            self.cursor_readout(dl, pos, size, cv, w, h, mouse)
        self.rulers(dl, pos, size, cv, w, h, mouse, hovered)
        if panning:
            return
        if not isinstance(s, Surface):
            if hovered and imgui.is_mouse_clicked(0):
                self._pick_source(cv, mouse)
            return
        if hovered and imgui.is_mouse_clicked(0) and not imgui.is_key_down(imgui.Key.space):
            hit, best_d = None, PICK_R * sc
            for k, q in enumerate(s.source):
                d = np.linalg.norm(cv.to_screen(q) - mouse)
                if d < best_d:
                    hit, best_d = k, d
            if hit is not None:
                self.push()
                self.sel_point = hit
                self.drag = ("point", mouse, s.source.copy(), hit)
            elif geo.point_in_poly(cv.to_norm(mouse), s.source):
                self.push()
                self.drag = ("body", mouse, s.source.copy(), None)
            else:
                self._pick_source(cv, mouse)
        if self.drag and imgui.is_mouse_down(0):
            s = self.selected()
            if isinstance(s, Surface):
                mode, start, base, hit = self.drag
                d = (mouse - start) / cv.extent * (0.25 if io.key_shift else 1.0)
                if mode == "body":
                    s.source = base + d
                else:
                    s.source = base.copy()
                    s.source[hit] = base[hit] + d
                s.touch()
                self.app.dirty = True
        elif self.drag:
            self.drag = None
        if self.drag and self.drag[0] == "point":
            self.loupe(dl, cv, mouse, tex, flip)

    def _pick_source(self, cv, mouse):
        n = cv.to_norm(mouse)
        p = self.project
        for i in range(len(p.surfaces) - 1, -1, -1):
            if p.surfaces[i].output == p.active_output and geo.point_in_poly(n, p.surfaces[i].source):
                self.select("surface", i)
                return
        self.sel_point = None

    # ================================================================ status / keys
    def after_history(self):
        self.app.rebuild_source()
        for idx in list(self.app.outs):
            self.app.place_output(idx)

    def shortcuts(self):
        io = imgui.get_io()
        if io.want_text_input:
            return
        K = imgui.Key
        ctrl = io.key_ctrl
        step = 10 if io.key_shift else 1
        if ctrl and imgui.is_key_pressed(K.z, False):
            if io.key_shift:
                self.app.history.redo()
            else:
                self.app.history.undo()
            self.after_history()
        elif ctrl and imgui.is_key_pressed(K.y, False):
            self.app.history.redo()
            self.after_history()
        elif ctrl and imgui.is_key_pressed(K.s, False):
            self.save_project()
        elif ctrl and imgui.is_key_pressed(K.d, False):
            self.duplicate()
        elif not ctrl:
            self.handle_key_actions(imgui.is_key_pressed, step)

    def handle_key_actions(self, pressed, step):
        K = imgui.Key
        if pressed(K.left_arrow):
            self.nudge(-step, 0)
        if pressed(K.right_arrow):
            self.nudge(step, 0)
        if pressed(K.up_arrow):
            self.nudge(0, -step)
        if pressed(K.down_arrow):
            self.nudge(0, step)
        if pressed(K.tab, False):
            self.cycle_point(-1 if imgui.get_io().key_shift else 1)
        if pressed(K.delete, False):
            self.delete_selected()
        if pressed(K.c, False):
            self.calib = not self.calib
        if pressed(K.g, False):
            self.projector_guides = not self.projector_guides
        if pressed(K.f, False):
            self.app.toggle_output()
        if pressed(K.home, False):
            (self.out_canvas if self.tab == "output" else self.in_canvas).reset()
        if pressed(K.escape, False):
            self.sel_point = None
