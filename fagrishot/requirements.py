"""Quick checks for the extracted Windows player. The build performs full integrity checks."""
import ctypes
import sys


PLAYER_TOOLS = {
    "Bundled Python": "tools/python/python.exe",
    "Game builder": "tools/build/fefw_build.py",
    "Bundled Zig compiler": "tools/build/zig/zig.exe",
    "Runtime DLL": "fefw_rt.dll",
    "Shader translator": "tools/spirv_harness/spirv_harness.exe",
    "Shader validator": "tools/spirv-val/spirv-val.exe",
}


def check_tools(root):
    missing = [rel for rel in PLAYER_TOOLS.values() if not (root / rel).is_file()]
    if missing:
        raise ValueError("Missing player files: " + ", ".join(missing) +
                         ". Extract the entire FagriShot Windows ZIP again, including its tools folder.")


def vulkan_loader():
    """Check the system loader without starting a game or creating a graphics device."""
    if sys.platform != "win32":
        return None, "The downloadable player targets Windows x64."
    try:
        loader = ctypes.WinDLL("vulkan-1.dll", winmode=0x800)  # LOAD_LIBRARY_SEARCH_SYSTEM32
        version = ctypes.c_uint32()
        query = loader.vkEnumerateInstanceVersion
        query.argtypes = [ctypes.POINTER(ctypes.c_uint32)]
        query.restype = ctypes.c_int32
        if query(ctypes.byref(version)) == 0:
            major = (version.value >> 22) & 0x7f
            minor = (version.value >> 12) & 0x3ff
            if (major, minor) >= (1, 3):
                return True, f"Vulkan loader {major}.{minor} present. Play also requires a Vulkan 1.3-capable GPU."
    except (AttributeError, OSError):
        pass
    return False, ("Vulkan 1.3 is required for Play. Install the graphics driver for your laptop's GPU "
                   "from the laptop or GPU manufacturer, then reopen FagriShot.")
