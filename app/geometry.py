import numpy as np

UNIT_SQUARE = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], dtype=np.float64)


def homography(src, dst):
    """3x3 matrix mapping 4 src points to 4 dst points."""
    a = []
    b = []
    for (x, y), (u, v) in zip(src, dst):
        a.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        a.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        b.append(u)
        b.append(v)
    try:
        h = np.linalg.solve(np.array(a, dtype=np.float64), np.array(b, dtype=np.float64))
    except np.linalg.LinAlgError:
        return np.eye(3)
    return np.append(h, 1.0).reshape(3, 3)


def apply_h(h, pts):
    pts = np.asarray(pts, dtype=np.float64)
    shape = pts.shape
    flat = pts.reshape(-1, 2)
    ones = np.ones((flat.shape[0], 1))
    res = np.hstack([flat, ones]) @ h.T
    w = res[:, 2:3]
    w[np.abs(w) < 1e-12] = 1e-12
    return (res[:, :2] / w).reshape(shape)


def corners_of_grid(points):
    return np.array([points[0, 0], points[0, -1], points[-1, -1], points[-1, 0]])


def lattice(rows, cols):
    v, u = np.meshgrid(np.linspace(0, 1, rows + 1), np.linspace(0, 1, cols + 1), indexing="ij")
    return np.stack([u, v], axis=-1)


def _pad(arr, axis):
    # linear ghost points so the spline does not flatten at the edges
    first = np.take(arr, [0], axis=axis)
    second = np.take(arr, [1], axis=axis) if arr.shape[axis] > 1 else first
    last = np.take(arr, [-1], axis=axis)
    before_last = np.take(arr, [-2], axis=axis) if arr.shape[axis] > 1 else last
    return np.concatenate([2 * first - second, arr, 2 * last - before_last], axis=axis)


def _catmull_1d(p, t):
    """p: control values along axis 0 (already padded), t: params in [0,1]."""
    n = p.shape[0] - 3  # number of segments
    x = np.clip(t, 0, 1) * n
    i = np.minimum(np.floor(x).astype(int), n - 1)
    f = x - i
    f = f.reshape((-1,) + (1,) * (p.ndim - 1))
    p0, p1, p2, p3 = p[i], p[i + 1], p[i + 2], p[i + 3]
    return 0.5 * ((2 * p1) + (-p0 + p2) * f + (2 * p0 - 5 * p1 + 4 * p2 - p3) * f ** 2
                  + (-p0 + 3 * p1 - 3 * p2 + p3) * f ** 3)


def _linear_1d(p, t):
    n = p.shape[0] - 1
    x = np.clip(t, 0, 1) * n
    i = np.minimum(np.floor(x).astype(int), max(n - 1, 0))
    f = (x - i).reshape((-1,) + (1,) * (p.ndim - 1))
    return p[i] * (1 - f) + p[np.minimum(i + 1, n)] * f


def eval_grid(points, us, vs, smooth=True):
    """Evaluate a warp grid at parameter vectors us (cols) and vs (rows).
    The 4 outer corners define a perspective-correct base (homography);
    the inner control points add mesh offsets on top of it.
    Returns array (len(vs), len(us), 2)."""
    points = np.asarray(points, dtype=np.float64)
    h = homography(UNIT_SQUARE, corners_of_grid(points))
    hinv = np.linalg.inv(h)
    local = apply_h(hinv, points)  # (r+1, c+1, 2) in unit space
    if smooth and (points.shape[0] > 2 or points.shape[1] > 2):
        tmp = _catmull_1d(_pad(local, 0), np.asarray(vs))      # (len(vs), c+1, 2)
        tmp = np.swapaxes(tmp, 0, 1)                            # (c+1, len(vs), 2)
        res = _catmull_1d(_pad(tmp, 0), np.asarray(us))        # (len(us), len(vs), 2)
    else:
        tmp = _linear_1d(local, np.asarray(vs))
        tmp = np.swapaxes(tmp, 0, 1)
        res = _linear_1d(tmp, np.asarray(us))
    res = np.swapaxes(res, 0, 1)
    return apply_h(h, res)


def resample_grid(points, rows, cols, smooth=True):
    us = np.linspace(0, 1, cols + 1)
    vs = np.linspace(0, 1, rows + 1)
    return eval_grid(points, us, vs, smooth)


def boundary(points, samples=24, smooth=True):
    t = np.linspace(0, 1, samples + 1)
    top = eval_grid(points, t, [0.0], smooth)[0]
    right = eval_grid(points, [1.0], t, smooth)[:, 0]
    bottom = eval_grid(points, t[::-1], [1.0], smooth)[0]
    left = eval_grid(points, [0.0], t[::-1], smooth)[:, 0]
    return np.concatenate([top[:-1], right[:-1], bottom[:-1], left[:-1]])


def point_in_poly(p, poly):
    x, y = p
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            xc = (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi
            if x < xc:
                inside = not inside
        j = i
    return inside


def _area(poly):
    x = poly[:, 0]
    y = poly[:, 1]
    return 0.5 * np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y)


def triangulate(poly):
    """Ear clipping for simple polygons. Returns list of index triples."""
    poly = np.asarray(poly, dtype=np.float64)
    n = len(poly)
    if n < 3:
        return []
    idx = list(range(n))
    if _area(poly) < 0:
        idx.reverse()
    tris = []
    guard = 0
    while len(idx) > 3 and guard < n * n:
        guard += 1
        found = False
        for k in range(len(idx)):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            a, b, c = poly[i0], poly[i1], poly[i2]
            cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
            if cross <= 1e-12:
                continue
            ear = True
            for j in idx:
                if j in (i0, i1, i2):
                    continue
                if _in_tri(poly[j], a, b, c):
                    ear = False
                    break
            if ear:
                tris.append((i0, i1, i2))
                idx.pop(k)
                found = True
                break
        if not found:
            break
    if len(idx) == 3:
        tris.append(tuple(idx))
    return tris


def _in_tri(p, a, b, c):
    def s(p1, p2, p3):
        return (p1[0] - p3[0]) * (p2[1] - p3[1]) - (p2[0] - p3[0]) * (p1[1] - p3[1])
    d1, d2, d3 = s(p, a, b), s(p, b, c), s(p, c, a)
    neg = d1 < 0 or d2 < 0 or d3 < 0
    pos = d1 > 0 or d2 > 0 or d3 > 0
    return not (neg and pos)


def dist_to_segment(p, a, b):
    p, a, b = np.asarray(p), np.asarray(a), np.asarray(b)
    ab = b - a
    t = np.clip(np.dot(p - a, ab) / (np.dot(ab, ab) + 1e-12), 0, 1)
    return np.linalg.norm(p - (a + t * ab))
