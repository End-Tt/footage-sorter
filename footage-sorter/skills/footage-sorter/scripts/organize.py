"""Step 7 (optional): put the footage into a new folder, sorted by category. Originals are never moved or changed.

usage: organize.py --work WORK --dest DEST [--mode copy|link]

DEST/
  按场景/<场景>/★★★ C0007 一句话描述 (原文件名).mov   every usable clip once, in its main scene folder
  ★★★ 精选/…                                          all 3-star clips again
  ✕ 废片/…                                            0-star clips (not in the scene folders)
  未分类/…                                            clips the AI could not classify
mode copy: APFS clone when source and DEST are on the same disk (takes almost no space), real copy otherwise
           (checks free space first). mode link: Finder-visible symlinks (no space; originals must stay put).
Re-running skips files that already exist. Progress: one JSON line per event."""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import UNSURE, classify_ids, read_jsonl, work_paths  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--work", required=True)
ap.add_argument("--dest", required=True)
ap.add_argument("--mode", choices=["copy", "link"], default="copy")
args = ap.parse_args()
P = work_paths(args.work)
DEST = os.path.abspath(os.path.expanduser(args.dest))
os.makedirs(DEST, exist_ok=True)


def emit(**kw):
    print(json.dumps(dict(stage="organize", **kw), ensure_ascii=False), flush=True)


def clean(s, n=40):
    s = re.sub(r'[\\/:*?"<>|\n\r\t]+', " ", str(s or "")).strip().strip(".")
    return s[:n].strip()


man = read_jsonl(P["manifest"])
cls = {}
for p in sorted(glob.glob(os.path.join(P["cls"], "*.jsonl"))):
    for r in read_jsonl(p):
        cls[r["id"]] = r

STARS = {3: "★★★", 2: "★★", 1: "★", 0: "✕"}
plan = []  # (src, [dest dirs], filename)
for x in classify_ids(man):
    c = cls.get(x["id"])
    src = os.path.realpath(x["path"])
    stem, ext = os.path.splitext(os.path.basename(x["path"]))
    if not c:
        plan.append((src, ["未分类"], f"{x['id']} ({clean(stem, 60)}){ext}"))
        continue
    rating = c.get("rating")
    name = f"{STARS.get(rating, '')} {x['id']} {clean(c.get('desc'))} ({clean(stem, 60)}){ext}".strip()
    if rating == 0:
        plan.append((src, ["✕ 废片"], name))
        continue
    scene = c.get("scene") or ""
    dirs = [os.path.join("按场景", clean(scene, 30) if scene and scene != UNSURE else "场景不确定")]
    if rating == 3:
        dirs.append("★★★ 精选")
    plan.append((src, dirs, name))

jobs = [(src, os.path.join(DEST, d, name)) for src, dirs, name in plan for d in dirs]

# files this tool created last time but no longer wants (labels changed after re-classifying): archive, never delete
oj = os.path.join(P["work"], "organize.json")
prev = json.load(open(oj, encoding="utf-8")) if os.path.exists(oj) else {}
want = {os.path.relpath(d, DEST) for _, d in jobs}
stale = [f for f in prev.get("files", []) if prev.get("dest") == DEST and f not in want and os.path.lexists(os.path.join(DEST, f))]
if stale:
    arch = os.path.join(DEST, "_旧文件", time.strftime("%Y%m%d-%H%M%S"))
    for f in stale:
        os.makedirs(os.path.dirname(os.path.join(arch, f)), exist_ok=True)
        os.replace(os.path.join(DEST, f), os.path.join(arch, f))
    emit(message=f"标签变了的 {len(stale)} 个旧文件挪到了 _旧文件/（没删）")

todo = [(s, d) for s, d in jobs if not os.path.lexists(d)]
if args.mode == "copy" and todo:
    # APFS clone works only on the same Mac-formatted (APFS) disk; probe once instead of trusting st_dev
    s0, d0 = todo[0]
    os.makedirs(os.path.dirname(d0), exist_ok=True)
    cloned = subprocess.run(["/bin/cp", "-c", s0, d0], capture_output=True).returncode == 0
    dev = os.stat(DEST).st_dev
    rest = todo[1:] if cloned else todo
    need = sum(os.path.getsize(s) for s, _ in rest if not cloned or os.stat(s).st_dev != dev)
    free = shutil.disk_usage(DEST).free
    if need > free * 0.95:
        if cloned:
            os.remove(d0)
        emit(error=f"目标磁盘空间不够：需要约 {need / 1e9:.1f} GB，只剩 {free / 1e9:.1f} GB。可以把结果放到素材所在的 Mac 格式（APFS）硬盘上（那样几乎不占空间），或者改选「替身」。")
        sys.exit(2)
    if need:
        emit(message=f"这块硬盘不能用克隆（不是同一块 Mac 格式的硬盘），要真复制约 {need / 1e9:.1f} GB")
    if cloned:
        todo = todo[1:]

emit(done=0, total=len(jobs), message=f"整理进新文件夹（{'复制' if args.mode == 'copy' else '替身'}）：{len(plan)} 条素材，{len(jobs)} 个文件")
failed = []
for k, (src, dst) in enumerate(todo, 1):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        if args.mode == "link":
            os.symlink(src, dst)
        else:
            r = subprocess.run(["/bin/cp", "-c", src, dst], capture_output=True)  # APFS clone: no extra space
            if r.returncode != 0:
                shutil.copy2(src, dst)
    except Exception as e:  # keep going; report at the end
        failed.append(f"{os.path.basename(dst)}: {e}")
    if k % 10 == 0 or k == len(todo):
        emit(done=len(jobs) - len(todo) + k, total=len(jobs))
counts = {}
for _, dirs, _ in plan:
    counts[dirs[0]] = counts.get(dirs[0], 0) + 1
made = sorted(set(prev.get("files", [])) - set(stale) | {r for r in want if os.path.lexists(os.path.join(DEST, r))}) \
    if prev.get("dest") == DEST else sorted(r for r in want if os.path.lexists(os.path.join(DEST, r)))
json.dump({"dest": DEST, "mode": args.mode, "folders": counts, "files": made}, open(oj, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
emit(done=len(jobs), total=len(jobs), dest=DEST, failed=failed[:20],
     message=f"整理好了：{len(counts)} 个文件夹" + (f"，{len(failed)} 个文件没复制成功" if failed else ""))
sys.exit(1 if failed else 0)
