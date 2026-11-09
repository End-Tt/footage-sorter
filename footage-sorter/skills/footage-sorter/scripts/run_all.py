"""Engine of the desktop app: scan → extract → brief → classify (Codex / Claude CLI) → check → build.
Prints one JSON line per progress event on stdout:
  {"stage": "scan|extract|classify|check|build|done|error", "message": "...", "done": n, "total": m, ...}

usage: run_all.py --src FOLDER [FOLDER ...] --out OUT [--event NAME] [--title T] [--backend codex|claude]
                  [--effort medium] [--exclude TEXT ...]
Work files go to OUT/_工作文件; re-running continues where it stopped."""
import argparse
import json
import os
import re
import signal
import subprocess
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
sys.path.insert(0, HERE)
from common import augment_path  # noqa: E402

augment_path()  # child steps (ffmpeg, ffprobe, codex, claude) inherit it

ap = argparse.ArgumentParser()
ap.add_argument("--src", nargs="+", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--event", default="")
ap.add_argument("--title", default="")
ap.add_argument("--backend", choices=["codex", "claude"], default="codex")
ap.add_argument("--effort", default="medium")
ap.add_argument("--exclude", action="append", default=[])
ap.add_argument("--organize", choices=["copy", "link", "none"], default="copy",
                help="also put the footage into OUT/整理好的素材, sorted by scene (copy = APFS clone when possible)")
ap.add_argument("--no-fcp", action="store_true", help="skip the Final Cut Pro file")
args = ap.parse_args()
OUT = os.path.abspath(os.path.expanduser(args.out))
WORK = os.path.join(OUT, "_工作文件")
os.makedirs(WORK, exist_ok=True)
name = os.path.basename(os.path.normpath(args.src[0]))
EVENT = args.event or name
TITLE = args.title or f"{name} 素材目录"

try:  # own process group, so the app can stop everything (including codex/claude) with one signal
    os.setpgrp()
except OSError:
    pass


def emit(**kw):
    print(json.dumps(kw, ensure_ascii=False), flush=True)


def fail(stage, msg):
    emit(stage="error", failed=stage, message=msg)
    sys.exit(1)


def run(stage, script, *a, progress=None):
    """Run one step; forward its progress; return its stdout text."""
    p = subprocess.Popen([PY, os.path.join(HERE, script), *a], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    out_lines, err_tail = [], []

    def pump_err():
        for line in p.stderr:
            err_tail.append(line)
            del err_tail[:-30]
            if progress:
                m = re.search(progress, line)
                if m:
                    emit(stage=stage, done=int(m.group(1)), total=int(m.group(2)))
    import threading
    t = threading.Thread(target=pump_err, daemon=True)
    t.start()
    for line in p.stdout:
        out_lines.append(line)
        if stage in ("classify", "organize") and line.startswith("{"):
            print(line.rstrip("\n"), flush=True)  # classify.py already speaks our protocol
    p.wait()
    t.join(timeout=2)
    return p.returncode, "".join(out_lines), "".join(err_tail)


def last_json(text):
    i = text.find("{")
    try:
        return json.loads(text[i:]) if i >= 0 else {}
    except json.JSONDecodeError:
        return {}


emit(stage="scan", message="扫描素材文件夹…")
ex = [x for e in args.exclude for x in ("--exclude", e)]
rc, out, err = run("scan", "scan.py", *args.src, "--work", WORK, *ex, progress=r"已读 (\d+)/(\d+)")
if rc:
    fail("scan", err.strip()[-300:] or "扫描失败")
s = last_json(out)
emit(stage="scan", message=f"找到 {s.get('视频总数', '?')} 个视频，可分类 {s.get('可分类', '?')} 条，坏文件 {s.get('坏文件', 0)} 个，"
                           f"重复副本 {s.get('重复副本（已跳过）', 0)} 个，共 {s.get('总时长（分钟）', '?')} 分钟", summary=s)
if not s.get("可分类"):
    fail("scan", "这个文件夹里没有能分类的视频")

emit(stage="extract", message="抽帧、拼联系表…")
rc, out, err = run("extract", "extract.py", "--work", WORK, progress=r"抽帧 (\d+)/(\d+)")
if rc:
    fail("extract", err.strip()[-300:] or "抽帧失败")
e = last_json(out)
emit(stage="extract", message=f"联系表 {e.get('联系表', '?')} 张" + (f"，{len(e['解不出画面'])} 条解不出画面" if e.get("解不出画面") else ""),
     done=e.get("可分类", 0), total=e.get("可分类", 0))

rc, out, err = run("brief", "make_brief.py", "--work", WORK)
if rc:
    fail("brief", err.strip()[-300:])

rc, out, err = run("classify", "classify.py", "--work", WORK, "--backend", args.backend, "--effort", args.effort)
if rc == 2:
    fail("classify", err.strip()[-300:] or f"找不到 {args.backend}")

rc, out, err = run("check", "check.py", "--work", WORK)
c = last_json(out)
emit(stage="check", message=f"已分类 {c.get('已分类', '?')} / {c.get('应分类', '?')} 条" + ("，有问题见日志" if c.get("问题") else ""), report=c)

emit(stage="build", message="生成目录网页、表格和 Final Cut Pro 文件…")
rc, out, err = run("build", "build.py", "--work", WORK, "--out", OUT, "--event", EVENT, "--title", TITLE,
                   *(["--no-fcpxml"] if args.no_fcp else []))
if rc:
    fail("build", err.strip()[-300:] or "生成失败")
b = last_json(out)
organized = ""
if args.organize != "none":
    rc, out, err = run("organize", "organize.py", "--work", WORK, "--dest", os.path.join(OUT, "整理好的素材"), "--mode", args.organize)
    o = [json.loads(l) for l in out.splitlines() if l.startswith("{")]
    if rc == 2:
        fail("organize", (o[-1].get("error") if o else "") or err.strip()[-300:])
    organized = os.path.join(OUT, "整理好的素材")
r = b.get("评分", {})
parts = [f"{lab} {r.get(k, 0)}" for k, lab in (("3", "精选"), ("2", "可用"), ("1", "凑合"), ("0", "废片")) if r.get(k)]
emit(stage="done", message=f"完成：{b.get('条数', '?')} 条（{'、'.join(parts)}）" + (f"，{b['未分类']} 条没分出来" if b.get("未分类") else ""), out=OUT,
     index=os.path.join(OUT, "index.html"), csv=os.path.join(OUT, "素材清单.csv"), fcpxml=b.get("fcpxml", ""), organized=organized,
     unclassified=b.get("未分类", 0))
