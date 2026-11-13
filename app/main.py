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
        self.source = None
        self.rebuild_source()
        self.editor = Editor(self)

        self.out_win = None
        self.out_ctx = None
        self.out_tex = None
        self.out_tex_key = None
        self.out_blit = None
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

    # ------------------------------------------------------------ output window
    @property
    def output_open(self):
        return self.out_win is not None

    def toggle_output(self):
        if self.out_win:
            self.close_output()
        else:
            self.open_output()

    def open_output(self):
        mons = self.monitors()
        single = len(mons) < 2
        gl_hints()
        glfw.window_hint(glfw.AUTO_ICONIFY, False)
        glfw.window_hint(glfw.FOCUS_ON_SHOW, False)
        if not single:
            glfw.window_hint(glfw.DECORATED, False)
        w, h = (960, 540) if single else (640, 360)
        self.out_win = glfw.create_window(w, h, "ProjMap Output", None, self.window)
        if not self.out_win:
            self.editor.notify("Could not create output window")
            return
        self._out_single = single
        glfw.set_key_callback(self.out_win, self._out_key)
        glfw.make_context_current(self.out_win)
        glfw.swap_interval(1)
        self.out_ctx = moderngl.create_context()
        self.out_blit = make_blitter(self.out_ctx)
        self.out_tex = None
        self.out_tex_key = None
        glfw.make_context_current(self.window)
        self.place_output()
        glfw.show_window(self.out_win)
        glfw.focus_window(self.window)

    def place_output(self):
        if not self.out_win or self._out_single:
            return
        mons = self.monitors()
        m = mons[min(self.project.output_monitor, len(mons) - 1)]
        x, y = glfw.get_monitor_pos(m)
        mode = glfw.get_video_mode(m)
        glfw.set_window_pos(self.out_win, x, y)
        # +1 px height avoids the driver's exclusive-fullscreen switch on Windows
        glfw.set_window_size(self.out_win, mode.size.width, mode.size.height + 1)

    def close_output(self):
        if not self.out_win:
            return
        glfw.make_context_current(self.out_win)
        if self.out_tex:
            self.out_tex.release()
        self.out_blit[1].release()
        self.out_blit[0].release()
        self.out_ctx.release()
        glfw.destroy_window(self.out_win)
        self.out_win = None
        self.out_ctx = None
        self.out_tex = None
        glfw.make_context_current(self.window)

    def _out_key(self, win, key, scancode, action, mods):
        if action not in (glfw.PRESS, glfw.REPEAT):
            return
        step = 10 if mods & glfw.MOD_SHIFT else 1
        e = self.editor
        if key == glfw.KEY_ESCAPE or (key == glfw.KEY_F and action == glfw.PRESS):
            self._close_requested = True
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

    def draw_output(self, tex):
        win = self.out_win
        glfw.make_context_current(win)
        key = (tex.glo, tex.size)
        if self.out_tex_key != key:
            if self.out_tex:
                self.out_tex.release()
            self.out_tex = self.out_ctx.external_texture(tex.glo, tex.size, 4, 0, "f1")
            self.out_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
            self.out_tex_key = key
        fw, fh = glfw.get_framebuffer_size(win)
        self.out_ctx.screen.use()
        self.out_ctx.viewport = (0, 0, fw, fh)
        self.out_ctx.clear(0, 0, 0, 1)
        self.out_tex.use(0)
        prog, vao = self.out_blit
        prog["tex"].value = 0
        vao.render(moderngl.TRIANGLE_STRIP)
        glfw.swap_buffers(win)
        glfw.make_context_current(self.window)

    # ------------------------------------------------------------ loop
    def frame(self):
        glfw.poll_events()
        self.impl.process_inputs()
        glfw.make_context_current(self.window)

        if self.source:
            self.source.update()
        e = self.editor
        self.renderer.render(self.project, self.source, e.calib)
        out_tex = self.renderer.comp_tex
        if self.out_win and e.projector_guides:
            self.renderer.render_guides(self.project, e.sel_kind, e.sel_index, e.sel_point)
            out_tex = self.renderer.guide_tex

        imgui.new_frame()
        fw, fh = glfw.get_framebuffer_size(self.window)
        e.draw(fw, fh)
        imgui.render()
        self.ctx.screen.use()
        self.ctx.viewport = (0, 0, fw, fh)
        self.ctx.clear(0.075, 0.08, 0.095, 1)
        self.impl.render(imgui.get_draw_data())
        glfw.swap_buffers(self.window)

        if self.out_win:
            self.ctx.finish()
            self.draw_output(out_tex)
            if glfw.window_should_close(self.out_win) or getattr(self, "_close_requested", False):
                self._close_requested = False
                self.close_output()

        if self.dirty and time.time() - self.last_autosave > 5:
            self.autosave()
            self.last_autosave = time.time()

    def run(self):
        while not glfw.window_should_close(self.window):
            self.frame()
            if glfw.get_window_attrib(self.window, glfw.ICONIFIED) and not self.out_win:
                time.sleep(0.05)
        self.autosave()
        self.close_output()
        if self.source:
            self.source.release()
        self.impl.shutdown()
        glfw.terminate()


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else None
    App(path).run()


if __name__ == "__main__":
    main()
