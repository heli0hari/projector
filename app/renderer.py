import numpy as np
import moderngl

from . import geometry as geo

TESS = 40  # fine mesh resolution per surface

SURFACE_VS = """
#version 330
in vec2 in_pos;     // output space 0..1, y down
in vec2 in_uv;      // input space 0..1, y down
in vec2 in_local;   // surface parameter 0..1
out vec2 v_uv;
out vec2 v_local;
void main() {
    v_uv = in_uv;
    v_local = in_local;
    gl_Position = vec4(in_pos.x * 2.0 - 1.0, 1.0 - in_pos.y * 2.0, 0.0, 1.0);
}
"""

SURFACE_FS = """
#version 330
uniform sampler2D tex;
uniform bool flip_v;
uniform float opacity;
uniform float brightness;
uniform float feather;
uniform bool calib;
uniform vec3 tint;
uniform vec2 grid;      // control grid cols, rows
in vec2 v_uv;
in vec2 v_local;
out vec4 frag;

float line(float x, float n, float w) {
    float f = abs(fract(x * n + 0.5) - 0.5) / fwidth(x * n);
    return 1.0 - clamp(f - w, 0.0, 1.0);
}

void main() {
    vec3 col;
    if (calib) {
        vec2 c = floor(v_local * 8.0);
        float checker = mod(c.x + c.y, 2.0);
        col = mix(vec3(0.06), tint * 0.35, checker);
        float fine = max(line(v_local.x, 8.0, 0.0), line(v_local.y, 8.0, 0.0));
        float ctrl = max(line(v_local.x, grid.x, 1.0), line(v_local.y, grid.y, 1.0));
        col = mix(col, tint * 0.7, fine * 0.6);
        col = mix(col, vec3(1.0), ctrl);
        float d = length(v_local - 0.5);
        col = mix(col, tint, 1.0 - smoothstep(0.02, 0.025, d));
    } else {
        vec2 uv = vec2(v_uv.x, flip_v ? 1.0 - v_uv.y : v_uv.y);
        col = texture(tex, uv).rgb * brightness;
    }
    float a = opacity;
    if (feather > 0.0) {
        vec2 e = min(v_local, 1.0 - v_local);
        a *= smoothstep(0.0, feather, e.x) * smoothstep(0.0, feather, e.y);
    }
    frag = vec4(col, a);
}
"""

FLAT_VS = """
#version 330
in vec2 in_pos;
in vec4 in_col;
out vec4 v_col;
void main() {
    v_col = in_col;
    gl_Position = vec4(in_pos.x * 2.0 - 1.0, 1.0 - in_pos.y * 2.0, 0.0, 1.0);
}
"""

FLAT_FS = """
#version 330
in vec4 v_col;
out vec4 frag;
void main() { frag = v_col; }
"""

BLIT_VS = """
#version 330
in vec2 in_pos;
out vec2 v_uv;
void main() {
    v_uv = in_pos * 0.5 + 0.5;
    gl_Position = vec4(in_pos, 0.0, 1.0);
}
"""

BLIT_FS = """
#version 330
uniform sampler2D tex;
in vec2 v_uv;
out vec4 frag;
void main() { frag = vec4(texture(tex, v_uv).rgb, 1.0); }
"""


def make_blitter(ctx):
    prog = ctx.program(vertex_shader=BLIT_VS, fragment_shader=BLIT_FS)
    vbo = ctx.buffer(np.array([-1, -1, 1, -1, -1, 1, 1, 1], dtype="f4").tobytes())
    vao = ctx.vertex_array(prog, [(vbo, "2f", "in_pos")])
    return prog, vao


class Renderer:
    def __init__(self, ctx):
        self.ctx = ctx
        self.surface_prog = ctx.program(vertex_shader=SURFACE_VS, fragment_shader=SURFACE_FS)
        self.flat_prog = ctx.program(vertex_shader=FLAT_VS, fragment_shader=FLAT_FS)
        self.blit_prog, self.blit_vao = make_blitter(ctx)
        self.cache = {}
        self.targets = {}  # output index -> dict(size, comp_tex, comp_fbo, guide_tex, guide_fbo)
        idx = []
        n = TESS + 1
        for j in range(TESS):
            for i in range(TESS):
                a = j * n + i
                idx += [a, a + 1, a + n, a + 1, a + n + 1, a + n]
        self.ibo = ctx.buffer(np.array(idx, dtype="i4").tobytes())

    def _target(self, idx, w, h):
        t = self.targets.get(idx)
        if t and t["size"] == (w, h):
            return t
        if t:
            for k in ("comp_fbo", "comp_tex", "guide_fbo", "guide_tex"):
                t[k].release()
        t = {"size": (w, h)}
        for k in ("comp", "guide"):
            tex = self.ctx.texture((w, h), 4)
            tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
            t[k + "_tex"] = tex
            t[k + "_fbo"] = self.ctx.framebuffer([tex])
        self.targets[idx] = t
        return t

    def tex(self, idx):
        t = self.targets.get(idx)
        return t["comp_tex"] if t else None

    def guide_tex(self, idx):
        t = self.targets.get(idx)
        return t["guide_tex"] if t else None

    def prune(self, n_outputs):
        for idx in [k for k in self.targets if k >= n_outputs]:
            t = self.targets.pop(idx)
            for k in ("comp_fbo", "comp_tex", "guide_fbo", "guide_tex"):
                t[k].release()

    # ---------- meshes ----------
    def _surface_vao(self, s):
        key = id(s)
        entry = self.cache.get(key)
        if entry and entry[0] is s and entry[1] == s.version:
            return entry[3]
        if entry:
            entry[2].release()
            entry[3].release()
        t = np.linspace(0, 1, TESS + 1)
        pos = geo.eval_grid(s.points, t, t, s.smooth)
        vv, uu = np.meshgrid(t, t, indexing="ij")
        local = np.stack([uu, vv], axis=-1)
        hs = geo.homography(geo.UNIT_SQUARE, s.source)
        uv = geo.apply_h(hs, local)
        data = np.concatenate([pos, uv, local], axis=-1).astype("f4")
        vbo = self.ctx.buffer(data.tobytes())
        vao = self.ctx.vertex_array(self.surface_prog,
                                    [(vbo, "2f 2f 2f", "in_pos", "in_uv", "in_local")],
                                    self.ibo)
        self.cache[key] = (s, s.version, vbo, vao)
        return vao

    def _gc(self, live):
        ids = {id(o) for o in live}
        for k in list(self.cache.keys()):
            if k not in ids or self.cache[k][0] not in live:
                e = self.cache.pop(k)
                e[2].release()
                e[3].release()

    def _flat(self, verts, mode):
        if len(verts) == 0:
            return
        vbo = self.ctx.buffer(np.asarray(verts, dtype="f4").tobytes())
        vao = self.ctx.vertex_array(self.flat_prog, [(vbo, "2f 4f", "in_pos", "in_col")])
        vao.render(mode)
        vao.release()
        vbo.release()

    # ---------- passes ----------
    def gc(self, project):
        self._gc(project.surfaces + project.masks)
        self.prune(len(project.outputs))

    def render(self, project, source, calib, idx):
        out = project.outputs[idx]
        t = self._target(idx, out.width, out.height)
        ctx = self.ctx
        t["comp_fbo"].use()
        ctx.viewport = (0, 0, out.width, out.height)
        ctx.clear(0, 0, 0, 1)
        ctx.enable(moderngl.BLEND)
        ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA
        if source is not None and source.texture is not None:
            source.texture.use(0)
        p = self.surface_prog
        p["tex"].value = 0
        p["flip_v"].value = bool(source.origin_bottom ^ project.flip_input) if source else False
        p["calib"].value = calib
        for s in project.surfaces:
            if not s.visible or s.output != idx:
                continue
            vao = self._surface_vao(s)
            p["opacity"].value = float(s.opacity)
            p["brightness"].value = float(s.brightness)
            p["feather"].value = float(s.feather)
            p["tint"].value = s.color
            p["grid"].value = (float(s.cols), float(s.rows))
            vao.render(moderngl.TRIANGLES)
        # masks: blackout
        verts = []
        for m in project.masks:
            if not m.visible or m.output != idx or len(m.points) < 3:
                continue
            for tri in geo.triangulate(m.points):
                for i in tri:
                    x, y = m.points[i]
                    verts.append([x, y, 0, 0, 0, 1])
        self._flat(verts, moderngl.TRIANGLES)
        ctx.disable(moderngl.BLEND)

    def render_guides(self, project, idx, sel_kind, sel_index, sel_point):
        """Composite + outlines, for calibrating directly on the wall."""
        out = project.outputs[idx]
        w, h = out.width, out.height
        t = self.targets[idx]
        ctx = self.ctx
        t["guide_fbo"].use()
        ctx.viewport = (0, 0, w, h)
        t["comp_tex"].use(0)
        self.blit_prog["tex"].value = 0
        self.blit_vao.render(moderngl.TRIANGLE_STRIP)
        lines = []
        quads = []
        px = 1.0 / w
        py = 1.0 / h

        def seg(a, b, c):
            lines.append([a[0], a[1], *c])
            lines.append([b[0], b[1], *c])

        def square(p, r, c):
            x0, x1 = p[0] - r * px, p[0] + r * px
            y0, y1 = p[1] - r * py, p[1] + r * py
            for q in ((x0, y0), (x1, y0), (x1, y1), (x0, y0), (x1, y1), (x0, y1)):
                quads.append([q[0], q[1], *c])

        for i, s in enumerate(project.surfaces):
            if not s.visible or s.output != idx:
                continue
            selected = sel_kind == "surface" and sel_index == i
            col = (*s.color, 1.0) if selected else (*s.color, 0.55)
            b = geo.boundary(s.points, 24, s.smooth)
            for k in range(len(b)):
                seg(b[k], b[(k + 1) % len(b)], col)
            if selected:
                pts = s.points
                for r in range(pts.shape[0]):
                    for c in range(pts.shape[1]):
                        if c + 1 < pts.shape[1]:
                            seg(pts[r, c], pts[r, c + 1], (1, 1, 1, 0.35))
                        if r + 1 < pts.shape[0]:
                            seg(pts[r, c], pts[r + 1, c], (1, 1, 1, 0.35))
                        hot = sel_point == (r, c)
                        square(pts[r, c], 9 if hot else 5, (1, 1, 1, 1) if hot else col)
        for i, m in enumerate(project.masks):
            if not m.visible or m.output != idx:
                continue
            selected = sel_kind == "mask" and sel_index == i
            col = (1, 0.2, 0.2, 1.0 if selected else 0.5)
            for k in range(len(m.points)):
                seg(m.points[k], m.points[(k + 1) % len(m.points)], col)
            if selected:
                for k, pt in enumerate(m.points):
                    hot = sel_point == k
                    square(pt, 9 if hot else 5, (1, 1, 1, 1) if hot else col)
        ctx.enable(moderngl.BLEND)
        ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA
        self._flat(lines, moderngl.LINES)
        self._flat(quads, moderngl.TRIANGLES)
        ctx.disable(moderngl.BLEND)
