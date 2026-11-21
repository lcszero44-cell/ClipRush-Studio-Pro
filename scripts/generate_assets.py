"""Generate high-resolution ClipRush Studio Pro icon (.png & .ico) with Pillow
and create the Desktop shortcut 'ClipRush Studio Pro.lnk'."""

from __future__ import annotations

import math
import os
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

BASE_DIR = Path(r"D:\ClipRush_Pro_V2")
ASSETS_DIR = BASE_DIR / "assets"
PNG_PATH = ASSETS_DIR / "cliprush.png"
ICO_PATH = ASSETS_DIR / "cliprush.ico"


def generate_logo() -> None:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    size = 512
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    # 1. Rounded dark studio base with subtle purple-to-cyan gradient
    bg = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    px = bg.load()
    for y in range(size):
        for x in range(size):
            t = (x * 0.65 + y * 0.35) / size
            r = int(16 + t * 18)
            g = int(18 + t * 20)
            b = int(34 + t * 38)
            px[x, y] = (r, g, b, 255)

    mask = Image.new("L", (size, size), 0)
    mdraw = ImageDraw.Draw(mask)
    mdraw.rounded_rectangle((20, 20, size - 20, size - 20), radius=112, fill=255)
    img.paste(bg, (0, 0), mask)

    # 2. Ambient neon glows (vibrant purple #6366F1 & neon cyan #22D3EE)
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    gdraw.ellipse((40, 40, 340, 340), fill=(99, 102, 241, 130))
    gdraw.ellipse((190, 190, 470, 470), fill=(34, 211, 238, 120))
    glow = glow.filter(ImageFilter.GaussianBlur(radius=62))
    img = Image.alpha_composite(img, Image.composite(glow, Image.new("RGBA", (size, size), (0, 0, 0, 0)), mask))

    # 3. Crisp neon border ring
    border_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bdraw = ImageDraw.Draw(border_layer)
    bdraw.rounded_rectangle((26, 26, size - 26, size - 26), radius=106, outline=(99, 102, 241, 220), width=10)
    bdraw.rounded_rectangle((36, 36, size - 36, size - 36), radius=98, outline=(34, 211, 238, 120), width=4)
    img = Image.alpha_composite(img, border_layer)

    # 4. Dynamic gamer/studio emblem: stylized play triangle + lightning slash cut
    emblem_glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    egdraw = ImageDraw.Draw(emblem_glow)
    play_poly = [(154, 122), (392, 256), (154, 390)]
    egdraw.polygon(play_poly, fill=(99, 102, 241, 180))
    bolt_poly = [(270, 86), (186, 252), (254, 252), (218, 426), (344, 232), (272, 232)]
    egdraw.polygon(bolt_poly, fill=(34, 211, 238, 210))
    emblem_glow = emblem_glow.filter(ImageFilter.GaussianBlur(radius=22))
    img = Image.alpha_composite(img, emblem_glow)

    emblem = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    edraw = ImageDraw.Draw(emblem)
    # Outer vibrant purple play chassis
    edraw.polygon([(148, 118), (396, 256), (148, 394)], fill=(99, 102, 241, 255))
    # Inner lighter violet highlight
    edraw.polygon([(164, 146), (360, 256), (164, 366)], fill=(129, 140, 248, 255))
    # High-contrast neon cyan lightning bolt cutting through play icon
    edraw.polygon([(274, 86), (184, 256), (254, 256), (214, 426), (346, 230), (274, 230)], fill=(34, 211, 238, 255))
    # Core white-cyan spark highlight inside bolt
    edraw.polygon([(268, 122), (208, 244), (266, 244), (234, 382), (324, 240), (264, 240)], fill=(224, 247, 255, 255))

    # Corner studio rec dot (neon cyan)
    edraw.ellipse((396, 78, 430, 112), fill=(34, 211, 238, 255))
    edraw.ellipse((404, 86, 422, 104), fill=(255, 255, 255, 255))

    img = Image.alpha_composite(img, emblem)

    # Save PNG (512x512) and multi-resolution ICO
    img.save(PNG_PATH, format="PNG")
    img.save(
        ICO_PATH,
        format="ICO",
        sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)],
    )
    print(f"Generated {PNG_PATH} and {ICO_PATH}")


def create_desktop_shortcut() -> None:
    desktop = Path(os.path.expandvars(r"%USERPROFILE%\Desktop"))
    lnk_path = desktop / "ClipRush Studio Pro.lnk"
    target = BASE_DIR / "iniciar.vbs"

    ps_script = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$sc = $ws.CreateShortcut('{str(lnk_path)}'); "
        f"$sc.TargetPath = 'wscript.exe'; "
        f"$sc.Arguments = '\"{str(target)}\"'; "
        f"$sc.WorkingDirectory = '{str(BASE_DIR)}'; "
        f"$sc.IconLocation = '{str(ICO_PATH)},0'; "
        "$sc.Description = 'ClipRush Studio Pro - Editor Viral e Analise de Gameplay PS4/PS5'; "
        "$sc.Save();"
    )
    res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_script], capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Failed creating Desktop shortcut: {res.stderr}")
    print(f"Created Desktop shortcut: {lnk_path}")


if __name__ == "__main__":
    generate_logo()
    create_desktop_shortcut()
