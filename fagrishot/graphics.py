"""Player graphics preferences written to the same INI that the in-game menu uses."""
import os
from pathlib import Path
import re
import tempfile

from .storage import build_lock


OPTIONS = {
    "output": ("console", "720p", "1080p", "1440p", "2160p", "window", "2560x1080", "3440x1440", "5120x1440"),
    "scale": ("0", "0.33", "0.5", "0.59", "0.67", "0.75", "0.85", "1.0"),
    "fps": ("30", "60", "120"),
    "upscaler": ("taau", "auto", "bilinear", "dlss", "fsr3", "xess"),
    "reflections": ("full", "reduced", "off"),
    "ray_reflections": ("off", "on"),
}
STREAMLINE = ("sl.interposer.dll", "sl.common.dll", "sl.dlss.dll", "nvngx_dlss.dll")
SECTION = re.compile(r"^\s*\[([^\]]+)]")
SETTING = re.compile(r'''^(?P<prefix>\s*(?P<key>[^=;#\s][^=]*?)\s*=\s*)(?P<value>"[^"]*"|.*?)(?P<suffix>\s+[;#].*|\s*)$''')


class Ini:
    """Keep sections, comments, casing and unknown keys; like the runtime, the last key wins."""

    def __init__(self, path):
        self.path = path
        try:
            text = path.read_bytes().decode("utf-8-sig")
        except FileNotFoundError:
            text = ""
        self.newline = "\r\n" if "\r\n" in text else "\n"
        self.lines = text.splitlines()

    def _positions(self, section, key):
        current = None
        last_section = last_key = None
        for index, line in enumerate(self.lines):
            header = SECTION.match(line)
            if header:
                current = header[1].strip().lower()
            if current == section:
                last_section = index
                setting = SETTING.match(line)
                if setting and setting["key"].strip().lower() == key:
                    last_key = index, setting
        return last_section, last_key

    def get(self, section, key, default=""):
        _, found = self._positions(section, key)
        return found[1]["value"].strip().strip('"') if found else default

    def set(self, section, key, value):
        end, found = self._positions(section, key)
        if found:
            index, match = found
            self.lines[index] = match["prefix"] + value + match["suffix"]
        elif end is None:
            if self.lines and self.lines[-1]:
                self.lines.append("")
            self.lines.extend([f"[{section}]", f"{key} = {value}"])
        else:
            self.lines.insert(end + 1, f"{key} = {value}")

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = (self.newline.join(self.lines) + self.newline).encode("utf-8")
        with tempfile.NamedTemporaryFile(dir=self.path.parent, delete=False) as stream:
            temporary = Path(stream.name)
        try:
            temporary.write_bytes(data)
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)


def upscaler_files(game_ini, game_dir, root):
    settings = game_ini.path.parent
    controls = Ini(settings / game_ini.get("play", "controls", "controls.ini"))
    def folders(folder, option):
        custom = controls.get("upscale", option + "_dll")
        if custom:
            location = settings / custom
            yield location if location.is_dir() else location.parent
            if option == "dlss":
                return
        for base in (game_dir, root, settings):
            yield base / "upscalers" / folder
        if option == "fsr":
            yield game_dir / "upscalers/fsr3"
        yield game_dir

    # Streamline stops at the first interposer, even if that folder has an incomplete set.
    sl = next((p for p in folders("streamline", "dlss") if (p / STREAMLINE[0]).is_file()), None)
    return {
        "dlss": bool(sl and all((sl / name).is_file() for name in STREAMLINE)),
        "fsr3": any((p / name).is_file() for p in folders("fsr", "fsr")
                    for name in ("amd_fidelityfx_upscaler_vk.dll", "amd_fidelityfx_vk.dll")),
        "xess": any((p / "libxess.dll").is_file() for p in folders("xess", "xess")),
    }


def read(ini, game_dir, root):
    def enabled(key):
        return ini.get("fefw", key).lower() in {"1", "on", "true", "yes"}
    output = ini.get("fefw", "output", "console").lower()
    scale = ini.get("fefw", "render_ratio", "0")
    try:
        scale = next((v for v in OPTIONS["scale"] if float(v) == float(scale)), scale)
    except ValueError:
        pass
    return {
        "output": "2160p" if output == "4k" else output,
        "scale": scale, "fixed_render": ini.get("fefw", "render"),
        "fps": "120" if enabled("fps120") else "60" if enabled("fps60") else "30",
        "upscaler": ini.get("dlss", "upscaler", "taau").lower(),
        "reflections": ini.get("fefw", "reflections", "full").lower(),
        "ray_reflections": "on" if ini.get("experimental", "rt_visible_reflections").lower() in {"true", "1", "yes", "on"} else "off",
        "installed": upscaler_files(ini, game_dir, root),
    }


def preferences(settings, game_dir, root, changes):
    path = settings / "fefw.ini"
    if not changes:
        return read(Ini(path), game_dir, root)
    for key, value in changes.items():
        if key not in OPTIONS or value not in OPTIONS[key]:
            raise ValueError(f"Unsupported graphics setting: {key}={value}.")
    with build_lock(settings / "save.lock"):
        ini = Ini(path)
        if "upscaler" in changes:
            chosen = changes["upscaler"]
            if not upscaler_files(ini, game_dir, root).get(chosen, True):
                raise ValueError(f"The required {chosen.upper()} files are missing. Choose an installed upscaler.")
        for key, value in changes.items():
            if key == "fps":
                ini.set("fefw", "fps60", "1" if value == "60" else "0")
                ini.set("fefw", "fps120", "1" if value == "120" else "0")
            elif key == "scale":
                if ini.get("fefw", "render"):
                    ini.set("fefw", "render", "")
                ini.set("fefw", "render_ratio", value)
            elif key == "upscaler":
                ini.set("dlss", "upscaler", value)
            elif key == "ray_reflections":
                ini.set("experimental", "rt_visible_reflections", "true" if value == "on" else "false")
            else:
                ini.set("fefw", key, value)
        ini.save()
        return read(ini, game_dir, root)
