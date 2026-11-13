bl_info = {
    "name": "ProjMap Spout Sender",
    "author": "Siva",
    "version": (1, 1, 0),
    "blender": (3, 6, 0),
    "location": "3D View > Sidebar (N) > ProjMap",
    "description": "Streams the scene camera, or several cameras as one atlas, to ProjMap over Spout",
    "category": "Render",
}

import math
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


def _to_bytes_view(buf):
    try:
        return memoryview(buf)
    except TypeError:
        import numpy as np
        return np.asarray(buf.to_list(), dtype=np.uint8)


def _to_bytes(buf):
    try:
        return memoryview(buf)
    except TypeError:
        import numpy as np
        return np.asarray(buf.to_list(), dtype=np.uint8).tobytes()


def atlas_cameras(scene):
    cams = []
    for item in scene.projmap.cameras:
        ob = item.camera
        if ob is not None and ob.type == "CAMERA" and ob.name in scene.objects:
            cams.append(ob)
    return cams


def atlas_layout(scene, count):
    s = scene.projmap
    if count <= 0:
        return 1, 1
    cols = s.atlas_cols if s.atlas_cols > 0 else min(count, 4)
    cols = max(1, min(cols, count))
    rows = math.ceil(count / cols)
    return cols, rows


def _render_camera(context, cam, w, h):
    st = _state
    scene = context.scene
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
        return fb.read_color(0, 0, w, h, 4, 0, "UBYTE")


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
    st["busy"] = True
    try:
        w, h = output_size(scene)
        if props.mode == "ATLAS":
            cams = atlas_cameras(scene)
            if not cams:
                st["error"] = "Add cameras to the atlas list"
                return
            import numpy as np
            cols, rows = atlas_layout(scene, len(cams))
            aw, ah = w * cols, h * rows
            if aw > 16384 or ah > 16384:
                st["error"] = f"Atlas {aw}x{ah} is too large, lower the tile size"
                return
            atlas = st.get("atlas")
            if atlas is None or atlas.shape != (ah, aw, 4):
                atlas = np.zeros((ah, aw, 4), dtype=np.uint8)
                st["atlas"] = atlas
            for i, cam in enumerate(cams):
                buf = _render_camera(context, cam, w, h)
                tile = np.asarray(_to_bytes_view(buf), dtype=np.uint8).reshape(h, w, 4)
                c, r = i % cols, i // cols
                y0 = (rows - 1 - r) * h  # GPU rows start at the bottom
                atlas[y0:y0 + h, c * w:(c + 1) * w] = tile
            ok = st["sender"].sendImage(atlas.tobytes(), aw, ah, GL_RGBA, props.flip, 0)
            st["size"] = (aw, ah)
            st["layout"] = (cols, rows)
        else:
            cam = scene.camera
            if cam is None:
                st["error"] = "Scene has no active camera"
                return
            buf = _render_camera(context, cam, w, h)
            ok = st["sender"].sendImage(_to_bytes(buf), w, h, GL_RGBA, props.flip, 0)
            st["size"] = (w, h)
            st["layout"] = (1, 1)
        dt = now - st["last"]
        st["fps"] = st["fps"] * 0.9 + (1.0 / dt if dt > 0 else 0) * 0.1
        st["last"] = now
        st["error"] = "" if ok is not False else "Spout send failed"
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
    _state["atlas"] = None


def _is_camera(self, ob):
    return ob.type == "CAMERA"


class PROJMAP_CameraItem(bpy.types.PropertyGroup):
    camera: bpy.props.PointerProperty(name="Camera", type=bpy.types.Object, poll=_is_camera)


class PROJMAP_Props(bpy.types.PropertyGroup):
    mode: bpy.props.EnumProperty(
        name="Mode",
        items=[("CAMERA", "Active camera", "Stream the scene camera"),
               ("ATLAS", "Camera atlas", "Tile several cameras into one stream")],
        default="CAMERA")
    cameras: bpy.props.CollectionProperty(type=PROJMAP_CameraItem)
    camera_index: bpy.props.IntProperty()
    atlas_cols: bpy.props.IntProperty(name="Columns", default=0, min=0, max=8,
                                      description="0 = automatic (up to 4 per row)")
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


class PROJMAP_OT_cam_add(bpy.types.Operator):
    bl_idname = "projmap.cam_add"
    bl_label = "Add cameras"
    bl_description = "Add the selected cameras (or all scene cameras if none are selected)"

    def execute(self, context):
        props = context.scene.projmap
        have = {it.camera for it in props.cameras if it.camera}
        cams = [o for o in context.selected_objects if o.type == "CAMERA"]
        if not cams:
            cams = sorted((o for o in context.scene.objects if o.type == "CAMERA"), key=lambda o: o.name)
        for ob in cams:
            if ob not in have:
                props.cameras.add().camera = ob
        props.camera_index = len(props.cameras) - 1
        return {"FINISHED"}


class PROJMAP_OT_cam_remove(bpy.types.Operator):
    bl_idname = "projmap.cam_remove"
    bl_label = "Remove camera"

    def execute(self, context):
        props = context.scene.projmap
        if 0 <= props.camera_index < len(props.cameras):
            props.cameras.remove(props.camera_index)
            props.camera_index = max(0, props.camera_index - 1)
        return {"FINISHED"}


class PROJMAP_OT_cam_move(bpy.types.Operator):
    bl_idname = "projmap.cam_move"
    bl_label = "Move camera"
    direction: bpy.props.IntProperty(default=-1)

    def execute(self, context):
        props = context.scene.projmap
        i, j = props.camera_index, props.camera_index + self.direction
        if 0 <= i < len(props.cameras) and 0 <= j < len(props.cameras):
            props.cameras.move(i, j)
            props.camera_index = j
        return {"FINISHED"}


class PROJMAP_UL_cameras(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.label(text=f"{index + 1}", icon="OUTLINER_OB_CAMERA")
        row.prop(item, "camera", text="", emboss=False)


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
        layout.prop(props, "mode", expand=True)
        if props.mode == "ATLAS":
            row = layout.row()
            row.template_list("PROJMAP_UL_cameras", "", props, "cameras", props, "camera_index", rows=3)
            side = row.column(align=True)
            side.operator("projmap.cam_add", text="", icon="ADD")
            side.operator("projmap.cam_remove", text="", icon="REMOVE")
            side.separator()
            side.operator("projmap.cam_move", text="", icon="TRIA_UP").direction = -1
            side.operator("projmap.cam_move", text="", icon="TRIA_DOWN").direction = 1
            layout.prop(props, "atlas_cols")
            n = len(atlas_cameras(context.scene))
            cols, rows = atlas_layout(context.scene, n)
            tw, th = output_size(context.scene)
            box = layout.box()
            box.label(text=f"{n} cameras  ->  {cols} x {rows} tiles  ({tw * cols}x{th * rows})", icon="MESH_GRID")
            box.label(text=f"ProjMap: set Input > Atlas to {cols} x {rows}")
        col = layout.column()
        col.enabled = not live
        col.label(text="Tile size" if props.mode == "ATLAS" else "Resolution")
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
            w, h = _state.get("size") or output_size(context.scene)
            layout.label(text=f"{w}x{h}  ~{_state['fps']:.0f} fps", icon="RADIOBUT_ON")
            if props.mode == "ATLAS" and len(atlas_cameras(context.scene)) > 1:
                layout.label(text="Each camera is a full render: fps drops per camera.", icon="INFO")
        if props.mode == "CAMERA" and context.scene.camera is None:
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


classes = (PROJMAP_CameraItem, PROJMAP_Props, PROJMAP_OT_toggle, PROJMAP_OT_cam_add, PROJMAP_OT_cam_remove,
           PROJMAP_OT_cam_move, PROJMAP_UL_cameras, PROJMAP_OT_install, PROJMAP_PT_panel, PROJMAP_Prefs)


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
