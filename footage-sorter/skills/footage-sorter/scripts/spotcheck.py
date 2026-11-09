"""Step 5: spot-check image — N random clips per batch, each strip captioned with its labels -> WORK/spotcheck.jpg.
Look at it: labels that clearly don't match the frames mean that batch should be redone.

usage: spotcheck.py --work WORK [--n 2] [--seed 7]"""
import argparse
import glob
import json
import os
import random
import sys

from PIL import Image, ImageDraw

sys.dont_write_bytecode = True  # no __pycache__ inside the installed skill folder
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import font, read_jsonl, work_paths  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--work", required=True)
ap.add_argument("--n", type=int, default=2)
ap.add_argument("--seed", type=int, default=7)
args = ap.parse_args()
P = work_paths(args.work)
cls = {}
for p in sorted(glob.glob(os.path.join(P["cls"], "*.jsonl"))):
    for r in read_jsonl(p):
        cls[r["id"]] = r
batches = json.load(open(P["batches"], encoding="utf-8"))
random.seed(args.seed)
pick = []
for b in batches:
    have = [i for i in b["ids"] if i in cls]
    pick += [(b["batch"], i) for i in random.sample(have, min(args.n, len(have)))]
F = font(17)
sh = Image.new("RGB", (6 * 216 + 20, max(1, len(pick)) * 152), (15, 15, 15))
d = ImageDraw.Draw(sh)
for k, (b, i) in enumerate(pick):
    c, y = cls[i], k * 152
    multi = lambda v: "、".join(v) if isinstance(v, list) else (v or "")
    label = f"第{b}批 {i} ★{c.get('rating')} {c.get('scene')}/{c.get('light')}/{c.get('size')}/{c.get('move')} 用途:{c.get('role', '')} 风格:{multi(c.get('style'))} | {c.get('desc')}"
    d.text((6, y + 3), label[:110], fill="yellow", font=F)
    sh.paste(Image.open(os.path.join(P["strips"], i + ".jpg")), (10, y + 28))
out = os.path.join(P["work"], "spotcheck.jpg")
sh.save(out, quality=80)
print(out, [i for _, i in pick])
