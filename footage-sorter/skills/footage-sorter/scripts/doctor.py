"""Check what footage-sorter needs: Python 3.9+, ffmpeg + ffprobe, Pillow. Prints how to install what is missing.
usage: doctor.py          exit code 0 = ready"""
import json
import shutil
import sys

import os
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import augment_path, find_cli  # noqa: E402

augment_path()

if "--json" in sys.argv:  # for the desktop app
    try:
        import PIL  # noqa: F401
        pil = True
    except ImportError:
        pil = False
    print(json.dumps({"python": sys.version.split()[0], "python_ok": sys.version_info >= (3, 9), "ffmpeg": shutil.which("ffmpeg") or "",
                      "ffprobe": shutil.which("ffprobe") or "", "pillow": pil, "codex": find_cli("codex"),
                      "claude": find_cli("claude")}))
    sys.exit(0)

ok = True
print(f"Python {sys.version.split()[0]}：", "OK" if sys.version_info >= (3, 9) else "太旧，需要 3.9 以上")
ok &= sys.version_info >= (3, 9)
for tool in ("ffmpeg", "ffprobe"):
    found = shutil.which(tool)
    print(f"{tool}：", found or "没装")
    ok &= bool(found)
try:
    import PIL
    print("Pillow：", PIL.__version__)
except ImportError:
    print("Pillow：没装")
    ok = False
if ok:
    print("\n都齐了，可以用。")
    sys.exit(0)
print("""
缺东西。macOS 上在「终端」里依次运行（没有 Homebrew 的先装 Homebrew，网址：
  https://brew.sh
）：
  brew install ffmpeg
  python3 -m pip install --user pillow
如果第二行报 externally-managed-environment，改用：
  brew install pillow
Windows：ffmpeg 从 https://www.gyan.dev/ffmpeg/builds/ 下载并加入 PATH；然后 py -m pip install pillow""")
sys.exit(1)
