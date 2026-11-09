"""Shared helpers for the footage-sorter scripts (paths, vocab, fonts, JSONL)."""
import json
import os
import shutil
import sys

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UNSURE = "不确定"


def work_paths(work):
    work = os.path.abspath(os.path.expanduser(work))
    return {
        "work": work,
        "manifest": os.path.join(work, "manifest.jsonl"),
        "frames": os.path.join(work, "frames"),
        "strips": os.path.join(work, "strips"),
        "sheets": os.path.join(work, "sheets"),
        "batches": os.path.join(work, "batches.json"),
        "vocab": os.path.join(work, "vocab.json"),
        "brief": os.path.join(work, "brief.md"),
        "cls": os.path.join(work, "cls"),
        "itinerary": os.path.join(work, "itinerary.json"),
    }


def read_jsonl(path):
    out = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def write_jsonl(path, rows):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def load_vocab(work):
    """The work folder's vocab.json wins; otherwise copy the skill's default there first."""
    p = work_paths(work)
    if not os.path.exists(p["vocab"]):
        os.makedirs(p["work"], exist_ok=True)
        shutil.copyfile(os.path.join(SKILL_DIR, "vocab.json"), p["vocab"])
    with open(p["vocab"], encoding="utf-8") as f:
        v = json.load(f)
    by_key = {d["key"]: d for d in v["dimensions"]}
    for d in v["dimensions"]:
        if "values_from" in d:
            d["values"] = by_key[d["values_from"]]["values"]
    return v


def classify_ids(manifest):
    """Clips that get frames and a classification: readable, not a duplicate copy."""
    return [x for x in manifest if not x.get("broken") and not x.get("duplicate_of") and not x.get("extract_error")]


FONT_CANDIDATES = [
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
]


def font(size):
    from PIL import ImageFont
    for f in FONT_CANDIDATES:
        if os.path.exists(f):
            try:
                return ImageFont.truetype(f, size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def need(tool):
    if not shutil.which(tool):
        sys.exit(f"缺少 {tool}：请先安装 ffmpeg（macOS: brew install ffmpeg），再重新运行。")


def find_cli(name):
    """codex / claude: PATH first, then the usual install places a Finder-launched app's PATH misses (nvm, npm -g, …)."""
    import glob as _glob
    hit = shutil.which(name)
    if hit:
        return hit
    home = os.path.expanduser("~")
    pats = [f"{home}/.nvm/versions/node/*/bin/{name}", f"{home}/.npm-global/bin/{name}", f"{home}/.local/bin/{name}",
            f"{home}/.volta/bin/{name}", f"{home}/.bun/bin/{name}", f"{home}/Library/pnpm/{name}", f"/opt/homebrew/bin/{name}",
            f"/usr/local/bin/{name}", f"{home}/.{name}/bin/{name}"]
    if name == "codex":  # the ChatGPT / Codex desktop app ships its own codex CLI (same login as the app)
        pats += ["/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex",
                 f"{home}/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex",
                 "/Applications/Codex.app/Contents/Resources/codex-cli/bin/codex"]
    for p in pats:
        for f in sorted(_glob.glob(p), reverse=True):
            if os.access(f, os.X_OK):
                return f
    return ""


def augment_path():
    """Add the usual tool folders to PATH (apps opened from Finder miss what ~/.zshrc sets up)."""
    home = os.path.expanduser("~")
    extra = ["/opt/homebrew/bin", "/usr/local/bin", f"{home}/.local/bin", f"{home}/.npm-global/bin"]
    for n in ("codex", "claude"):
        f = find_cli(n)
        if f:
            extra.append(os.path.dirname(f))
    cur = os.environ.get("PATH", "").split(":")
    os.environ["PATH"] = ":".join(cur + [d for d in extra if d not in cur and os.path.isdir(d)])
