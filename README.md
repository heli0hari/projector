# ProjMap

A small projection-mapping tool for Windows. It receives a live Spout feed from Blender or After Effects, lets you warp it onto surfaces, and sends the result fullscreen to your projector.

## Run
1. Install **Python 3.13** (3.11–3.13 work; 3.14 is not supported by Spout yet) from python.org, and tick *Add to PATH*.
2. Double-click `run.bat`. The first run sets everything up.
3. Optional: run `build_exe.bat` to get a standalone `dist\ProjMap\ProjMap.exe`.

## Blender (live camera)
1. *Edit > Preferences > Add-ons > Install from Disk*, then pick `blender/projmap_spout.py` and enable it.
2. In the add-on preferences, click **Install SpoutGL** (one time only), then restart Blender.
3. *Preferences > System > Backend* must be **OpenGL**.
4. In the 3D View, press N, open the **ProjMap** tab and click **Start streaming**. It streams the active scene camera, using the viewport's shading mode (Material Preview / Rendered with EEVEE).

## After Effects (live comp)
Rather than writing a custom AE plugin, use the free, maintained route:
1. Install **NDI Tools** (it includes *NDI for Adobe CC*), then in AE go to *Preferences > Video Preview*, enable **Mercury Transmit** and tick **NDI Output**.
2. Run **NDI to Spout** (Spout's free NDI bridge) and select the AE source.
3. In ProjMap, set Input to **Spout** and click **List** to pick that sender.

Alternative: the open-source `Spout-AfterEffects` plugin sends Spout directly, but it's older and may not load in current AE versions.

## Workflow
1. Set **Input**: Spout, a test pattern, or an image.
2. Pick the projector monitor, click **Match**, then **Open projector output** (F).
3. **Output tab**: drag the 4 corners onto the real object. Corner pin is perspective-correct.
4. For curved surfaces, raise **Grid** (e.g. 4x3) and drag the inner points. Moving a corner carries the whole mesh with it; hold Alt to move only the corner.
5. **Input tab**: choose which part of the feed each surface shows. This is how one Blender render can feed several surfaces.
6. **Masks** black out areas. Double-click an edge to add a point; right-click a point to delete it.
7. **Calibration pattern** (C) shows a checker on each surface, so you can align on the wall without content.

Projects are saved as JSON. The last session is autosaved and restored when you launch.

| Key | Action |
|---|---|
| Drag point / inside surface | Move point / whole surface |
| Shift (while dragging) | Fine (¼ speed) |
| Arrows / Shift+Arrows | Nudge selected point 1 / 10 px. Also works when the projector window is focused |
| Tab / Shift+Tab | Next / previous point |
| Wheel, Middle-drag or Space+drag | Zoom, pan |
| Home | Fit view |
| F, G, C | Projector output, projector guides, calibration pattern |
| Ctrl+Z / Ctrl+Y, Ctrl+S, Ctrl+D, Del | Undo / redo, save, duplicate, delete |

## Troubleshooting
- **Image upside down**: toggle *Flip vertically* in Blender, or *Flip input vertically* in ProjMap.
- **No Spout senders**: make sure the sender is running and both apps use the same GPU. On laptops, set Blender and Python to *High performance* in Windows Graphics settings.
