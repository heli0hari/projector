import numpy as np
from imgui_bundle import imgui

from . import geometry as geo
from . import theme as T
from .model import Surface, Mask, Project
from .theme import col32, ACCENT, INK, DIM, FAINT, PANEL, LIVE, WARN

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

    # ================================================================ widgets
    def section(self, num, title):
        sc = self.sc
        imgui.dummy(V2(0, 10 * sc))
        dl = imgui.get_window_draw_list()
        p = imgui.get_cursor_screen_pos()
        right = p.x + imgui.get_content_region_avail().x
        T.draw_text(dl, "mono", 10, p, col32(*ACCENT), num)
        x = p.x + T.text_size("mono", 10, num).x + 8 * sc
        T.draw_text(dl, "mono_medium", 10, V2(x, p.y), col32(*INK), title.upper(), 1.6)
        x += T.tracked_width("mono_medium", 10, title.upper(), 1.6) + 10 * sc
        y = p.y + 7 * sc
        dl.add_line(V2(x, y), V2(right, y), col32(*FAINT, 0.9), 1)
        dl.add_line(V2(right, y - 3 * sc), V2(right, y + 3 * sc), col32(*FAINT), 1)
        imgui.dummy(V2(0, 16 * sc))

    def label(self, text, col_w=88):
        sc = self.sc
        p = imgui.get_cursor_screen_pos()
        h = imgui.get_frame_height()
        dl = imgui.get_window_draw_list()
        T.draw_text(dl, "mono", 9.5, V2(p.x, p.y + (h - 12 * sc) / 2), col32(*DIM), text.upper(), 1.0)
        imgui.dummy(V2(col_w * sc - imgui.get_style().item_spacing.x, h))
        imgui.same_line()
        imgui.set_next_item_width(-1)

    def button(self, text, width=0.0, kind="ghost", active=False):
        """kind: ghost | primary"""
        sc = self.sc
        pushed = 0
        if kind == "primary" or active:
            imgui.push_style_color(imgui.Col_.button, col32(*ACCENT, 1.0))
            imgui.push_style_color(imgui.Col_.button_hovered, col32(0.92, 1.0, 0.55))
            imgui.push_style_color(imgui.Col_.button_active, col32(0.75, 0.86, 0.35))
            imgui.push_style_color(imgui.Col_.text, col32(0.03, 0.035, 0.03))
            imgui.push_style_color(imgui.Col_.border, col32(*ACCENT, 1.0))
            pushed = 5
        else:
            imgui.push_style_color(imgui.Col_.border, col32(1, 1, 1, 0.12))
            pushed = 1
        with T.use("mono_medium", 10.5):
            clicked = imgui.button(text.upper(), V2(width, 30 * sc))
        imgui.pop_style_color(pushed)
        return clicked

    def button_row(self, labels, kinds=None):
        n = len(labels)
        sp = imgui.get_style().item_spacing.x
        w = (imgui.get_content_region_avail().x - sp * (n - 1)) / n
        hit = None
        for i, l in enumerate(labels):
            if i:
                imgui.same_line()
            k = kinds[i] if kinds else "ghost"
            if self.button(l, w, k):
                hit = i
        return hit

    def pill(self, dl, pos, text, color, size=9.5, filled=False):
        sc = self.sc
        tw = T.tracked_width("mono_medium", size, text, 1.2)
        dot = 10 * sc
        w = tw + dot + 16 * sc
        h = 20 * sc
        a, b = pos, V2(pos.x + w, pos.y + h)
        if filled:
            dl.add_rect_filled(a, b, col32(*color, 0.14), h / 2)
        dl.add_rect(a, b, col32(*color, 0.55), h / 2, 1)
        dl.add_circle_filled(V2(a.x + 10 * sc, a.y + h / 2), 3 * sc, col32(*color))
        T.draw_text(dl, "mono_medium", size, V2(a.x + 8 * sc + dot, a.y + (h - 12 * sc) / 2), col32(*color), text, 1.2)
        return w

    def logo(self, dl, p):
        """A corner-pinned plane inside registration brackets."""
        sc = self.sc
        s = 26 * sc
        x0, y0 = p.x, p.y
        x1, y1 = x0 + s, y0 + s
        L = 6 * sc
        c = col32(*INK, 0.9)
        for (cx, cy, dx, dy) in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x1, y1, -1, -1), (x0, y1, 1, -1)):
            dl.add_line(V2(cx, cy), V2(cx + dx * L, cy), c, 1.2 * sc)
            dl.add_line(V2(cx, cy), V2(cx, cy + dy * L), c, 1.2 * sc)
        q = [V2(x0 + 6.5 * sc, y0 + 8 * sc), V2(x1 - 5 * sc, y0 + 5.5 * sc),
             V2(x1 - 7 * sc, y1 - 6 * sc), V2(x0 + 5.5 * sc, y1 - 8.5 * sc)]
        dl.add_convex_poly_filled(q, col32(*ACCENT))
        for v in q:
            dl.add_rect_filled(V2(v.x - 1.3 * sc, v.y - 1.3 * sc), V2(v.x + 1.3 * sc, v.y + 1.3 * sc), col32(0.03, 0.03, 0.03))
        return s

    def card(self, height, draw_fn):
        sc = self.sc
        dl = imgui.get_window_draw_list()
        a = imgui.get_cursor_screen_pos()
        w = imgui.get_content_region_avail().x
        b = V2(a.x + w, a.y + height * sc)
        dl.add_rect_filled(a, b, col32(1, 1, 1, 0.028), 10 * sc)
        dl.add_rect(a, b, col32(1, 1, 1, 0.08), 10 * sc, 1)
        draw_fn(dl, a, b)
        imgui.dummy(V2(w, height * sc))

    # ================================================================ frame
    def draw(self, fb_w, fb_h):
        io = imgui.get_io()
        sc = self.sc
        side_w = 352 * sc
        status_h = 30 * sc
        flags = (imgui.WindowFlags_.no_title_bar | imgui.WindowFlags_.no_resize |
                 imgui.WindowFlags_.no_move | imgui.WindowFlags_.no_collapse |
                 imgui.WindowFlags_.no_bring_to_front_on_focus)
        w, h = io.display_size.x, io.display_size.y

        imgui.set_next_window_pos(V2(0, 0))
        imgui.set_next_window_size(V2(side_w, h - status_h))
        imgui.begin("##side", None, flags)
        self.draw_sidebar()
        imgui.end()

        imgui.set_next_window_pos(V2(side_w, 0))
        imgui.set_next_window_size(V2(w - side_w, h - status_h))
        imgui.push_style_var(imgui.StyleVar_.window_padding, V2(0, 0))
        imgui.begin("##view", None, flags | imgui.WindowFlags_.no_scrollbar | imgui.WindowFlags_.no_scroll_with_mouse)
        imgui.pop_style_var()
        self.draw_view()
        imgui.end()

        imgui.set_next_window_pos(V2(0, h - status_h))
        imgui.set_next_window_size(V2(w, status_h))
        imgui.push_style_var(imgui.StyleVar_.window_padding, V2(0, 0))
        imgui.begin("##status", None, flags | imgui.WindowFlags_.no_scrollbar)
        imgui.pop_style_var()
        self.draw_status()
        imgui.end()

        # divider between sidebar and view
        fg = imgui.get_foreground_draw_list()
        fg.add_line(V2(side_w, 0), V2(side_w, h - status_h), col32(1, 1, 1, 0.06), 1)
        self.shortcuts()

    # ================================================================ sidebar
    def draw_sidebar(self):
        p = self.project
        app = self.app
        sc = self.sc
        dl = imgui.get_window_draw_list()

        # ---- brand
        o = imgui.get_cursor_screen_pos()
        right = o.x + imgui.get_content_region_avail().x
        s = self.logo(dl, o)
        T.draw_text(dl, "ui_medium", 17, V2(o.x + s + 12 * sc, o.y - 1 * sc), col32(*INK), "PROJMAP", 3.2)
        T.draw_text(dl, "mono", 8.5, V2(o.x + s + 12 * sc, o.y + 18 * sc), col32(*DIM), "PROJECTION SYSTEM", 1.6)
        tag = "V1.0"
        tw = T.tracked_width("mono", 8.5, tag, 1.2)
        T.draw_text(dl, "mono", 8.5, V2(right - tw, o.y + 2 * sc), col32(*DIM), tag, 1.2)
        imgui.dummy(V2(0, s + 14 * sc))

        # ---- telemetry card
        state, scol = self.source_state()
        src = app.source

        def tele(dl, a, b):
            pad = 14 * sc
            T.draw_text(dl, "mono", 8.5, V2(a.x + pad, a.y + pad), col32(*DIM), f"O{p.active_output + 1}  {p.output.name.upper()[:18]}", 1.4)
            pw = T.tracked_width("mono_medium", 9, state, 1.2) + 26 * sc
            self.pill(dl, V2(b.x - pad - pw, a.y + pad - 5 * sc), state, scol, 9, True)
            big = f"{p.width}"
            T.draw_text(dl, "light", 40, V2(a.x + pad - 2 * sc, a.y + 30 * sc), col32(*INK), big)
            bw = T.text_size("light", 40, big).x
            T.draw_text(dl, "ui", 12, V2(a.x + pad + bw + 6 * sc, a.y + 52 * sc), col32(*INK, 0.8), f"x {p.height}  PX")
            name = p.path.replace("\\", "/").split("/")[-1] if p.path else "untitled"
            name = (name[:22] + "..") if len(name) > 24 else name
            T.draw_text(dl, "mono", 8.5, V2(b.x - pad - T.tracked_width("mono", 8.5, name.upper(), 0.8), a.y + 58 * sc),
                        col32(*DIM), name.upper() + (" *" if app.dirty else ""), 0.8)
            # ruler strip
            y = b.y - 30 * sc
            dl.add_line(V2(a.x + pad, y), V2(b.x - pad, y), col32(1, 1, 1, 0.08), 1)
            n = 40
            for i in range(n + 1):
                x = a.x + pad + (b.x - a.x - 2 * pad) * i / n
                hgt = 5 if i % 10 == 0 else 2.5
                dl.add_line(V2(x, y), V2(x, y + hgt * sc), col32(1, 1, 1, 0.22 if i % 10 == 0 else 0.1), 1)
            srcname = {"spout": "SPOUT", "image": "IMAGE", "test": "TEST PATTERN"}[p.source_kind]
            if p.source_kind == "spout" and p.spout_name:
                srcname += " / " + p.spout_name.upper()[:14]
            T.draw_text(dl, "mono", 8.5, V2(a.x + pad, b.y - 19 * sc), col32(*DIM), "SRC  " + srcname, 1.0)
            sw, sh = src.size if src else (0, 0)
            r = f"{sw}x{sh}"
            T.draw_text(dl, "mono", 8.5, V2(b.x - pad - T.tracked_width("mono", 8.5, r, 1.0), b.y - 19 * sc), col32(*DIM), r, 1.0)

        self.card(128, tele)
        imgui.dummy(V2(0, 2 * sc))
        hit = self.button_row(["New", "Open", "Save", "Save as"])
        if hit == 0:
            self.new_project()
        elif hit == 1:
            self.open_project()
        elif hit == 2:
            self.save_project()
        elif hit == 3:
            self.save_project(True)

        # ---- input
        self.section("01", "Input")
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
            imgui.set_next_item_width(-64 * sc)
            ch, p.spout_name = imgui.input_text_with_hint("##spname", "active sender", p.spout_name)
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
            imgui.set_next_item_width(-78 * sc)
            imgui.input_text("##img", p.image_path, imgui.InputTextFlags_.read_only)
            imgui.same_line()
            if self.button("Browse", -1):
                path = file_dialog(False, "image")
                if path:
                    p.image_path = path
                    app.rebuild_source()
                    app.dirty = True
        self.label("Flip V")
        ch, p.flip_input = imgui.checkbox("##flip", p.flip_input)
        if ch:
            app.dirty = True

        self.label("Atlas")
        ch, v = imgui.slider_int2("##atlas", [p.input_cols, p.input_rows], 1, 6, "%d")
        if imgui.is_item_activated():
            self.push()
        if ch:
            p.input_cols, p.input_rows = max(1, v[0]), max(1, v[1])
        if imgui.is_item_hovered():
            imgui.set_tooltip("Camera atlas layout (columns x rows). Match the Blender add-on.")

        # ---- outputs
        self.section("02", "Outputs")
        self.output_list()
        hit = self.button_row(["+ Output", "Del", "Match", "Open" if not app.is_open() else "Close"],
                              ["ghost", "ghost", "ghost", "primary" if not app.is_open() else "ghost"])
        if hit == 0:
            self.add_output()
        elif hit == 1:
            self.delete_output()
        elif hit == 2:
            o = p.output
            if o.mode == "fullscreen":
                r = app.monitor_resolution(o.monitor)
                if r:
                    o.width, o.height = r
            app.dirty = True
        elif hit == 3:
            app.toggle_output()
        self.output_props(p.output)
        self.label("Guides")
        _, self.projector_guides = imgui.checkbox("##guides", self.projector_guides)
        imgui.same_line(0, 22 * sc)
        with T.use("mono", 9.5):
            imgui.text_disabled("CALIB")
        imgui.same_line()
        _, self.calib = imgui.checkbox("##calib", self.calib)

        # ---- layers
        self.section("03", "Layers")
        self.layer_list()
        hit = self.button_row(["+ Surface", "+ Mask", "Dup", "Del"])
        if hit == 0:
            self.add_surface()
        elif hit == 1:
            self.add_mask()
        elif hit == 2:
            self.duplicate()
        elif hit == 3:
            self.delete_selected()

        # ---- properties
        it = self.selected()
        if it is not None and it.output == p.active_output:
            self.section("04", "Surface" if isinstance(it, Surface) else "Mask")
            if isinstance(it, Surface):
                self.surface_props(it)
            else:
                self.mask_props(it)
        imgui.dummy(V2(0, 10 * sc))

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

    def output_list(self):
        sc = self.sc
        p = self.project
        app = self.app
        row_h = 32 * sc
        n = max(1, min(len(p.outputs), 4))
        imgui.push_style_var(imgui.StyleVar_.item_spacing, V2(0, 2 * sc))
        imgui.begin_child("outputs", V2(0, n * (row_h + 2 * sc) + 2 * sc))
        dl = imgui.get_window_draw_list()
        width = imgui.get_content_region_avail().x
        for i, o in enumerate(p.outputs):
            imgui.push_id(f"out{i}")
            a = imgui.get_cursor_screen_pos()
            b = V2(a.x + width, a.y + row_h)
            sel = p.active_output == i
            if imgui.invisible_button("row", V2(width - 30 * sc, row_h)):
                self.set_active_output(i)
            hov = imgui.is_item_hovered()
            imgui.same_line(0, 0)
            if imgui.invisible_button("live", V2(30 * sc, row_h)):
                app.toggle_output(i)
            live_hov = imgui.is_item_hovered()
            if live_hov:
                imgui.set_tooltip("Close output" if app.is_open(i) else "Open output")
            if sel:
                dl.add_rect_filled(a, b, col32(1, 1, 1, 0.05), 6 * sc)
                dl.add_rect(a, b, col32(1, 1, 1, 0.10), 6 * sc, 1)
            elif hov:
                dl.add_rect_filled(a, b, col32(1, 1, 1, 0.025), 6 * sc)
            cy = a.y + row_h / 2
            T.draw_text(dl, "mono", 9.5, V2(a.x + 10 * sc, cy - 6.5 * sc), col32(*(ACCENT if sel else DIM)), f"O{i + 1}")
            x = a.x + 36 * sc
            # little screen glyph
            dl.add_rect(V2(x, cy - 4.5 * sc), V2(x + 11 * sc, cy + 3 * sc), col32(*INK, 0.8), 1, 1.1)
            dl.add_line(V2(x + 3 * sc, cy + 5.5 * sc), V2(x + 8 * sc, cy + 5.5 * sc), col32(*INK, 0.8), 1.1)
            T.draw_text(dl, "ui", 13, V2(x + 19 * sc, cy - 8.5 * sc), col32(*INK), o.name)
            info = ("FULL " if o.mode == "fullscreen" else "WIN ") + f"{o.width}x{o.height}"
            iw = T.tracked_width("mono", 9, info, 0.6)
            T.draw_text(dl, "mono", 9, V2(b.x - 34 * sc - iw, cy - 6 * sc), col32(*DIM), info, 0.6)
            ex = b.x - 15 * sc
            if app.is_open(i):
                dl.add_circle_filled(V2(ex, cy), 6 * sc, col32(*LIVE, 0.18))
                dl.add_circle_filled(V2(ex, cy), 3 * sc, col32(*LIVE))
            else:
                dl.add_circle(V2(ex, cy), 4 * sc, col32(*(INK if live_hov else DIM)), 0, 1.1)
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
            self.int_pair("##cw", "##ch", o, "width", "height", 64, 8192)
        else:
            self.label("Position")
            if self.int_pair("##ox", "##oy", o, "x", "y", -10000, 10000):
                app.place_output(idx)
            self.label("Size")
            if self.int_pair("##ow", "##oh", o, "width", "height", 64, 8192):
                app.place_output(idx)
            self.label("Window")
            for key, text in (("borderless", "NO FRAME"), ("lock", "LOCK"), ("on_top", "ON TOP")):
                ch, v = imgui.checkbox(f"##{key}", getattr(o, key))
                if ch:
                    setattr(o, key, v)
                    app.place_output(idx)
                    app.dirty = True
                imgui.same_line(0, 4 * sc)
                with T.use("mono", 9):
                    imgui.text_disabled(text)
                imgui.same_line(0, 10 * sc)
            imgui.new_line()

    def int_pair(self, id_a, id_b, obj, attr_a, attr_b, lo, hi):
        sc = self.sc
        avail = imgui.get_content_region_avail().x
        sp = imgui.get_style().item_spacing.x
        fw = (avail - 2 * sp - 10 * sc) / 2
        imgui.set_next_item_width(fw)
        ch1, a = imgui.input_int(id_a, int(getattr(obj, attr_a)), 0)
        imgui.same_line()
        with T.use("mono", 10):
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

    def layer_list(self):
        sc = self.sc
        p = self.project
        ao = p.active_output
        rows = ([("surface", i, s) for i, s in enumerate(p.surfaces) if s.output == ao] +
                [("mask", i, m) for i, m in enumerate(p.masks) if m.output == ao])
        row_h = 32 * sc
        n = max(1, min(len(rows), 6))
        imgui.push_style_var(imgui.StyleVar_.item_spacing, V2(0, 2 * sc))
        imgui.begin_child("layers", V2(0, n * (row_h + 2 * sc) + 2 * sc))
        dl = imgui.get_window_draw_list()
        width = imgui.get_content_region_avail().x
        for k, (kind, i, it) in enumerate(rows):
            imgui.push_id(f"{kind}{i}")
            a = imgui.get_cursor_screen_pos()
            b = V2(a.x + width, a.y + row_h)
            sel = self.sel_kind == kind and self.sel_index == i
            clicked = imgui.invisible_button("row", V2(width - 30 * sc, row_h))
            hov = imgui.is_item_hovered()
            imgui.same_line(0, 0)
            vis_clicked = imgui.invisible_button("vis", V2(30 * sc, row_h))
            vis_hov = imgui.is_item_hovered()
            if clicked:
                self.select(kind, i)
            if vis_clicked:
                it.visible = not it.visible
                it.touch()
            if sel:
                dl.add_rect_filled(a, b, col32(1, 1, 1, 0.05), 6 * sc)
                dl.add_rect(a, b, col32(1, 1, 1, 0.10), 6 * sc, 1)
            elif hov:
                dl.add_rect_filled(a, b, col32(1, 1, 1, 0.025), 6 * sc)
            color = it.color if isinstance(it, Surface) else MASK_COL
            alpha = 1.0 if it.visible else 0.35
            cy = a.y + row_h / 2
            T.draw_text(dl, "mono", 9.5, V2(a.x + 10 * sc, cy - 6.5 * sc), col32(*(ACCENT if sel else DIM), alpha),
                        f"{k + 1:02d}")
            x = a.x + 36 * sc
            if kind == "mask":
                dl.add_rect(V2(x, cy - 4 * sc), V2(x + 8 * sc, cy + 4 * sc), col32(*color, alpha), 1, 1.2)
            else:
                dl.add_rect_filled(V2(x, cy - 4 * sc), V2(x + 8 * sc, cy + 4 * sc), col32(*color, alpha), 1)
            T.draw_text(dl, "ui", 13, V2(x + 16 * sc, cy - 8.5 * sc), col32(*INK, alpha), it.name)
            info = f"{it.cols}x{it.rows}" if isinstance(it, Surface) else f"{len(it.points)} PT"
            iw = T.tracked_width("mono", 9, info, 0.6)
            T.draw_text(dl, "mono", 9, V2(b.x - 34 * sc - iw, cy - 6 * sc), col32(*DIM, alpha), info, 0.6)
            # visibility eye: ring + pupil
            ex = b.x - 15 * sc
            ec = col32(*(INK if vis_hov else DIM), 1.0 if it.visible else 0.5)
            dl.add_circle(V2(ex, cy), 5 * sc, ec, 0, 1.1)
            if it.visible:
                dl.add_circle_filled(V2(ex, cy), 2 * sc, ec)
            else:
                dl.add_line(V2(ex - 6 * sc, cy + 6 * sc), V2(ex + 6 * sc, cy - 6 * sc), ec, 1.1)
            imgui.pop_id()
        imgui.end_child()
        imgui.pop_style_var()

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
        with T.use("mono", 9.5):
            imgui.text_disabled("DBL-CLICK EDGE   ADD POINT")
            imgui.text_disabled("RIGHT-CLICK PT   REMOVE")

    # ================================================================ view
    def draw_view(self):
        sc = self.sc
        if self.tab_request:
            self.tab = self.tab_request
            self.tab_request = None
        dl = imgui.get_window_draw_list()
        wp = imgui.get_window_pos()
        ww = imgui.get_window_width()
        bar_h = 52 * sc

        # ---- top bar
        dl.add_line(V2(wp.x, wp.y + bar_h), V2(wp.x + ww, wp.y + bar_h), col32(1, 1, 1, 0.06), 1)
        seg_w = self.segmented(V2(wp.x + 18 * sc, wp.y + 12 * sc))
        p = self.project
        o = p.output
        lab = f"O{p.active_output + 1}  {o.name.upper()}"
        x0 = wp.x + 18 * sc + seg_w + 18 * sc
        T.draw_text(dl, "mono_medium", 9.5, V2(x0, wp.y + 21 * sc), col32(*INK), lab, 1.2)
        x0 += T.tracked_width("mono_medium", 9.5, lab, 1.2) + 12 * sc
        live = self.app.is_open()
        T.draw_text(dl, "mono", 9.5, V2(x0, wp.y + 21 * sc), col32(*(LIVE if live else DIM)),
                    ("LIVE  " if live else "OFF  ") + f"{o.width}x{o.height}", 1.2)
        canvas = self.out_canvas if self.tab == "output" else self.in_canvas
        x = wp.x + ww - 18 * sc
        x = self.top_toggle("guides", "GUIDES", self.projector_guides, x, wp.y + 12 * sc)
        if x[1]:
            self.projector_guides = not self.projector_guides
        x = self.top_toggle("calib", "CALIB", self.calib, x[0] - 8 * sc, wp.y + 12 * sc)
        if x[1]:
            self.calib = not self.calib
        x = self.top_toggle("fit", "FIT", False, x[0] - 8 * sc, wp.y + 12 * sc)
        if x[1]:
            canvas.reset()
        z = f"ZOOM {canvas.zoom * 100:03.0f}%"
        zw = T.tracked_width("mono", 9.5, z, 1.2)
        T.draw_text(dl, "mono", 9.5, V2(x[0] - 16 * sc - zw, wp.y + 21 * sc), col32(*DIM), z, 1.2)

        # ---- canvas
        pos = V2(wp.x, wp.y + bar_h + 1)
        size = V2(ww, imgui.get_window_height() - bar_h - 1)
        dl.push_clip_rect(pos, V2(pos.x + size.x, pos.y + size.y), True)
        self.dot_grid(dl, pos, size)
        if self.tab == "output":
            self.output_view(dl, pos, size)
        else:
            self.input_view(dl, pos, size)
        dl.pop_clip_rect()

    def segmented(self, p):
        sc = self.sc
        dl = imgui.get_window_draw_list()
        h = 28 * sc
        items = [("output", "OUTPUT"), ("input", "INPUT")]
        widths = [T.tracked_width("mono_medium", 10, l, 1.6) + 32 * sc for _, l in items]
        total = sum(widths) + 6 * sc
        dl.add_rect(p, V2(p.x + total, p.y + h), col32(1, 1, 1, 0.14), h / 2, 1)
        x = p.x + 3 * sc
        for (key, lab), w in zip(items, widths):
            imgui.set_cursor_screen_pos(V2(x, p.y + 3 * sc))
            if imgui.invisible_button("seg_" + key, V2(w, h - 6 * sc)):
                self.tab = key
                self.drag = None
            on = self.tab == key
            a, b = V2(x, p.y + 3 * sc), V2(x + w, p.y + h - 3 * sc)
            if on:
                dl.add_rect_filled(a, b, col32(*INK), (h - 6 * sc) / 2)
            elif imgui.is_item_hovered():
                dl.add_rect_filled(a, b, col32(1, 1, 1, 0.06), (h - 6 * sc) / 2)
            T.draw_text(dl, "mono_medium", 10, V2(x + 16 * sc, p.y + 8 * sc),
                        col32(0.03, 0.03, 0.03) if on else col32(*DIM), lab, 1.6)
            x += w
        return total

    def top_toggle(self, sid, text, on, right_x, y):
        sc = self.sc
        dl = imgui.get_window_draw_list()
        h = 28 * sc
        w = T.tracked_width("mono_medium", 9.5, text, 1.4) + 26 * sc
        a = V2(right_x - w, y)
        b = V2(right_x, y + h)
        imgui.set_cursor_screen_pos(a)
        clicked = imgui.invisible_button("tt_" + sid, V2(w, h))
        hov = imgui.is_item_hovered()
        if on:
            dl.add_rect_filled(a, b, col32(*ACCENT, 0.12), h / 2)
            dl.add_rect(a, b, col32(*ACCENT, 0.8), h / 2, 1)
        else:
            dl.add_rect(a, b, col32(1, 1, 1, 0.2 if hov else 0.12), h / 2, 1)
        T.draw_text(dl, "mono_medium", 9.5, V2(a.x + 13 * sc, a.y + 8 * sc),
                    col32(*(ACCENT if on else (INK if hov else DIM))), text, 1.4)
        return a.x, clicked

    def dot_grid(self, dl, pos, size):
        sc = self.sc
        step = 26 * sc
        c = col32(1, 1, 1, 0.07)
        y = pos.y + step / 2
        while y < pos.y + size.y:
            x = pos.x + step / 2
            while x < pos.x + size.x:
                dl.add_rect_filled(V2(x, y), V2(x + 1.2, y + 1.2), c)
                x += step
            y += step

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

    def _frame(self, dl, canvas, tex, uv_flip):
        sc = self.sc
        a = canvas.v((0, 0))
        b = canvas.v((1, 1))
        dl.add_rect_filled(a, b, col32(0, 0, 0))
        if tex is not None:
            uv0, uv1 = (V2(0, 1), V2(1, 0)) if uv_flip else (V2(0, 0), V2(1, 1))
            dl.add_image(imgui.ImTextureRef(tex.glo), a, b, uv0, uv1)
        dl.add_rect(V2(a.x - 0.5, a.y - 0.5), V2(b.x + 0.5, b.y + 0.5), col32(1, 1, 1, 0.14), 0, 1)
        # registration brackets
        g, L = 9 * sc, 16 * sc
        c = col32(*INK, 0.75)
        for (cx, cy, dx, dy) in ((a.x - g, a.y - g, 1, 1), (b.x + g, a.y - g, -1, 1),
                                 (b.x + g, b.y + g, -1, -1), (a.x - g, b.y + g, 1, -1)):
            dl.add_line(V2(cx, cy), V2(cx + dx * L, cy), c, 1)
            dl.add_line(V2(cx, cy), V2(cx, cy + dy * L), c, 1)

    def rulers(self, dl, pos, size, cv, w_px, h_px, mouse, hovered):
        sc = self.sc
        t = 22 * sc
        tl = 40 * sc
        strip = col32(0.045, 0.047, 0.05, 0.96)
        dl.add_rect_filled(pos, V2(pos.x + size.x, pos.y + t), strip)
        dl.add_rect_filled(V2(pos.x, pos.y + t), V2(pos.x + tl, pos.y + size.y), strip)
        dl.add_line(V2(pos.x, pos.y + t), V2(pos.x + size.x, pos.y + t), col32(1, 1, 1, 0.08), 1)
        dl.add_line(V2(pos.x + tl, pos.y), V2(pos.x + tl, pos.y + size.y), col32(1, 1, 1, 0.08), 1)
        ppx = cv.extent[0] / w_px  # screen px per output px
        step = 10
        for s_ in (10, 20, 50, 100, 200, 250, 500, 1000):
            step = s_
            if s_ * ppx >= 9 * sc:
                break
        major = step * 5 if step not in (250,) else 1000
        tick = col32(1, 1, 1, 0.16)
        tick_m = col32(1, 1, 1, 0.38)
        lab = col32(*DIM)
        # horizontal
        v = 0
        while v <= w_px:
            x = cv.origin[0] + v * ppx
            if pos.x + tl <= x <= pos.x + size.x:
                m = v % major == 0
                dl.add_line(V2(x, pos.y + t - (7 if m else 3.5) * sc), V2(x, pos.y + t), tick_m if m else tick, 1)
                if m:
                    T.draw_text(dl, "mono", 7.5, V2(x + 3 * sc, pos.y + 3 * sc), lab, f"{v:04d}")
            v += step
        # vertical
        ppy = cv.extent[1] / h_px
        v = 0
        while v <= h_px:
            y = cv.origin[1] + v * ppy
            if pos.y + t <= y <= pos.y + size.y:
                m = v % major == 0
                dl.add_line(V2(pos.x + tl - (7 if m else 3.5) * sc, y), V2(pos.x + tl, y), tick_m if m else tick, 1)
                if m:
                    T.draw_text(dl, "mono", 7.5, V2(pos.x + 6 * sc, y + 2 * sc), lab, f"{v:04d}")
            v += step
        if hovered:
            mx, my = mouse
            dl.add_triangle_filled(V2(mx - 4 * sc, pos.y + t - 6 * sc), V2(mx + 4 * sc, pos.y + t - 6 * sc),
                                   V2(mx, pos.y + t), col32(*ACCENT))
            dl.add_triangle_filled(V2(pos.x + tl - 6 * sc, my - 4 * sc), V2(pos.x + tl - 6 * sc, my + 4 * sc),
                                   V2(pos.x + tl, my), col32(*ACCENT))
        dl.add_rect_filled(pos, V2(pos.x + tl, pos.y + t), strip)
        T.draw_text(dl, "mono", 7.5, V2(pos.x + 6 * sc, pos.y + 5 * sc), col32(*DIM), "PX")

    def cursor_readout(self, dl, pos, size, cv, w_px, h_px, mouse):
        sc = self.sc
        n = cv.to_norm(mouse)
        if not (0 <= n[0] <= 1 and 0 <= n[1] <= 1) or self.drag:
            return
        mx, my = mouse
        c = col32(1, 1, 1, 0.07)
        dl.add_line(V2(pos.x, my), V2(pos.x + size.x, my), c, 1)
        dl.add_line(V2(mx, pos.y), V2(mx, pos.y + size.y), c, 1)
        txt = f"X {n[0] * w_px:04.0f}  Y {n[1] * h_px:04.0f}"
        tw = T.tracked_width("mono", 9, txt, 0.8)
        a = V2(mx + 14 * sc, my + 14 * sc)
        dl.add_rect_filled(a, V2(a.x + tw + 14 * sc, a.y + 20 * sc), col32(0.02, 0.02, 0.022, 0.85), 3 * sc)
        dl.add_rect(a, V2(a.x + tw + 14 * sc, a.y + 20 * sc), col32(1, 1, 1, 0.12), 3 * sc, 1)
        T.draw_text(dl, "mono", 9, V2(a.x + 7 * sc, a.y + 4 * sc), col32(*INK), txt, 0.8)

    def callout(self, dl, anchor, num, name, info, color, strong):
        sc = self.sc
        a = 1.0 if strong else 0.55
        ax, ay = anchor.x, anchor.y
        kx, ky = ax + 22 * sc, ay - 22 * sc
        dl.add_circle_filled(anchor, 2.5 * sc, col32(*color, a))
        dl.add_line(anchor, V2(kx, ky), col32(*color, a * 0.8), 1)
        dl.add_line(V2(kx, ky), V2(kx + 12 * sc, ky), col32(*color, a * 0.8), 1)
        bx = kx + 12 * sc
        nw = T.text_size("mono_medium", 9, num).x
        tw = T.text_size("ui", 12, name).x
        iw = T.text_size("mono", 8.5, info).x
        w = nw + tw + iw + 34 * sc
        h = 22 * sc
        r0, r1 = V2(bx, ky - h / 2), V2(bx + w, ky + h / 2)
        dl.add_rect_filled(r0, r1, col32(0.03, 0.032, 0.035, 0.88 if strong else 0.7), 4 * sc)
        dl.add_rect(r0, r1, col32(*color, a * 0.6), 4 * sc, 1)
        x = bx + 8 * sc
        T.draw_text(dl, "mono_medium", 9, V2(x, ky - 6.5 * sc), col32(*color, a), num)
        x += nw + 8 * sc
        T.draw_text(dl, "ui", 12, V2(x, ky - 8 * sc), col32(*INK, a), name)
        x += tw + 10 * sc
        T.draw_text(dl, "mono", 8.5, V2(x, ky - 6 * sc), col32(*DIM, a), info)

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

    def handle(self, dl, c, color, hot, corner, label=None):
        sc = self.sc
        r = (4.5 if corner else 3.5) * sc
        if hot:
            L = 16 * sc
            dl.add_line(V2(c.x - L, c.y), V2(c.x - r - 3 * sc, c.y), col32(*ACCENT, 0.9), 1)
            dl.add_line(V2(c.x + r + 3 * sc, c.y), V2(c.x + L, c.y), col32(*ACCENT, 0.9), 1)
            dl.add_line(V2(c.x, c.y - L), V2(c.x, c.y - r - 3 * sc), col32(*ACCENT, 0.9), 1)
            dl.add_line(V2(c.x, c.y + r + 3 * sc), V2(c.x, c.y + L), col32(*ACCENT, 0.9), 1)
            dl.add_rect_filled(V2(c.x - r, c.y - r), V2(c.x + r, c.y + r), col32(*ACCENT))
            if label:
                T.draw_text(dl, "mono", 8.5, V2(c.x + 10 * sc, c.y - 20 * sc), col32(*ACCENT), label, 0.6)
        else:
            dl.add_rect_filled(V2(c.x - r, c.y - r), V2(c.x + r, c.y + r), col32(0.03, 0.03, 0.035, 0.95))
            dl.add_rect(V2(c.x - r, c.y - r), V2(c.x + r, c.y + r), col32(*color), 0, 1.4 * sc)

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

    def loupe(self, dl, cv, mouse, tex, uv_flip):
        if tex is None:
            return
        sc = self.sc
        size = 132 * sc
        n = cv.to_norm(mouse)
        rad = 0.025 / max(cv.zoom, 1)
        ar = cv.extent[1] / cv.extent[0]
        u0, u1 = n[0] - rad * ar, n[0] + rad * ar
        v0, v1 = n[1] - rad, n[1] + rad
        if uv_flip:
            v0, v1 = 1 - v0, 1 - v1
        a = V2(mouse[0] + 28 * sc, mouse[1] - size - 28 * sc)
        b = V2(a.x + size, a.y + size)
        dl.add_rect_filled(V2(a.x - 1, a.y - 1), V2(b.x + 1, b.y + 22 * sc), col32(0.02, 0.02, 0.022, 0.95), 4 * sc)
        dl.add_image(imgui.ImTextureRef(tex.glo), a, b, V2(u0, v0), V2(u1, v1))
        cx, cy = (a.x + b.x) / 2, (a.y + b.y) / 2
        ac = col32(*ACCENT)
        dl.add_line(V2(cx - 10 * sc, cy), V2(cx - 3 * sc, cy), ac, 1)
        dl.add_line(V2(cx + 3 * sc, cy), V2(cx + 10 * sc, cy), ac, 1)
        dl.add_line(V2(cx, cy - 10 * sc), V2(cx, cy - 3 * sc), ac, 1)
        dl.add_line(V2(cx, cy + 3 * sc), V2(cx, cy + 10 * sc), ac, 1)
        L = 12 * sc
        for (x, y, dx, dy) in ((a.x, a.y, 1, 1), (b.x, a.y, -1, 1), (b.x, b.y, -1, -1), (a.x, b.y, 1, -1)):
            dl.add_line(V2(x, y), V2(x + dx * L, y), ac, 1.4)
            dl.add_line(V2(x, y), V2(x, y + dy * L), ac, 1.4)
        T.draw_text(dl, "mono", 8.5, V2(a.x + 6 * sc, b.y + 5 * sc), col32(*DIM), "LOUPE", 1.2)
        T.draw_text(dl, "mono", 8.5, V2(b.x - 30 * sc, b.y + 5 * sc), col32(*ACCENT), f"x{1 / (2 * rad):.0f}")

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
            gc = col32(*ACCENT, 0.5)
            for c in range(1, cols):
                dl.add_line(cv.v((c / cols, 0)), cv.v((c / cols, 1)), gc, 1)
            for r in range(1, rows):
                dl.add_line(cv.v((0, r / rows)), cv.v((1, r / rows)), gc, 1)
            for k in range(cols * rows):
                a = cv.v(((k % cols) / cols, (k // cols) / rows))
                tag = f"CAM {k + 1:02d}"
                tw = T.tracked_width("mono_medium", 9, tag, 1.0)
                dl.add_rect_filled(V2(a.x, a.y), V2(a.x + tw + 14 * sc, a.y + 20 * sc), col32(*ACCENT, 0.9))
                T.draw_text(dl, "mono_medium", 9, V2(a.x + 7 * sc, a.y + 4 * sc), col32(0.03, 0.03, 0.03), tag, 1.0)
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
    def draw_status(self):
        sc = self.sc
        io = imgui.get_io()
        dl = imgui.get_window_draw_list()
        wp = imgui.get_window_pos()
        ww = imgui.get_window_width()
        hh = imgui.get_window_height()
        dl.add_line(wp, V2(wp.x + ww, wp.y), col32(1, 1, 1, 0.06), 1)
        y = wp.y + (hh - 12 * sc) / 2
        x = wp.x + 18 * sc
        state, scol = self.source_state()

        def seg(text, color, dot=None):
            nonlocal x
            if dot:
                dl.add_circle_filled(V2(x + 3 * sc, y + 6 * sc), 3 * sc, col32(*dot))
                x += 12 * sc
            T.draw_text(dl, "mono", 9, V2(x, y), col32(*color), text, 1.0)
            x += T.tracked_width("mono", 9, text, 1.0) + 14 * sc
            dl.add_line(V2(x, y), V2(x, y + 12 * sc), col32(1, 1, 1, 0.1), 1)
            x += 14 * sc

        seg(state, scol, scol)
        seg(f"FPS {io.framerate:03.0f}", DIM)
        seg(f"OUT {self.project.width}x{self.project.height}", DIM)
        n_open = self.app.open_count()
        seg(f"OUTPUTS {n_open}/{len(self.project.outputs)} LIVE", LIVE if n_open else DIM)
        it = self.selected()
        if it is not None:
            pt = ""
            if isinstance(self.sel_point, tuple):
                pt = f"  PT {self.sel_point[1]},{self.sel_point[0]}"
            elif isinstance(self.sel_point, int):
                pt = f"  PT {self.sel_point}"
            seg(("SEL " + it.name).upper() + pt, INK)
        if self.toast and imgui.get_time() - self.toast_time < 4:
            msg = self.toast.upper()
            col = ACCENT
        else:
            msg = "DRAG MOVE  /  SHIFT FINE  /  ALT CORNER  /  ARROWS NUDGE  /  TAB NEXT  /  WHEEL ZOOM  /  SPACE PAN"
            col = FAINT
        mw = T.tracked_width("mono", 8.5, msg, 0.8)
        if wp.x + ww - 18 * sc - mw > x:
            T.draw_text(dl, "mono", 8.5, V2(wp.x + ww - 18 * sc - mw, y + 1 * sc), col32(*col), msg, 0.8)

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
