import json
import os
import sys
import time

if os.getenv("XDG_SESSION_TYPE") == "wayland" and not os.getenv("PYOPENGL_PLATFORM"):
    os.environ["PYOPENGL_PLATFORM"] = "x11"

import glfw
import moderngl
from imgui_bundle import imgui
from imgui_bundle.python_backends.glfw_backend import GlfwRenderer

from . import theme
from .editor import Editor
from .model import Project, History
from .renderer import Renderer, make_blitter
from .sources import make_source


def config_dir():
    base = os.getenv("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    d = os.path.join(base, "ProjMap")
    os.makedirs(d, exist_ok=True)
    return d


AUTOSAVE = os.path.join(config_dir(), "autosave.projmap.json")


def gl_hints():
    glfw.default_window_hints()
    glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 3)
    glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 3)
    glfw.window_hint(glfw.OPENGL_PROFILE, glfw.OPENGL_CORE_PROFILE)
    glfw.window_hint(glfw.OPENGL_FORWARD_COMPAT, True)


class App:
    def __init__(self, project_path=None):
        if not glfw.init():
            raise SystemExit("Could not initialise GLFW")
        gl_hints()
        glfw.window_hint(glfw.MAXIMIZED, True)
        self.window = glfw.create_window(1600, 960, "ProjMap", None, None)
        glfw.make_context_current(self.window)
        glfw.swap_interval(0 if os.name == "nt" else 1)
        self.ctx = moderngl.create_context()

        imgui.create_context()
        io = imgui.get_io()
        io.set_ini_filename("")
        xs, _ = glfw.get_window_content_scale(self.window)
        self.ui_scale = max(1.0, xs)
        self.fonts = theme.load_fonts(self.ui_scale)
        theme.apply(self.ui_scale)
        self.impl = GlfwRenderer(self.window)

        self.project = Project()
        self.history = History(self.project)
        self.dirty = False
        path = project_path or (AUTOSAVE if os.path.exists(AUTOSAVE) else None)
        if path:
            try:
                self.project.load(path)
                if path == AUTOSAVE:
                    self.project.path = self._last_path()
            except Exception as e:
                print("Could not load project:", e)

        self.renderer = Renderer(self.ctx)
        self.gpu_name = self.ctx.info.get("GL_RENDERER", "")
        from .renderer import BLIT_VS
        import numpy as _np
        self._bd_prog = self.ctx.program(vertex_shader=BLIT_VS, fragment_shader=theme.BACKDROP_FS)
        self._bd_vbo = self.ctx.buffer(_np.array([-1, -1, 1, -1, -1, 1, 1, 1], dtype="f4").tobytes())
        self._bd_vao = self.ctx.vertex_array(self._bd_prog, [(self._bd_vbo, "2f", "in_pos")])
        self.source = None
        self.rebuild_source()
        self.editor = Editor(self)

        self.outs = {}       # output index -> OutWindow
        self._placing = False
        self._close_requests = set()
        self.last_autosave = time.time()

    # ------------------------------------------------------------ misc
    def _last_path(self):
        try:
            with open(os.path.join(config_dir(), "last.json")) as f:
                p = json.load(f).get("path")
                return p if p and os.path.exists(p) else None
        except Exception:
            return None

    def autosave(self):
        try:
            path = self.project.path
            with open(AUTOSAVE, "w", encoding="utf-8") as f:
                json.dump(self.project.to_dict(), f)
            self.project.path = path
            with open(os.path.join(config_dir(), "last.json"), "w") as f:
                json.dump({"path": path}, f)
        except Exception as e:
            print("Autosave failed:", e)

    def rebuild_source(self):
        glfw.make_context_current(self.window)
        if self.source is not None:
            self.source.release()
        self.source = make_source(self.ctx, self.project)

    # ------------------------------------------------------------ monitors
    def monitors(self):
        return glfw.get_monitors() or []

    def monitor_names(self):
        out = []
        for i, m in enumerate(self.monitors()):
            name = glfw.get_monitor_name(m)
            name = name.decode() if isinstance(name, bytes) else str(name)
            mode = glfw.get_video_mode(m)
            out.append(f"{i + 1}: {name}  {mode.size.width}x{mode.size.height}")
        return out

    def monitor_resolution(self, idx):
        mons = self.monitors()
        if not mons:
            return None
        mode = glfw.get_video_mode(mons[min(idx, len(mons) - 1)])
        return mode.size.width, mode.size.height

    # ------------------------------------------------------------ output windows
    def is_open(self, idx=None):
        return (self.project.active_output if idx is None else idx) in self.outs

    @property
    def output_open(self):
        return self.is_open()

    def open_count(self):
        return len(self.outs)

    def toggle_output(self, idx=None):
        idx = self.project.active_output if idx is None else idx
        if idx in self.outs:
            self.close_output(idx)
        else:
            self.open_output(idx)

    def open_output(self, idx):
        if idx in self.outs or idx >= len(self.project.outputs):
            return
        o = self.project.outputs[idx]
        gl_hints()
        glfw.window_hint(glfw.AUTO_ICONIFY, False)
        glfw.window_hint(glfw.FOCUS_ON_SHOW, False)
        glfw.window_hint(glfw.VISIBLE, False)
        win = glfw.create_window(max(64, o.width), max(64, o.height), f"ProjMap - {o.name}", None, self.window)
        if not win:
            self.editor.notify("Could not create output window")
            return
        glfw.make_context_current(win)
        ctx = moderngl.create_context()
        ow = {"win": win, "ctx": ctx, "blit": make_blitter(ctx), "tex": None, "key": None}
        glfw.make_context_current(self.window)
        self.outs[idx] = ow
        glfw.set_key_callback(win, lambda w, k, sc, a, m, i=idx: self._out_key(i, k, a, m))
        glfw.set_window_pos_callback(win, lambda w, x, y, i=idx: self._out_moved(i, x, y))
        glfw.set_window_size_callback(win, lambda w, ww, hh, i=idx: self._out_resized(i, ww, hh))
        self.place_output(idx)
        glfw.show_window(win)
        glfw.focus_window(self.window)
        self._sync_vsync()

    def place_output(self, idx=None):
        idx = self.project.active_output if idx is None else idx
        ow = self.outs.get(idx)
        if not ow:
            return
        o = self.project.outputs[idx]
        win = ow["win"]
        self._placing = True
        try:
            if o.mode == "fullscreen":
                mons = self.monitors()
                m = mons[min(o.monitor, len(mons) - 1)]
                x, y = glfw.get_monitor_pos(m)
                mode = glfw.get_video_mode(m)
                glfw.set_window_attrib(win, glfw.DECORATED, False)
                glfw.set_window_attrib(win, glfw.RESIZABLE, False)
                glfw.set_window_attrib(win, glfw.FLOATING, False)
                glfw.set_window_pos(win, x, y)
                # +1 px height avoids the driver's exclusive-fullscreen switch on Windows
                glfw.set_window_size(win, mode.size.width, mode.size.height + 1)
            else:
                glfw.set_window_attrib(win, glfw.DECORATED, not o.borderless)
                glfw.set_window_attrib(win, glfw.RESIZABLE, not o.lock)
                glfw.set_window_attrib(win, glfw.FLOATING, bool(o.on_top))
                glfw.set_window_pos(win, int(o.x), int(o.y))
                glfw.set_window_size(win, int(o.width), int(o.height))
        finally:
            self._placing = False

    def _out_moved(self, idx, x, y):
        if self._placing or idx >= len(self.project.outputs):
            return
        o = self.project.outputs[idx]
        if o.mode == "window":
            o.x, o.y = x, y
            self.dirty = True

    def _out_resized(self, idx, w, h):
        if self._placing or idx >= len(self.project.outputs) or w < 16 or h < 16:
            return
        o = self.project.outputs[idx]
        if o.mode == "window":
            o.width, o.height = w, h
            self.dirty = True

    def close_output(self, idx):
        ow = self.outs.pop(idx, None)
        if not ow:
            return
        glfw.make_context_current(ow["win"])
        if ow["tex"]:
            ow["tex"].release()
        ow["blit"][1].release()
        ow["blit"][0].release()
        ow["ctx"].release()
        glfw.destroy_window(ow["win"])
        glfw.make_context_current(self.window)
        self._sync_vsync()

    def close_all_outputs(self):
        for idx in list(self.outs):
            self.close_output(idx)

    def _sync_vsync(self):
        # only one window waits for vsync, otherwise every extra window halves the frame rate
        for k, idx in enumerate(sorted(self.outs)):
            glfw.make_context_current(self.outs[idx]["win"])
            glfw.swap_interval(1 if k == 0 else 0)
        glfw.make_context_current(self.window)

    def _out_key(self, idx, key, action, mods):
        if action not in (glfw.PRESS, glfw.REPEAT):
            return
        step = 10 if mods & glfw.MOD_SHIFT else 1
        e = self.editor
        if key == glfw.KEY_ESCAPE:
            self._close_requests.add(idx)
        elif key == glfw.KEY_LEFT:
            e.nudge(-step, 0)
        elif key == glfw.KEY_RIGHT:
            e.nudge(step, 0)
        elif key == glfw.KEY_UP:
            e.nudge(0, -step)
        elif key == glfw.KEY_DOWN:
            e.nudge(0, step)
        elif key == glfw.KEY_TAB and action == glfw.PRESS:
            e.cycle_point(-1 if mods & glfw.MOD_SHIFT else 1)
        elif key == glfw.KEY_G and action == glfw.PRESS:
            e.projector_guides = not e.projector_guides
        elif key == glfw.KEY_C and action == glfw.PRESS:
            e.calib = not e.calib

    def draw_output(self, idx, tex):
        ow = self.outs[idx]
        win = ow["win"]
        glfw.make_context_current(win)
        key = (tex.glo, tex.size)
        if ow["key"] != key:
            if ow["tex"]:
                ow["tex"].release()
            ow["tex"] = ow["ctx"].external_texture(tex.glo, tex.size, 4, 0, "f1")
            ow["tex"].filter = (moderngl.LINEAR, moderngl.LINEAR)
            ow["key"] = key
        fw, fh = glfw.get_framebuffer_size(win)
        ctx = ow["ctx"]
        ctx.screen.use()
        ctx.viewport = (0, 0, fw, fh)
        ctx.clear(0, 0, 0, 1)
        ow["tex"].use(0)
        prog, vao = ow["blit"]
        prog["tex"].value = 0
        vao.render(moderngl.TRIANGLE_STRIP)
        glfw.swap_buffers(win)

    # ------------------------------------------------------------ loop
    def frame(self):
        glfw.poll_events()
        self.impl.process_inputs()
        glfw.make_context_current(self.window)

        p = self.project
        for idx in [i for i in self.outs if i >= len(p.outputs)]:
            self.close_output(idx)
        if self.source:
            self.source.update()
        e = self.editor
        r = self.renderer
        r.gc(p)
        to_render = set(self.outs) | {p.active_output}
        out_tex = {}
        for idx in sorted(to_render):
            r.render(p, self.source, e.calib, idx)
            if idx in self.outs:
                if e.projector_guides:
                    r.render_guides(p, idx, e.sel_kind, e.sel_index, e.sel_point)
                    out_tex[idx] = r.guide_tex(idx)
                else:
                    out_tex[idx] = r.tex(idx)

        imgui.new_frame()
        fw, fh = glfw.get_framebuffer_size(self.window)
        e.draw(fw, fh)
        imgui.render()
        self.ctx.screen.use()
        self.ctx.viewport = (0, 0, fw, fh)
        self.ctx.disable(moderngl.BLEND)
        self._bd_prog["res"].value = (float(fw), float(fh))
        self._bd_prog["t"].value = float(time.perf_counter() % 10000)
        self._bd_vao.render(moderngl.TRIANGLE_STRIP)
        self.impl.render(imgui.get_draw_data())
        glfw.swap_buffers(self.window)

        if self.outs:
            self.ctx.finish()
            for idx in sorted(self.outs):
                if idx in out_tex:
                    self.draw_output(idx, out_tex[idx])
            glfw.make_context_current(self.window)
            for idx in list(self.outs):
                if glfw.window_should_close(self.outs[idx]["win"]) or idx in self._close_requests:
                    self.close_output(idx)
            self._close_requests.clear()

        if self.dirty and time.time() - self.last_autosave > 5:
            self.autosave()
            self.last_autosave = time.time()

    def run(self):
        while not glfw.window_should_close(self.window):
            self.frame()
            if glfw.get_window_attrib(self.window, glfw.ICONIFIED) and not self.outs:
                time.sleep(0.05)
        self.autosave()
        self.close_all_outputs()
        if self.source:
            self.source.release()
        self.impl.shutdown()
        glfw.terminate()


def main():
    from .gpu_pref import ensure_and_relaunch
    ensure_and_relaunch()
    path = sys.argv[1] if len(sys.argv) > 1 else None
    App(path).run()


if __name__ == "__main__":
    main()
