"""Step 1: inventory every video under one or more folders (read-only) -> WORK/manifest.jsonl.

usage: scan.py FOLDER [FOLDER ...] --work WORK [--exclude TEXT ...] [--prefix C]

For each file: duration, size, resolution, fps, rotation (portrait), timecode, camera model, GPS,
shooting date (+ where the date came from), broken / duplicate-copy flags, DJI .LRF proxy.
IDs (C0001 …) follow shooting date; re-running keeps the IDs already handed out."""
import argparse
import collections
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

sys.dont_write_bytecode = True  # no __pycache__ inside the installed skill folder
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import need, read_jsonl, work_paths, write_jsonl  # noqa: E402

VIDEO_EXT = (".mp4", ".mov", ".m4v", ".mts", ".m2ts", ".avi", ".mkv", ".mxf", ".3gp")
# software that rewrites files on export: their container date is the export date, not the shooting date
EDITOR_HINTS = ("davinci", "resolve", "premiere", "final cut", "compressor", "handbrake", "lavf", "capcut", "剪映", "imovie", "adobe")

ap = argparse.ArgumentParser()
ap.add_argument("folders", nargs="+")
ap.add_argument("--work", required=True)
ap.add_argument("--exclude", action="append", default=[], help="skip paths containing this text (repeatable)")
ap.add_argument("--prefix", default="C", help="ID prefix, default C → C0001")
ap.add_argument("--jobs", type=int, default=8)
args = ap.parse_args()
need("ffprobe")
P = work_paths(args.work)
os.makedirs(P["work"], exist_ok=True)

roots = [os.path.abspath(os.path.expanduser(f)) for f in args.folders]
files = []
for root in roots:
    if not os.path.isdir(root):
        sys.exit(f"找不到文件夹：{root}")
    for dp, dn, fn in os.walk(root):
        dn[:] = sorted(d for d in dn if not d.startswith(".") and not d.endswith((".fcpbundle", ".photoslibrary")))
        for f in sorted(fn):
            if f.startswith((".", "._")) or not f.lower().endswith(VIDEO_EXT):
                continue
            p = os.path.join(dp, f)
            if any(e in p for e in args.exclude):
                continue
            files.append((root, p))
print(f"找到视频文件 {len(files)} 个", file=sys.stderr)


def probe(item):
    root, p = item
    rel = os.path.relpath(p, root)
    if len(roots) > 1:
        rel = os.path.join(os.path.basename(root), rel)
    rec = {"path": p, "rel": rel, "size": os.path.getsize(p)}
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", p],
                           capture_output=True, text=True, timeout=180)
        j = json.loads(r.stdout or "{}")
    except Exception as e:  # unreadable file: flagged as broken below
        rec["probe_error"] = repr(e)[:200]
        j = {}
    fmt = j.get("format", {})
    tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
    streams = j.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")), None)
    rec["duration"] = round(float(fmt.get("duration") or 0), 3)
    rec["creation_time"] = tags.get("creation_time")
    rec["apple_date"] = tags.get("com.apple.quicktime.creationdate")
    rec["model"] = tags.get("com.apple.quicktime.model") or tags.get("model") or ""
    rec["make"] = tags.get("com.apple.quicktime.make") or tags.get("make") or ""
    rec["encoder"] = tags.get("encoder") or tags.get("com.apple.quicktime.software") or ""
    rec["gps"] = tags.get("com.apple.quicktime.location.iso6709") or tags.get("location") or tags.get("location-eng") or ""
    tc = tags.get("timecode")
    rec["audio_streams"] = sum(1 for s in streams if s.get("codec_type") == "audio")
    if v:
        st = {k.lower(): val for k, val in (v.get("tags") or {}).items()}
        tc = tc or st.get("timecode")
        rot = st.get("rotate")
        for sd in v.get("side_data_list") or []:
            if "rotation" in sd:
                rot = sd["rotation"]
        rec.update({"codec": v.get("codec_name"), "w": v.get("width") or 0, "h": v.get("height") or 0,
                    "fps": v.get("avg_frame_rate") if v.get("avg_frame_rate") not in (None, "0/0") else v.get("r_frame_rate"),
                    "rotation": int(float(rot)) if rot not in (None, "") else 0})
        if not rec["duration"]:
            rec["duration"] = round(float(v.get("duration") or 0), 3)
    for s in streams:  # QuickTime tmcd track
        tc = tc or ((s.get("tags") or {}).get("timecode"))
    rec["timecode"] = tc or ""
    rec["broken"] = bool(rec["size"] < 20000 or not v or not rec["duration"] or not rec.get("w"))
    rec["portrait"] = bool(v) and ((rec["h"] > rec["w"]) != (abs(rec.get("rotation", 0)) % 180 == 90))
    lrf = os.path.splitext(p)[0] + ".LRF"
    rec["proxy"] = lrf if os.path.exists(lrf) else (os.path.splitext(p)[0] + ".lrf" if os.path.exists(os.path.splitext(p)[0] + ".lrf") else None)
    enc = rec["encoder"]
    if not rec["model"] and enc and not any(h in enc.lower() for h in EDITOR_HINTS) and not enc[:1].isdigit():
        rec["model"] = enc  # DJI writes the camera name here ("DJI OsmoPocket3"); iPhones write an iOS version
    rec["date"], rec["date_source"] = shooting_date(rec)
    return rec


def valid(y, m, d):
    return 2000 <= int(y) <= 2099 and 1 <= int(m) <= 12 and 1 <= int(d) <= 31


def shooting_date(r):
    name = os.path.basename(r["path"])
    # local-time stamps in file names: DJI_20251206201749, VID_20250101_123456, PXL_20250101_…, 20250101_123456, 2025-01-01 …
    for pat in (r"(?<!\d)(20\d{2})(\d{2})(\d{2})[_\-T]?(\d{2})(\d{2})(\d{2})", r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)",
                r"(?<!\d)(20\d{2})[-_.](\d{2})[-_.](\d{2})"):
        m = re.search(pat, name)
        if m and valid(*m.groups()[:3]):
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}", "文件名"
    if r.get("apple_date"):  # phone: local time with offset
        return r["apple_date"][:10], "手机拍摄时间"
    enc = (r.get("encoder") or "").lower()
    if r.get("creation_time"):
        if any(h in enc for h in EDITOR_HINTS):
            return "", "被剪辑软件重新导出过，文件里的日期是导出日，不可信"
        return r["creation_time"][:10], "文件拍摄时间（UTC，跨午夜可能差一天）"
    return "", "未知"


with ThreadPoolExecutor(args.jobs) as ex:
    recs = []
    for i, r in enumerate(ex.map(probe, files)):
        recs.append(r)
        if i and i % 100 == 0:
            print(f"  已读 {i}/{len(files)}", file=sys.stderr)

# exact copies (same name and size in another folder): keep the first, mark the rest
seen = {}
for r in sorted(recs, key=lambda r: r["rel"]):
    k = (os.path.basename(r["path"]).lower(), r["size"])
    if k in seen and not r["broken"]:
        r["duplicate_of"] = seen[k]
    else:
        seen.setdefault(k, r["path"])

prev = {x["path"]: x for x in read_jsonl(P["manifest"]) if "id" in x}
old = {p: x["id"] for p, x in prev.items()}
METRICS = ("brightness", "contrast", "dark_ratio", "sharpness", "motion", "motion_max", "colorfulness")
for r in recs:  # keep picture metrics from an earlier extract.py run (same file, same size)
    x = prev.get(r["path"])
    if x and x.get("size") == r["size"]:
        r.update({k: x[k] for k in METRICS if k in x})
recs.sort(key=lambda r: (r["broken"], bool(r.get("duplicate_of")), r["date"] or "9999", r["rel"]))
used = set(old.values())
n = max([int(re.sub(r"\D", "", i) or 0) for i in used] + [0])
for r in recs:
    if r["path"] in old:
        r["id"] = old[r["path"]]
    else:
        n += 1
        r["id"] = f"{args.prefix}{n:04d}"
write_jsonl(P["manifest"], recs)

ok = [r for r in recs if not r["broken"] and not r.get("duplicate_of")]
print(json.dumps({
    "视频总数": len(recs),
    "可分类": len(ok),
    "坏文件": sum(r["broken"] for r in recs),
    "重复副本（已跳过）": sum(bool(r.get("duplicate_of")) for r in recs),
    "总时长（分钟）": round(sum(r["duration"] for r in ok) / 60, 1),
    "竖拍": sum(r["portrait"] for r in ok),
    "有 .LRF 代理": sum(bool(r["proxy"]) for r in ok),
    "日期来源": dict(collections.Counter(r["date_source"] for r in ok)),
    "每天条数": dict(sorted(collections.Counter(r["date"] or "未知" for r in ok).items())),
    "manifest": P["manifest"],
}, ensure_ascii=False, indent=1))
