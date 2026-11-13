bl_info = {
    "name": "ProjMap Spout Sender",
    "author": "Siva",
    "version": (1, 0, 0),
    "blender": (3, 6, 0),
    "location": "3D View > Sidebar (N) > ProjMap",
    "description": "Streams the active scene camera to ProjMap (or any Spout app) in real time",
    "category": "Render",
}

import os
import subprocess
import sys
import time

import bpy
import gpu

GL_RGBA = 0x1908

_state = {
    "handler": None,
    "timer": False,
    "offscreen": None,
    "sender": None,
    "busy": False,
    "last": 0.0,
    "fps": 0.0,
    "error": "",
}


def modules_dir():
    path = bpy.utils.user_resource("SCRIPTS", path="modules", create=True)
    if path not in sys.path:
        sys.path.append(path)
    return path


def spout_module():
    modules_dir()
    try:
        import SpoutGL
        return SpoutGL
    except Exception:
        return None


def output_size(scene):
    s = scene.projmap
    if s.use_render_size:
        r = scene.render
        pct = r.resolution_percentage / 100.0
        return max(16, int(r.resolution_x * pct)), max(16, int(r.resolution_y * pct))
    return s.width, s.height


def _to_bytes(buf):
    try:
        return memoryview(buf)
    except TypeError:
        import numpy as np
        return np.asarray(buf.to_list(), dtype=np.uint8).tobytes()


def draw_callback():
    st = _state
    if st["busy"] or st["sender"] is None:
        return
    context = bpy.context
    scene = context.scene
    props = scene.projmap
    now = time.perf_counter()
    if now - st["last"] < 1.0 / max(1, props.max_fps):
        return
    cam = scene.camera
    if cam is None:
        st["error"] = "Scene has no active camera"
        return
    st["busy"] = True
    try:
        w, h = output_size(scene)
        off = st["offscreen"]
        if off is None or off.width != w or off.height != h:
            if off is not None:
                off.free()
            off = gpu.types.GPUOffScreen(w, h)
            st["offscreen"] = off
        depsgraph = context.evaluated_depsgraph_get()
        view = cam.matrix_world.inverted()
        proj = cam.calc_matrix_camera(depsgraph, x=w, y=h,
                                      scale_x=scene.render.pixel_aspect_x,
                                      scale_y=scene.render.pixel_aspect_y)
        off.draw_view3d(scene, context.view_layer, context.space_data, context.region,
                        view, proj, do_color_management=True)
        with off.bind():
            fb = gpu.state.active_framebuffer_get()
            buf = fb.read_color(0, 0, w, h, 4, 0, "UBYTE")
        st["sender"].sendImage(_to_bytes(buf), w, h, GL_RGBA, props.flip, 0)
        dt = now - st["last"]
        st["fps"] = st["fps"] * 0.9 + (1.0 / dt if dt > 0 else 0) * 0.1
        st["last"] = now
        st["error"] = ""
    except Exception as e:
        st["error"] = str(e)
    finally:
        st["busy"] = False


def redraw_timer():
    if _state["sender"] is None:
        _state["timer"] = False
        return None
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()
    fps = max(1, bpy.context.scene.projmap.max_fps)
    return 1.0 / fps


def start(context):
    SpoutGL = spout_module()
    if SpoutGL is None:
        return "SpoutGL is not installed. Use the add-on preferences to install it."
    backend = getattr(gpu.platform, "backend_type_get", lambda: "OPENGL")()
    if backend != "OPENGL":
        return f"Spout needs the OpenGL backend (current: {backend}). Preferences > System > Backend."
    sender = SpoutGL.SpoutSender()
    sender.setSenderName(context.scene.projmap.sender_name)
    _state["sender"] = sender
    _state["handler"] = bpy.types.SpaceView3D.draw_handler_add(draw_callback, (), "WINDOW", "POST_PIXEL")
    if not _state["timer"]:
        bpy.app.timers.register(redraw_timer, first_interval=0.1)
        _state["timer"] = True
    return None


def stop():
    if _state["handler"] is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_state["handler"], "WINDOW")
        _state["handler"] = None
    if _state["sender"] is not None:
        try:
            _state["sender"].releaseSender()
        except Exception:
            pass
        _state["sender"] = None
    if _state["offscreen"] is not None:
        _state["offscreen"].free()
        _state["offscreen"] = None


class PROJMAP_Props(bpy.types.PropertyGroup):
    sender_name: bpy.props.StringProperty(name="Sender", default="Blender")
    use_render_size: bpy.props.BoolProperty(name="Use render resolution", default=True)
    width: bpy.props.IntProperty(name="Width", default=1920, min=16, max=8192)
    height: bpy.props.IntProperty(name="Height", default=1080, min=16, max=8192)
    max_fps: bpy.props.IntProperty(name="Max FPS", default=60, min=1, max=240)
    flip: bpy.props.BoolProperty(name="Flip vertically", default=True,
                                 description="Toggle if the image arrives upside down")


class PROJMAP_OT_toggle(bpy.types.Operator):
    bl_idname = "projmap.toggle"
    bl_label = "Start / Stop streaming"

    def execute(self, context):
        if _state["sender"] is not None:
            stop()
            return {"FINISHED"}
        err = start(context)
        if err:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}
        return {"FINISHED"}


class PROJMAP_OT_install(bpy.types.Operator):
    bl_idname = "projmap.install_spout"
    bl_label = "Install SpoutGL"
    bl_description = "Installs the SpoutGL Python package into Blender's user modules folder"

    def execute(self, context):
        target = modules_dir()
        py = sys.executable
        try:
            subprocess.call([py, "-m", "ensurepip", "--user"])
            subprocess.check_call([py, "-m", "pip", "install", "--upgrade", "SpoutGL", "--target", target])
        except Exception as e:
            self.report({"ERROR"}, f"Install failed: {e}")
            return {"CANCELLED"}
        if spout_module() is None:
            self.report({"WARNING"}, "Installed, but restart Blender to load it")
        else:
            self.report({"INFO"}, "SpoutGL installed")
        return {"FINISHED"}


class PROJMAP_PT_panel(bpy.types.Panel):
    bl_label = "ProjMap Spout"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "ProjMap"

    def draw(self, context):
        layout = self.layout
        props = context.scene.projmap
        live = _state["sender"] is not None
        if spout_module() is None:
            box = layout.box()
            box.label(text="SpoutGL not installed", icon="ERROR")
            box.operator("projmap.install_spout", icon="IMPORT")
            return
        col = layout.column()
        col.enabled = not live
        col.prop(props, "sender_name")
        col.prop(props, "use_render_size")
        sub = col.row(align=True)
        sub.enabled = not props.use_render_size
        sub.prop(props, "width")
        sub.prop(props, "height")
        layout.prop(props, "max_fps")
        layout.prop(props, "flip")
        row = layout.row()
        row.scale_y = 1.6
        row.operator("projmap.toggle", text="Stop streaming" if live else "Start streaming",
                     icon="PAUSE" if live else "PLAY", depress=live)
        if live:
            w, h = output_size(context.scene)
            layout.label(text=f"{w}x{h}  ~{_state['fps']:.0f} fps", icon="RADIOBUT_ON")
        if context.scene.camera is None:
            layout.label(text="No active camera", icon="ERROR")
        if _state["error"]:
            layout.label(text=_state["error"], icon="ERROR")
        layout.label(text="Uses the viewport's shading mode.", icon="INFO")


class PROJMAP_Prefs(bpy.types.AddonPreferences):
    bl_idname = __name__

    def draw(self, context):
        layout = self.layout
        if spout_module() is None:
            layout.operator("projmap.install_spout", icon="IMPORT")
        else:
            layout.label(text="SpoutGL is installed", icon="CHECKMARK")


classes = (PROJMAP_Props, PROJMAP_OT_toggle, PROJMAP_OT_install, PROJMAP_PT_panel, PROJMAP_Prefs)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.projmap = bpy.props.PointerProperty(type=PROJMAP_Props)
    modules_dir()


def unregister():
    stop()
    del bpy.types.Scene.projmap
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
