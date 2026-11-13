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
        }

    @classmethod
    def from_dict(cls, d):
        s = cls(d.get("name", "Surface"), d.get("color_index", 0))
        for k in ("visible", "opacity", "brightness", "feather", "smooth", "rows", "cols"):
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
        self.version = 0

    def touch(self):
        self.version += 1

    def to_dict(self):
        return {"name": self.name, "visible": self.visible, "points": self.points.tolist()}

    @classmethod
    def from_dict(cls, d):
        m = cls(d.get("name", "Mask"))
        m.visible = d.get("visible", True)
        m.points = np.array(d["points"], dtype=np.float64)
        return m


class Project:
    def __init__(self):
        self.width = 1920
        self.height = 1080
        self.source_kind = "test"      # test | spout | image
        self.spout_name = ""
        self.image_path = ""
        self.flip_input = False
        self.output_monitor = 1
        self.surfaces = [Surface("Surface 1", 0)]
        self.masks = []
        self.path = None

    def to_dict(self):
        return {
            "version": 1, "width": self.width, "height": self.height,
            "source_kind": self.source_kind, "spout_name": self.spout_name,
            "image_path": self.image_path, "flip_input": self.flip_input,
            "output_monitor": self.output_monitor,
            "surfaces": [s.to_dict() for s in self.surfaces],
            "masks": [m.to_dict() for m in self.masks],
        }

    def load_dict(self, d):
        self.width = int(d.get("width", 1920))
        self.height = int(d.get("height", 1080))
        self.source_kind = d.get("source_kind", "test")
        self.spout_name = d.get("spout_name", "")
        self.image_path = d.get("image_path", "")
        self.flip_input = d.get("flip_input", False)
        self.output_monitor = d.get("output_monitor", 1)
        self.surfaces = [Surface.from_dict(s) for s in d.get("surfaces", [])]
        self.masks = [Mask.from_dict(m) for m in d.get("masks", [])]
        for s in self.surfaces:
            s.touch()
        for m in self.masks:
            m.touch()

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
