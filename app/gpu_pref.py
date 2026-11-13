"""Ask Windows to run ProjMap on the high-performance GPU.

This writes the same per-app setting as Settings > System > Display > Graphics.
Spout only shares frames between apps on the same GPU, so ProjMap and Blender
both need to be on the discrete GPU on hybrid laptops."""
import os
import subprocess
import sys

KEY = r"Software\Microsoft\DirectX\UserGpuPreferences"
HIGH = "GpuPreference=2;"


def set_high_performance(exe_path):
    """Returns True if the setting was changed (takes effect on next launch)."""
    if os.name != "nt" or not exe_path:
        return False
    try:
        import winreg
        exe_path = os.path.abspath(exe_path)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, KEY) as k:
            try:
                current, _ = winreg.QueryValueEx(k, exe_path)
            except FileNotFoundError:
                current = None
            if current == HIGH:
                return False
            winreg.SetValueEx(k, exe_path, 0, winreg.REG_SZ, HIGH)
            return True
    except Exception as e:
        print("Could not set GPU preference:", e)
        return False


def ensure_and_relaunch():
    """Call before any OpenGL context exists. Relaunches once if the preference was just set."""
    if os.name != "nt" or os.environ.get("PROJMAP_RELAUNCHED"):
        return
    if not set_high_performance(sys.executable):
        return
    env = dict(os.environ, PROJMAP_RELAUNCHED="1")
    args = [sys.executable] + (sys.argv[1:] if getattr(sys, "frozen", False) else sys.argv)
    try:
        subprocess.Popen(args, env=env, close_fds=True)
        sys.exit(0)
    except Exception as e:
        print("Relaunch failed, continuing:", e)
