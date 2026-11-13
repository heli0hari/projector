import numpy as np
from imgui_bundle import imgui

from . import geometry as geo
from .model import Surface, Mask, Project
from .theme import col32, ACCENT

V2 = imgui.ImVec2
HANDLE_R = 6.0
PICK_R = 11.0


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
        base = min(avail[0] / aspect, avail[1]) * 0.9
        self.extent = np.array([base * aspect, base]) * self.zoom
        centre = np.array([pos.x, pos.y]) + avail / 2 + self.pan
        self.origin = centre - self.extent / 2

    def to_screen(self, p):
        p = np.asarray(p)
        return self.origin + p * self.extent

    def to_norm(self, s):
        return (np.asarray(s) - self.origin) / self.extent

    def v(self, p):
        s = self.to_screen(p)
        return V2(float(s[0]), float(s[1]))

    def zoom_at(self, mouse, factor, pos, size):
        before = self.to_norm(mouse)
        self.zoom = float(np.clip(self.zoom * factor, 0.2, 12.0))
        self.layout(pos, size, self.extent[0] / self.extent[1])
        after = self.to_screen(before)
        self.pan += mouse - after

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
        self.out_canvas = Canvas()
        self.in_canvas = Canvas()
        self.drag = None
        self.calib = False
        self.projector_guides = True
        self.spout_list = []
        self.toast = ""
        self.toast_time = 0.0

    # ------------------------------------------------------------ helpers
    @property
    def project(self) -> Project:
        return self.app.project

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

    # ------------------------------------------------------------ actions
    def add_surface(self):
        self.push()
        p = self.project
        s = Surface(f"Surface {len(p.surfaces) + 1}", len(p.surfaces))
        off = 0.03 * (len(p.surfaces) % 5)
        s.points += off
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
            if self.sel_point is not None and isinstance(self.sel_point, int):
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
            i = -1 if not isinstance(self.sel_point, int) else self.sel_point
            self.sel_point = (i + step) % 4
            return
        if isinstance(it, Surface):
            r, c = it.points.shape[:2]
            flat = [(i, j) for i in range(r) for j in range(c)]
            cur = flat.index(self.sel_point) if self.sel_point in flat else -1
            self.sel_point = flat[(cur + step) % len(flat)]
        else:
            n = len(it.points)
            cur = self.sel_point if isinstance(self.sel_point, int) else -1
            self.sel_point = (cur + step) % n

    @staticmethod
    def _is_corner(s, rc):
        r, c = rc
        return r in (0, s.rows) and c in (0, s.cols)

    def _move_surface_point(self, s, rc, new, keep_mesh=True, base=None):
        pts = s.points if base is None else base
        if keep_mesh and self._is_corner(s, rc) and (s.rows > 1 or s.cols > 1):
            # move a corner, carrying the mesh along in perspective
            old_c = geo.corners_of_grid(pts)
            h_old = geo.homography(geo.UNIT_SQUARE, old_c)
            local = geo.apply_h(np.linalg.inv(h_old), pts)
            new_c = old_c.copy()
            idx = {(0, 0): 0, (0, s.cols): 1, (s.rows, s.cols): 2, (s.rows, 0): 3}[tuple(rc)]
            new_c[idx] = new
            s.points = geo.apply_h(geo.homography(geo.UNIT_SQUARE, new_c), local)
        else:
            if base is not None:
                s.points = base.copy()
            s.points[rc] = new
        s.touch()

    # ------------------------------------------------------------ project io
    def new_project(self):
        self.push()
        self.project.load_dict(Project().to_dict())
        self.project.path = None
        self.select("surface", 0)
        self.app.rebuild_source()

    def open_project(self):
        path = file_dialog(False)
        if path:
            try:
                self.push()
                self.project.load(path)
                self.select("surface", 0)
                self.app.rebuild_source()
                self.notify(f"Opened {path}")
            except Exception as e:
                self.notify(f"Open failed: {e}")

    def save_project(self, save_as=False):
        path = self.project.path
        if save_as or not path:
            path = file_dialog(True)
        if path:
            try:
                self.project.save(path)
                self.app.dirty = False
                self.notify(f"Saved {path}")
            except Exception as e:
                self.notify(f"Save failed: {e}")

    # ------------------------------------------------------------ frame
    def draw(self, fb_w, fb_h):
        io = imgui.get_io()
        sc = self.app.ui_scale
        side_w = 330 * sc
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
        imgui.push_style_var(imgui.StyleVar_.window_padding, V2(10 * sc, 10 * sc))
        imgui.begin("##view", None, flags | imgui.WindowFlags_.no_scrollbar | imgui.WindowFlags_.no_scroll_with_mouse)
        imgui.pop_style_var()
        self.draw_view()
        imgui.end()

        imgui.set_next_window_pos(V2(0, h - status_h))
        imgui.set_next_window_size(V2(w, status_h))
        imgui.push_style_var(imgui.StyleVar_.window_padding, V2(14 * sc, 6 * sc))
        imgui.push_style_color(imgui.Col_.window_bg, col32(0.055, 0.058, 0.07))
        imgui.begin("##status", None, flags | imgui.WindowFlags_.no_scrollbar)
        imgui.pop_style_color()
        imgui.pop_style_var()
        self.draw_status()
        imgui.end()

        self.shortcuts()

    # ------------------------------------------------------------ sidebar
    def heading(self, text):
        f = self.app.fonts.get("bold")
        imgui.dummy(V2(0, 2))
        if f:
            imgui.push_font(f, 0.0)
        imgui.text_colored(imgui.ImVec4(ACCENT[0], ACCENT[1], ACCENT[2], 1), text.upper())
        if f:
            imgui.pop_font()
        imgui.separator()

    def draw_sidebar(self):
        p = self.project
        app = self.app
        sc = app.ui_scale
        bold = app.fonts.get("bold")
        if bold:
            imgui.push_font(bold, 0.0)
        imgui.text("PROJ")
        imgui.same_line(0, 0)
        imgui.text_colored(imgui.ImVec4(*ACCENT, 1), "MAP")
        if bold:
            imgui.pop_font()
        imgui.same_line()
        name = p.path.replace("\\", "/").split("/")[-1] if p.path else "untitled"
        imgui.text_disabled(name + (" *" if app.dirty else ""))

        bw = (imgui.get_content_region_avail().x - 3 * imgui.get_style().item_spacing.x) / 4
        if imgui.button("New", V2(bw, 0)):
            self.new_project()
        imgui.same_line()
        if imgui.button("Open", V2(bw, 0)):
            self.open_project()
        imgui.same_line()
        if imgui.button("Save", V2(bw, 0)):
            self.save_project()
        imgui.same_line()
        if imgui.button("Save as", V2(bw, 0)):
            self.save_project(True)

        # ---- input
        self.heading("Input")
        kinds = ["test", "spout", "image"]
        labels = ["Test pattern", "Spout (Blender / AE)", "Image file"]
        cur = kinds.index(p.source_kind) if p.source_kind in kinds else 0
        imgui.set_next_item_width(-1)
        ch, cur = imgui.combo("##srckind", cur, labels)
        if ch:
            p.source_kind = kinds[cur]
            app.rebuild_source()
            app.dirty = True
        if p.source_kind == "spout":
            imgui.set_next_item_width(-60 * sc)
            ch, p.spout_name = imgui.input_text_with_hint("##spname", "sender name (empty = active)", p.spout_name)
            if imgui.is_item_deactivated_after_edit():
                app.rebuild_source()
                app.dirty = True
            imgui.same_line()
            if imgui.button("List", V2(-1, 0)):
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
            imgui.set_next_item_width(-70 * sc)
            imgui.input_text("##img", p.image_path, imgui.InputTextFlags_.read_only)
            imgui.same_line()
            if imgui.button("Browse", V2(-1, 0)):
                path = file_dialog(False, "image")
                if path:
                    p.image_path = path
                    app.rebuild_source()
                    app.dirty = True
        ch, p.flip_input = imgui.checkbox("Flip input vertically", p.flip_input)
        if ch:
            app.dirty = True
        src = app.source
        ok = getattr(src, "connected", True)
        dot = (0.35, 0.85, 0.45) if ok else (0.95, 0.6, 0.2)
        dl = imgui.get_window_draw_list()
        cp = imgui.get_cursor_screen_pos()
        dl.add_circle_filled(V2(cp.x + 5 * sc, cp.y + imgui.get_text_line_height() / 2), 4 * sc, col32(*dot))
        imgui.dummy(V2(12 * sc, 0))
        imgui.same_line()
        imgui.text_disabled(src.status if src else "No source")

        # ---- output
        self.heading("Projector")
        mons = app.monitor_names()
        if mons:
            mi = min(p.output_monitor, len(mons) - 1)
            imgui.set_next_item_width(-1)
            ch, mi = imgui.combo("##mon", mi, mons)
            if ch:
                p.output_monitor = mi
                app.place_output()
                app.dirty = True
        label = "Close projector output  (F)" if app.output_open else "Open projector output  (F)"
        if app.output_open:
            imgui.push_style_color(imgui.Col_.button, col32(*ACCENT, 0.85))
        if imgui.button(label, V2(-1, 0)):
            app.toggle_output()
        if app.output_open:
            imgui.pop_style_color()
        imgui.set_next_item_width(90 * sc)
        ch1, w_ = imgui.input_int("##w", p.width, 0)
        imgui.same_line()
        imgui.text_disabled("x")
        imgui.same_line()
        imgui.set_next_item_width(90 * sc)
        ch2, h_ = imgui.input_int("##h", p.height, 0)
        if ch1 or ch2:
            p.width = int(np.clip(w_, 64, 8192))
            p.height = int(np.clip(h_, 64, 8192))
            app.dirty = True
        imgui.same_line()
        if imgui.button("Match", V2(-1, 0)):
            r = app.monitor_resolution(p.output_monitor)
            if r:
                p.width, p.height = r
        _, self.projector_guides = imgui.checkbox("Guides on projector  (G)", self.projector_guides)
        _, self.calib = imgui.checkbox("Calibration pattern  (C)", self.calib)

        # ---- surfaces
        self.heading("Surfaces")
        self.item_list("surf", p.surfaces, "surface")
        bw = (imgui.get_content_region_avail().x - 4 * imgui.get_style().item_spacing.x) / 5
        if imgui.button("+ Add", V2(bw * 1.4, 0)):
            self.add_surface()
        imgui.same_line()
        if imgui.button("Mask", V2(bw * 1.2, 0)):
            self.add_mask()
        imgui.same_line()
        if imgui.button("Dup", V2(bw * 0.8, 0)):
            self.duplicate()
        imgui.same_line()
        if imgui.button("^", V2(bw * 0.4, 0)):
            self.move_selected(-1)
        imgui.same_line()
        if imgui.button("v", V2(bw * 0.4, 0)):
            self.move_selected(1)
        imgui.same_line()
        if imgui.button("Del", V2(-1, 0)):
            self.delete_selected()
        if p.masks:
            self.heading("Masks")
            self.item_list("mask", p.masks, "mask")

        # ---- properties
        it = self.selected()
        if it is not None:
            self.heading("Selected")
            if isinstance(it, Surface):
                self.surface_props(it)
            else:
                self.mask_props(it)

    def item_list(self, sid, items, kind):
        sc = self.app.ui_scale
        row_h = imgui.get_frame_height()
        n = max(1, min(len(items), 6))
        imgui.begin_child(sid, V2(0, n * (row_h + 4 * sc) + 10 * sc))
        dl = imgui.get_window_draw_list()
        for i, it in enumerate(items):
            imgui.push_id(f"{sid}{i}")
            ch, it.visible = imgui.checkbox("##vis", it.visible)
            if ch:
                it.touch()
            imgui.same_line()
            cp = imgui.get_cursor_screen_pos()
            color = it.color if isinstance(it, Surface) else (0.95, 0.25, 0.25)
            dl.add_rect_filled(V2(cp.x, cp.y + 4 * sc), V2(cp.x + 4 * sc, cp.y + row_h - 4 * sc), col32(*color), 2)
            imgui.dummy(V2(6 * sc, row_h))
            imgui.same_line()
            sel = self.sel_kind == kind and self.sel_index == i
            info = f"{it.cols}x{it.rows}" if isinstance(it, Surface) else f"{len(it.points)} pts"
            if imgui.selectable(f"{it.name}##sel", sel, 0, V2(0, row_h))[0]:
                self.select(kind, i)
            imgui.same_line(imgui.get_window_width() - imgui.get_style().window_padding.x - imgui.calc_text_size(info).x - 4 * sc)
            imgui.text_disabled(info)
            imgui.pop_id()
        imgui.end_child()

    def slider(self, label, obj, attr, lo, hi, fmt="%.2f"):
        imgui.set_next_item_width(-90 * self.app.ui_scale)
        ch, v = imgui.slider_float(label, getattr(obj, attr), lo, hi, fmt)
        if imgui.is_item_activated():
            self.push()
        if ch:
            setattr(obj, attr, v)
            obj.touch()
        return ch

    def surface_props(self, s):
        sc = self.app.ui_scale
        imgui.set_next_item_width(-90 * sc)
        _, s.name = imgui.input_text("Name", s.name)
        self.slider("Opacity", s, "opacity", 0, 1)
        self.slider("Brightness", s, "brightness", 0, 2)
        self.slider("Edge feather", s, "feather", 0, 0.5)
        imgui.text_disabled("Warp grid (cols x rows)")
        imgui.set_next_item_width(-90 * sc)
        ch, v = imgui.slider_int2("Grid", [s.cols, s.rows], 1, 12)
        if imgui.is_item_activated():
            self.push()
        if ch:
            s.set_grid(v[1], v[0])
            self.sel_point = None
        imgui.set_next_item_width(-90 * sc)
        ch, idx = imgui.combo("Interp.", 1 if s.smooth else 0, ["Linear", "Smooth (spline)"])
        if ch:
            self.push()
            s.smooth = idx == 1
            s.touch()
        bw = (imgui.get_content_region_avail().x - 2 * imgui.get_style().item_spacing.x) / 3
        if imgui.button("Flatten mesh", V2(bw, 0)):
            self.push()
            s.reset_mesh()
        imgui.same_line()
        if imgui.button("Fill output", V2(bw, 0)):
            self.push()
            s.points = geo.lattice(s.rows, s.cols)
            s.touch()
        imgui.same_line()
        if imgui.button("Full input", V2(bw, 0)):
            self.push()
            s.source = geo.UNIT_SQUARE.copy()
            s.touch()
        if self.sel_point is not None and isinstance(self.sel_point, tuple) and self.tab == "output":
            pt = s.points[self.sel_point]
            imgui.text_disabled(f"Point {self.sel_point}:  {pt[0] * self.project.width:.1f}, {pt[1] * self.project.height:.1f} px")

    def mask_props(self, m):
        imgui.set_next_item_width(-90 * self.app.ui_scale)
        _, m.name = imgui.input_text("Name", m.name)
        imgui.text_disabled("Double-click an edge to add a point.")
        imgui.text_disabled("Right-click a point to remove it.")

    # ------------------------------------------------------------ view
    def draw_view(self):
        sc = self.app.ui_scale
        if imgui.begin_tab_bar("views"):
            req = getattr(self, "tab_request", None)
            self.tab_request = None
            for key, label in (("output", "  Output  "), ("input", "  Input  ")):
                fl = imgui.TabItemFlags_.set_selected if req == key else 0
                if imgui.begin_tab_item(label, None, fl)[0]:
                    self.tab = key
                    imgui.end_tab_item()
            imgui.end_tab_bar()
        imgui.same_line(imgui.get_window_width() - imgui.get_style().window_padding.x - 230 * sc)
        imgui.set_cursor_pos_y(imgui.get_cursor_pos_y() - imgui.get_frame_height() - imgui.get_style().item_spacing.y)
        canvas = self.out_canvas if self.tab == "output" else self.in_canvas
        imgui.text_disabled(f"{canvas.zoom * 100:.0f}%")
        imgui.same_line()
        if imgui.small_button("Fit  (Home)"):
            canvas.reset()
        imgui.same_line()
        _, self.calib = imgui.checkbox("Calib", self.calib)

        pos = imgui.get_cursor_screen_pos()
        size = imgui.get_content_region_avail()
        dl = imgui.get_window_draw_list()
        dl.add_rect_filled(pos, V2(pos.x + size.x, pos.y + size.y), col32(0.045, 0.048, 0.058), 8 * sc)
        dl.push_clip_rect(pos, V2(pos.x + size.x, pos.y + size.y), True)
        if self.tab == "output":
            self.output_view(dl, pos, size)
        else:
            self.input_view(dl, pos, size)
        dl.pop_clip_rect()

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
        a = canvas.v((0, 0))
        b = canvas.v((1, 1))
        dl.add_rect_filled(V2(a.x - 1, a.y - 1), V2(b.x + 1, b.y + 1), col32(1, 1, 1, 0.08))
        if tex is not None:
            if uv_flip:
                dl.add_image(imgui.ImTextureRef(tex.glo), a, b, V2(0, 1), V2(1, 0))
            else:
                dl.add_image(imgui.ImTextureRef(tex.glo), a, b, V2(0, 0), V2(1, 1))

    def output_view(self, dl, pos, size):
        p = self.project
        cv = self.out_canvas
        cv.layout(pos, size, p.width / p.height)
        hovered, mouse, panning = self._canvas_input(cv, pos, size)
        self._frame(dl, cv, self.app.renderer.comp_tex, True)
        sc = self.app.ui_scale
        io = imgui.get_io()

        # --- overlays
        for i, s in enumerate(p.surfaces):
            if not s.visible:
                continue
            sel = self.sel_kind == "surface" and self.sel_index == i
            b = geo.boundary(s.points, 20, s.smooth)
            pts = [cv.v(q) for q in b]
            dl.add_polyline(pts, col32(*s.color, 1.0 if sel else 0.6), (2.2 if sel else 1.3) * sc, imgui.ImDrawFlags_.closed)
            c = cv.v(b.mean(axis=0))
            tw = imgui.calc_text_size(s.name)
            dl.add_rect_filled(V2(c.x - tw.x / 2 - 6, c.y - tw.y / 2 - 3), V2(c.x + tw.x / 2 + 6, c.y + tw.y / 2 + 3), col32(0, 0, 0, 0.55), 4)
            dl.add_text(V2(c.x - tw.x / 2, c.y - tw.y / 2), col32(*s.color), s.name)
            if sel:
                g = s.points
                for r in range(g.shape[0]):
                    for cc in range(g.shape[1]):
                        if cc + 1 < g.shape[1]:
                            dl.add_line(cv.v(g[r, cc]), cv.v(g[r, cc + 1]), col32(1, 1, 1, 0.28), 1)
                        if r + 1 < g.shape[0]:
                            dl.add_line(cv.v(g[r, cc]), cv.v(g[r + 1, cc]), col32(1, 1, 1, 0.28), 1)
                for r in range(g.shape[0]):
                    for cc in range(g.shape[1]):
                        self._handle(dl, cv.v(g[r, cc]), s.color, self.sel_point == (r, cc), self._is_corner(s, (r, cc)))
        for i, m in enumerate(p.masks):
            if not m.visible:
                continue
            sel = self.sel_kind == "mask" and self.sel_index == i
            pts = [cv.v(q) for q in m.points]
            dl.add_polyline(pts, col32(1, 0.3, 0.3, 1.0 if sel else 0.55), (2.0 if sel else 1.2) * sc, imgui.ImDrawFlags_.closed)
            if sel:
                for k, q in enumerate(m.points):
                    self._handle(dl, cv.v(q), (1, 0.3, 0.3), self.sel_point == k, True)

        if panning:
            return

        # --- interaction
        it = self.selected()
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
            speed = 0.25 if io.key_shift else 1.0
            d = (mouse - start) / cv.extent * speed
            if it is not None:
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

        # magnifier hint for precise dragging
        if self.drag and self.drag[0] == "point":
            self._loupe(dl, cv, mouse, self.app.renderer.comp_tex, True)

    def _handle(self, dl, c, color, hot, corner):
        sc = self.app.ui_scale
        r = HANDLE_R * sc * (1.0 if corner else 0.75)
        if hot:
            dl.add_circle_filled(c, r + 4 * sc, col32(1, 1, 1, 0.18))
            dl.add_circle_filled(c, r, col32(1, 1, 1))
            dl.add_circle(c, r + 1, col32(*color), 0, 2 * sc)
        else:
            dl.add_circle_filled(c, r, col32(0.05, 0.05, 0.06, 0.9))
            dl.add_circle(c, r, col32(*color), 0, 2 * sc)

    def _loupe(self, dl, cv, mouse, tex, uv_flip):
        if tex is None:
            return
        sc = self.app.ui_scale
        size = 120 * sc
        n = cv.to_norm(mouse)
        rad = 0.025 / max(cv.zoom, 1)
        u0, u1 = n[0] - rad * cv.extent[1] / cv.extent[0], n[0] + rad * cv.extent[1] / cv.extent[0]
        v0, v1 = n[1] - rad, n[1] + rad
        if uv_flip:
            v0, v1 = 1 - v0, 1 - v1
        a = V2(mouse[0] + 24 * sc, mouse[1] - size - 24 * sc)
        b = V2(a.x + size, a.y + size)
        dl.add_rect_filled(V2(a.x - 2, a.y - 2), V2(b.x + 2, b.y + 2), col32(0, 0, 0, 0.8), 6)
        dl.add_image(imgui.ImTextureRef(tex.glo), a, b, V2(u0, v0), V2(u1, v1))
        cx, cy = (a.x + b.x) / 2, (a.y + b.y) / 2
        dl.add_line(V2(cx - 8, cy), V2(cx + 8, cy), col32(*ACCENT), 1.5)
        dl.add_line(V2(cx, cy - 8), V2(cx, cy + 8), col32(*ACCENT), 1.5)
        dl.add_rect(a, b, col32(*ACCENT, 0.9), 4, 1.5)

    def _snapshot(self):
        it = self.selected()
        if it is None:
            return None
        if self.tab == "input" and isinstance(it, Surface):
            return it.source.copy()
        return it.points.copy()

    def _pick_point(self, cv, mouse):
        it = self.selected()
        if it is None:
            return None
        best, best_d = None, PICK_R * self.app.ui_scale
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
        for i in range(len(p.masks) - 1, -1, -1):
            if p.masks[i].visible and geo.point_in_poly(n, p.masks[i].points):
                return "mask", i
        # prefer the current selection when surfaces overlap
        if self.sel_kind == "surface" and 0 <= self.sel_index < len(p.surfaces):
            s = p.surfaces[self.sel_index]
            if s.visible and geo.point_in_poly(n, geo.boundary(s.points, 12, s.smooth)):
                return "surface", self.sel_index
        for i in range(len(p.surfaces) - 1, -1, -1):
            s = p.surfaces[i]
            if s.visible and geo.point_in_poly(n, geo.boundary(s.points, 12, s.smooth)):
                return "surface", i
        return None, None

    def _insert_mask_point(self, cv, mouse, m):
        best, best_d = None, 14 * self.app.ui_scale
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
        cv.layout(pos, size, w / max(h, 1))
        hovered, mouse, panning = self._canvas_input(cv, pos, size)
        flip = bool(src.origin_bottom ^ self.project.flip_input) if src else False
        self._frame(dl, cv, tex, flip)
        sc = self.app.ui_scale
        io = imgui.get_io()
        p = self.project
        for i, s in enumerate(p.surfaces):
            sel = self.sel_kind == "surface" and self.sel_index == i
            pts = [cv.v(q) for q in s.source]
            dl.add_polyline(pts, col32(*s.color, 1.0 if sel else 0.45), (2.2 if sel else 1.2) * sc, imgui.ImDrawFlags_.closed)
            if sel:
                c = cv.v(s.source.mean(axis=0))
                tw = imgui.calc_text_size(s.name)
                dl.add_text(V2(c.x - tw.x / 2, c.y - tw.y / 2), col32(*s.color), s.name)
                for k, q in enumerate(s.source):
                    self._handle(dl, cv.v(q), s.color, self.sel_point == k, True)
        if panning:
            return
        s = self.selected()
        if not isinstance(s, Surface):
            if hovered and imgui.is_mouse_clicked(0):
                self._pick_source(cv, mouse)
            return
        if hovered and imgui.is_mouse_clicked(0) and not imgui.is_key_down(imgui.Key.space):
            hit = None
            best_d = PICK_R * sc
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
                speed = 0.25 if io.key_shift else 1.0
                d = (mouse - start) / cv.extent * speed
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
            self._loupe(dl, cv, mouse, tex, flip)

    def _pick_source(self, cv, mouse):
        n = cv.to_norm(mouse)
        for i in range(len(self.project.surfaces) - 1, -1, -1):
            if geo.point_in_poly(n, self.project.surfaces[i].source):
                self.select("surface", i)
                return
        self.sel_point = None

    # ------------------------------------------------------------ status / keys
    def draw_status(self):
        io = imgui.get_io()
        app = self.app
        imgui.text_disabled(f"{io.framerate:5.0f} fps")
        imgui.same_line(0, 24)
        imgui.text_disabled(f"Output {self.project.width}x{self.project.height}")
        imgui.same_line(0, 24)
        if self.toast and imgui.get_time() - self.toast_time < 4:
            imgui.text_colored(imgui.ImVec4(*ACCENT, 1), self.toast)
        else:
            imgui.text_disabled("Drag points | drag inside to move | Shift = fine | Alt = corner only | "
                                "Arrows nudge | Tab next point | Wheel zoom | Space/Middle pan | Ctrl+Z undo")

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
            self.app.rebuild_source()
        elif ctrl and imgui.is_key_pressed(K.y, False):
            self.app.history.redo()
            self.app.rebuild_source()
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
