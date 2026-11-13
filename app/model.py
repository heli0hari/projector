import copy
import json
import numpy as np

from . import geometry as geo

PALETTE = [
    (0.85, 0.95, 0.42), (0.42, 0.89, 0.95), (0.98, 0.72, 0.42), (0.68, 0.58, 0.98),
    (0.95, 0.50, 0.62), (0.48, 0.95, 0.72), (0.93, 0.93, 0.90), (0.95, 0.86, 0.45),
]


class Surface:
    def __init__(self, name="Surface", color_index=0):
        self.name = name
        self.visible = True
        self.opacity = 1.0
        self.brightness = 1.0
        self.feather = 0.0
        self.smooth = True
        self.color_index = color_index
        self.rows = 1
        self.cols = 1
        self.points = np.array([[[0.25, 0.25], [0.75, 0.25]],
                                [[0.25, 0.75], [0.75, 0.75]]], dtype=np.float64)
        self.source = geo.UNIT_SQUARE.copy()  # TL, TR, BR, BL in input space
        self.output = 0
        self.version = 0

    @property
    def color(self):
        return PALETTE[self.color_index % len(PALETTE)]

    def touch(self):
        self.version += 1

    def set_grid(self, rows, cols):
        rows = max(1, min(16, rows))
        cols = max(1, min(16, cols))
        if rows == self.rows and cols == self.cols:
            return
        self.points = geo.resample_grid(self.points, rows, cols, self.smooth)
        self.rows, self.cols = rows, cols
        self.touch()

    def reset_mesh(self):
        """Keep the 4 corners, flatten all inner points back onto the perspective plane."""
        c = geo.corners_of_grid(self.points)
        h = geo.homography(geo.UNIT_SQUARE, c)
        self.points = geo.apply_h(h, geo.lattice(self.rows, self.cols))
        self.touch()

    def to_dict(self):
        return {
            "name": self.name, "visible": self.visible, "opacity": self.opacity,
            "brightness": self.brightness, "feather": self.feather, "smooth": self.smooth,
            "color_index": self.color_index, "rows": self.rows, "cols": self.cols,
            "points": self.points.tolist(), "source": self.source.tolist(),
            "output": self.output,
        }

    @classmethod
    def from_dict(cls, d):
        s = cls(d.get("name", "Surface"), d.get("color_index", 0))
        for k in ("visible", "opacity", "brightness", "feather", "smooth", "rows", "cols", "output"):
            if k in d:
                setattr(s, k, d[k])
        s.points = np.array(d["points"], dtype=np.float64)
        s.source = np.array(d["source"], dtype=np.float64)
        return s


class Mask:
    def __init__(self, name="Mask"):
        self.name = name
        self.visible = True
        self.points = np.array([[0.4, 0.4], [0.6, 0.4], [0.6, 0.6], [0.4, 0.6]], dtype=np.float64)
        self.output = 0
        self.version = 0

    def touch(self):
        self.version += 1

    def to_dict(self):
        return {"name": self.name, "visible": self.visible, "points": self.points.tolist(),
                "output": self.output}

    @classmethod
    def from_dict(cls, d):
        m = cls(d.get("name", "Mask"))
        m.visible = d.get("visible", True)
        m.output = d.get("output", 0)
        m.points = np.array(d["points"], dtype=np.float64)
        return m


class Output:
    """One output window: fullscreen on a display, or a free window."""

    def __init__(self, name="Output 1"):
        self.name = name
        self.width = 1920
        self.height = 1080
        self.mode = "fullscreen"   # fullscreen | window
        self.monitor = 1
        self.x = 120
        self.y = 120
        self.borderless = True
        self.lock = False
        self.on_top = False

    KEYS = ("name", "width", "height", "mode", "monitor", "x", "y", "borderless", "lock", "on_top")

    def to_dict(self):
        return {k: getattr(self, k) for k in self.KEYS}

    @classmethod
    def from_dict(cls, d):
        o = cls()
        for k in cls.KEYS:
            if k in d:
                setattr(o, k, d[k])
        return o


class Project:
    def __init__(self):
        self.source_kind = "test"      # test | spout | image | video
        self.spout_name = ""
        self.image_path = ""
        self.video_path = ""
        self.video_loop = True
        self.flip_input = False
        self.input_cols = 1            # camera atlas layout of the input
        self.input_rows = 1
        self.outputs = [Output("Output 1")]
        self.active_output = 0
        self.surfaces = [Surface("Surface 1", 0)]
        self.masks = []
        self.path = None

    # the editor works on the active output
    @property
    def output(self):
        self.active_output = max(0, min(self.active_output, len(self.outputs) - 1))
        return self.outputs[self.active_output]

    @property
    def width(self):
        return self.output.width

    @width.setter
    def width(self, v):
        self.output.width = v

    @property
    def height(self):
        return self.output.height

    @height.setter
    def height(self, v):
        self.output.height = v

    def tile_rect(self, index):
        cols, rows = max(1, self.input_cols), max(1, self.input_rows)
        c, r = index % cols, index // cols
        x0, y0, x1, y1 = c / cols, r / rows, (c + 1) / cols, (r + 1) / rows
        return np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=np.float64)

    def remove_output(self, idx):
        if len(self.outputs) <= 1:
            return
        self.outputs.pop(idx)
        for it in self.surfaces + self.masks:
            if it.output == idx:
                it.output = 0
            elif it.output > idx:
                it.output -= 1
            it.touch()
        self.active_output = min(self.active_output, len(self.outputs) - 1)

    def to_dict(self):
        return {
            "version": 2,
            "source_kind": self.source_kind, "spout_name": self.spout_name,
            "image_path": self.image_path, "flip_input": self.flip_input,
            "video_path": self.video_path, "video_loop": self.video_loop,
            "input_cols": self.input_cols, "input_rows": self.input_rows,
            "outputs": [o.to_dict() for o in self.outputs],
            "active_output": self.active_output,
            "surfaces": [s.to_dict() for s in self.surfaces],
            "masks": [m.to_dict() for m in self.masks],
        }

    def load_dict(self, d):
        self.source_kind = d.get("source_kind", "test")
        self.spout_name = d.get("spout_name", "")
        self.image_path = d.get("image_path", "")
        self.video_path = d.get("video_path", "")
        self.video_loop = d.get("video_loop", True)
        self.flip_input = d.get("flip_input", False)
        self.input_cols = int(d.get("input_cols", 1))
        self.input_rows = int(d.get("input_rows", 1))
        if "outputs" in d:
            self.outputs = [Output.from_dict(o) for o in d["outputs"]] or [Output()]
        else:  # version 1 projects had a single output
            o = Output("Output 1")
            o.width = int(d.get("width", 1920))
            o.height = int(d.get("height", 1080))
            o.monitor = d.get("output_monitor", 1)
            self.outputs = [o]
        self.active_output = int(d.get("active_output", 0))
        self.surfaces = [Surface.from_dict(s) for s in d.get("surfaces", [])]
        self.masks = [Mask.from_dict(m) for m in d.get("masks", [])]
        n = len(self.outputs)
        for it in self.surfaces + self.masks:
            it.output = it.output if 0 <= it.output < n else 0
            it.touch()

    def save(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=1)
        self.path = path

    def load(self, path):
        with open(path, "r", encoding="utf-8") as f:
            self.load_dict(json.load(f))
        self.path = path


class History:
    def __init__(self, project, limit=100):
        self.project = project
        self.undo_stack = []
        self.redo_stack = []
        self.limit = limit

    def push(self):
        self.undo_stack.append(copy.deepcopy(self.project.to_dict()))
        if len(self.undo_stack) > self.limit:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def undo(self):
        if not self.undo_stack:
            return False
        self.redo_stack.append(self.project.to_dict())
        self.project.load_dict(self.undo_stack.pop())
        return True

    def redo(self):
        if not self.redo_stack:
            return False
        self.undo_stack.append(self.project.to_dict())
        self.project.load_dict(self.redo_stack.pop())
        return True
