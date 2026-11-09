"""Step 2: frames, picture metrics, 6-frame strips and 10-clip contact sheets -> WORK/{frames,strips,sheets}, batches.json.

usage: extract.py --work WORK [--jobs 6] [--batch-size 100] [--only ID ...]

Originals are only read (DJI .LRF proxies are used when present, much faster).
Resumable: clips whose strip already exists are not decoded again.
Metrics written back into manifest.jsonl (0-255 scale, from 180p frames):
  brightness  mean luma          dark_ratio  share of pixels < 30
  sharpness   Laplacian spread   (low = soft; snow / fog / night are naturally low)
  motion      median change between neighbouring sampled frames (camera OR subject motion)
  colorfulness  Hasler–Süsstrunk on the middle frame"""
import argparse
import glob
import json
import math
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageStat

sys.dont_write_bytecode = True  # no __pycache__ inside the installed skill folder
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import classify_ids, font, need, read_jsonl, work_paths, write_jsonl  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--work", required=True)
ap.add_argument("--jobs", type=int, default=6)
ap.add_argument("--batch-size", type=int, default=100, help="clips per classifier batch (multiple of 10)")
ap.add_argument("--only", nargs="*", help="re-extract just these IDs")
args = ap.parse_args()
need("ffmpeg")
P = work_paths(args.work)
for k in ("frames", "strips", "sheets"):
    os.makedirs(P[k], exist_ok=True)
man = read_jsonl(P["manifest"])
if not man:
    sys.exit("先运行 scan.py")

LAPLACE = ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=128)
STRIP_W, STRIP_H, CELL = 213, 120, 216


def ffmpeg_frames(src, outdir, duration):
    """Keyframes first (fast); if too few, sample at a fixed rate (6-60 frames)."""
    pat = os.path.join(outdir, "k_%04d.jpg")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-skip_frame", "nokey", "-i", src, "-vf", "scale=-2:180",
                    "-fps_mode", "vfr", "-q:v", "5", pat], capture_output=True, timeout=1800)
    got = glob.glob(os.path.join(outdir, "*.jpg"))
    if len(got) >= max(6, min(60, duration / 4)):
        return
    for f in got:
        os.remove(f)
    target = max(6, min(60, duration))
    fps = max(0.02, min(10.0, target / max(duration, 0.1)))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-vf", f"fps={fps:.4f},scale=-2:180", "-fps_mode", "vfr",
                    "-q:v", "5", pat], capture_output=True, timeout=1800)


def colorfulness(im):
    px = list(im.resize((64, 36)).getdata())
    rg = [r - g for r, g, b in px]
    yb = [0.5 * (r + g) - b for r, g, b in px]

    def ms(a):
        m = sum(a) / len(a)
        return m, math.sqrt(sum((x - m) ** 2 for x in a) / len(a))
    (mrg, srg), (myb, syb) = ms(rg), ms(yb)
    return math.hypot(srg, syb) + 0.3 * math.hypot(mrg, myb)


def process(x):
    if os.path.exists(os.path.join(P["strips"], x["id"] + ".jpg")) and "brightness" in x and not args.only:
        return x
    d = os.path.join(P["frames"], x["id"])
    os.makedirs(d, exist_ok=True)
    if args.only:
        for f in glob.glob(os.path.join(d, "*.jpg")):
            os.remove(f)
    if not glob.glob(os.path.join(d, "*.jpg")):
        ffmpeg_frames(x.get("proxy") or x["path"], d, x["duration"])
    frames = sorted(glob.glob(os.path.join(d, "*.jpg")))
    if not frames:
        x["extract_error"] = "解不出画面"
        return x
    ims = [Image.open(f).convert("RGB") for f in frames]
    g = [im.convert("L") for im in ims]
    hist = [im.histogram() for im in g]
    x["brightness"] = round(sum(ImageStat.Stat(a).mean[0] for a in g) / len(g), 1)
    x["contrast"] = round(sum(ImageStat.Stat(a).stddev[0] for a in g) / len(g), 1)
    x["dark_ratio"] = round(sum(sum(h[:30]) / max(1, sum(h)) for h in hist) / len(hist), 3)
    sh = sorted(ImageStat.Stat(a.filter(LAPLACE)).stddev[0] ** 2 for a in g)
    x["sharpness"] = round(sh[len(sh) // 2], 1)
    diffs = sorted(ImageStat.Stat(ImageChops.difference(g[i], g[i + 1])).mean[0] for i in range(len(g) - 1) if g[i].size == g[i + 1].size)
    x["motion"] = round(diffs[len(diffs) // 2], 1) if diffs else 0.0
    x["motion_max"] = round(diffs[-1], 1) if diffs else 0.0
    x["colorfulness"] = round(colorfulness(ims[len(ims) // 2]), 1)
    pick = [ims[round(i * (len(ims) - 1) / 5)] for i in range(6)] if len(ims) >= 6 else ims
    strip = Image.new("RGB", (6 * CELL, STRIP_H), (0, 0, 0))
    for j, im in enumerate(pick):
        t = im.copy()
        t.thumbnail((STRIP_W, STRIP_H))
        strip.paste(t, (j * CELL + (STRIP_W - t.width) // 2, (STRIP_H - t.height) // 2))
    strip.save(os.path.join(P["strips"], x["id"] + ".jpg"), quality=78)
    x.pop("extract_error", None)
    return x


def safe(x):
    try:
        return process(x)
    except Exception as e:  # keep going; the clip is flagged and listed at the end
        x["extract_error"] = repr(e)[:200]
        return x


todo = [x for x in man if not x.get("broken") and not x.get("duplicate_of") and (not args.only or x["id"] in args.only)]
by_id = {x["id"]: x for x in man}
with ThreadPoolExecutor(args.jobs) as ex:
    for i, x in enumerate(ex.map(safe, todo)):
        by_id[x["id"]] = x
        if i and i % 25 == 0:
            print(f"  抽帧 {i}/{len(todo)}", file=sys.stderr, flush=True)
man = [by_id[x["id"]] for x in man]
write_jsonl(P["manifest"], man)

# contact sheets: 10 clips per image, left column = ID / date / length / numbers, right = 6 frames
F = font(15)
clips = classify_ids(man)
for f in glob.glob(os.path.join(P["sheets"], "sheet_*.jpg")):
    os.remove(f)
sheets = []
for s in range(0, len(clips), 10):
    grp = clips[s:s + 10]
    sheet = Image.new("RGB", (200 + 6 * CELL, 124 * len(grp)), (20, 20, 20))
    dr = ImageDraw.Draw(sheet)
    for i, x in enumerate(grp):
        y = i * 124
        folder = os.path.dirname(x["rel"]).split(os.sep)[0] if os.sep in x["rel"] else ""
        dr.text((6, y + 4), x["id"], fill="yellow", font=F)
        dr.text((6, y + 24), x.get("date") or "日期未知", fill="white", font=F)
        dr.text((6, y + 44), f"{x['duration']:.0f}s {folder[:8]}", fill="white", font=F)
        dr.text((6, y + 64), f"mot {x.get('motion')} br {x.get('brightness')}", fill=(150, 200, 255), font=F)
        dr.text((6, y + 84), f"sh {x.get('sharpness')}" + (" PORTRAIT" if x.get("portrait") else ""), fill=(150, 200, 255), font=F)
        sheet.paste(Image.open(os.path.join(P["strips"], x["id"] + ".jpg")), (200, y + 2))
    name = f"sheet_{s // 10 + 1:03d}.jpg"
    sheet.save(os.path.join(P["sheets"], name), quality=80)
    sheets.append({"sheet": os.path.join(P["sheets"], name), "ids": [x["id"] for x in grp]})

per = max(1, args.batch_size // 10)
batches = []
for b in range(0, len(sheets), per):
    grp = sheets[b:b + per]
    batches.append({"batch": len(batches) + 1, "sheets": [g["sheet"] for g in grp], "ids": [i for g in grp for i in g["ids"]],
                    "out": os.path.join(P["cls"], f"part_{len(batches) + 1:02d}.jsonl")})
json.dump(batches, open(P["batches"], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
os.makedirs(P["cls"], exist_ok=True)
errs = [x["id"] + " " + x["rel"] for x in man if x.get("extract_error")]
print(json.dumps({"可分类": len(clips), "联系表": len(sheets), "批次": len(batches), "解不出画面": errs}, ensure_ascii=False, indent=1))
