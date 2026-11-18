"""Step 1: inventory every video under one or more folders (read-only) -> WORK/manifest.jsonl.

usage: scan.py FOLDER [FOLDER ...] --work WORK [--exclude TEXT ...] [--prefix C]

For each file: duration, size, resolution, fps, rotation (portrait), timecode, camera model, GPS,
shooting date (+ where the date came from), broken / duplicate-copy flags, DJI .LRF proxy.
Camera RAW originals (.R3D / .NEV) are matched by file name to the video recorded with them (field "companions");
their content is not read then. Unmatched ones get their own record: if the file is a QuickTime shell with a JPEG
preview in moov/udta (Nikon R3D NE: NCDT/NCVW, 1920x1080), it is probed like a video and classified from that one
picture (raw + preview); otherwise raw + broken, so they do not vanish.
IDs (C0001 …) follow shooting date; re-running keeps the IDs already handed out."""
import argparse
import collections
import json
import os
import re
import struct
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

sys.dont_write_bytecode = True  # no __pycache__ inside the installed skill folder
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import need, read_jsonl, work_paths, write_jsonl  # noqa: E402

VIDEO_EXT = (".mp4", ".mov", ".m4v", ".mts", ".m2ts", ".avi", ".mkv", ".mxf", ".3gp")
# camera RAW originals (RED R3D, Nikon N-RAW): ffmpeg cannot decode them; they ride along with the same-name file
# the camera records at the same time. Without one, only the container header and an embedded preview JPEG are read.
RAW_EXT = (".r3d", ".nev")
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
files, raws = [], []
for root in roots:
    if not os.path.isdir(root):
        sys.exit(f"找不到文件夹：{root}")
    for dp, dn, fn in os.walk(root):
        dn[:] = sorted(d for d in dn if not d.startswith(".") and not d.endswith((".fcpbundle", ".photoslibrary")))
        for f in sorted(fn):
            if f.startswith((".", "._")) or not f.lower().endswith(VIDEO_EXT + RAW_EXT):
                continue
            p = os.path.join(dp, f)
            if any(e in p for e in args.exclude):
                continue
            (raws if f.lower().endswith(RAW_EXT) else files).append((root, p))
print(f"找到视频文件 {len(files)} 个", file=sys.stderr)


def rel_of(root, p):
    rel = os.path.relpath(p, root)
    return os.path.join(os.path.basename(root), rel) if len(roots) > 1 else rel


def probe(item):
    root, p = item
    rec = {"path": p, "rel": rel_of(root, p), "size": os.path.getsize(p)}
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


def raw_preview(path):
    """[file offset, length] of the largest JPEG preview stored in moov/udta of a QuickTime-shell RAW file, or None.
    Walks the atom headers with seek only (64-bit and to-end sizes); enters moov -> udta -> NCDT, never reads mdat,
    skips leaves over 32 MB. Bad atom, too many atoms or any error -> None."""
    try:
        end = os.path.getsize(path)
        with open(path, "rb") as f:
            if f.read(8)[4:8] != b"ftyp":
                return None
            budget = [10000]  # atom headers left to read
            best = []

            def walk(start, stop, depth):  # depth 0 = file, 1 = moov, 2 = udta, 3 = NCDT
                pos = start
                while pos + 8 <= stop:
                    budget[0] -= 1
                    if budget[0] < 0:
                        raise ValueError("too many atoms")
                    f.seek(pos)
                    n, kind = struct.unpack(">I4s", f.read(8))
                    head = 8
                    if n == 1:
                        n, head = struct.unpack(">Q", f.read(8))[0], 16
                    elif n == 0:
                        n = stop - pos
                    if n < head or pos + n > stop:
                        raise ValueError("bad atom")
                    body, size = pos + head, n - head
                    if (depth, kind) in ((0, b"moov"), (1, b"udta"), (2, b"NCDT")):
                        walk(body, pos + n, depth + 1)
                    elif depth >= 2 and kind != b"mdat" and 3 < size <= 32 << 20:
                        f.seek(body)
                        data = f.read(size)
                        i, j = data.find(b"\xff\xd8\xff", 0, 64), data.rfind(b"\xff\xd9")
                        if 0 <= i < j:
                            best.append([body + i, j + 2 - i])
                    pos += n
            walk(0, end, 0)
        return max(best, key=lambda b: b[1]) if best else None
    except Exception:
        return None


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

# RAW originals, by file name only: same folder + same name (any case) -> that video; else inside a RED .RDC folder
# -> the folder's first video. Duplicate copy -> the kept one. No (readable) video -> own record, still listed
# (classified from its embedded preview picture when it has one, see raw_preview).
by_path = {r["path"]: r for r in recs}
vids = collections.defaultdict(list)  # folder -> videos, file-name order
for r in sorted(recs, key=lambda r: r["path"]):
    vids[os.path.dirname(r["path"])].append(r)
mates = collections.defaultdict(dict)  # kept video path -> {(name lower, size): RAW path}
lone_items, copies = [], []
for root, p in sorted(raws, key=lambda t: t[1]):
    d, stem = os.path.dirname(p), os.path.splitext(os.path.basename(p))[0].lower()
    same = [r for r in vids[d] if os.path.splitext(os.path.basename(r["path"]))[0].lower() == stem]
    same.sort(key=lambda r: VIDEO_EXT.index(os.path.splitext(r["path"])[1].lower()))
    cand = same + (vids[d] if os.path.basename(d).lower().endswith(".rdc") else [])
    cand = [by_path.get(r["duplicate_of"]) if r.get("duplicate_of") else r for r in cand]
    t = next((r for r in cand if r and not r["broken"]), None)  # first usable one
    size = os.path.getsize(p)
    first = mates[t["path"]].setdefault((os.path.basename(p).lower(), size), p) if t else None
    if first == p:
        continue
    if not first:  # no usable same-name video: probed like a video below
        lone_items.append((root, p))
        continue
    rec = {"path": p, "rel": rel_of(root, p), "size": size, "duration": 0, "broken": True, "raw": True,
           "portrait": False, "proxy": None}
    # same name and size as a RAW already riding with that clip: a copy; listed, not organized twice
    rec["duplicate_of"] = first
    rec["date"], rec["date_source"] = shooting_date(rec)
    copies.append(rec)
for vp, m in mates.items():
    by_path[vp]["companions"] = sorted(m.values())
with ThreadPoolExecutor(args.jobs) as ex:
    lone = list(ex.map(probe, lone_items))
for rec in lone:
    rec["raw"] = True
    pv = raw_preview(rec["path"])
    if not rec["broken"] and pv:
        rec["preview"] = pv  # classified from this one picture
    else:
        rec["broken"] = True  # no picture at all: listed, put into the ⚠ folder
# exact copies (same name and size) among the classifiable ones, same rule as for videos; a RAW that already rides
# with a proxy somewhere else counts as the kept one
seen = {k: p for m in mates.values() for k, p in m.items()}
for r in sorted(lone, key=lambda r: r["rel"]):
    k = (os.path.basename(r["path"]).lower(), r["size"])
    if k in seen and not r["broken"]:
        r["duplicate_of"] = seen[k]
    elif not r["broken"]:
        seen.setdefault(k, r["path"])
recs += lone + copies

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
vid = [r for r in recs if not r.get("raw")]
stats = {
    "视频总数": len(vid),
    "可分类": len(ok),
    "坏文件": sum(r["broken"] for r in vid),
    "重复副本（已跳过）": sum(bool(r.get("duplicate_of")) for r in recs),
    "总时长（分钟）": round(sum(r["duration"] for r in ok) / 60, 1),
    "竖拍": sum(r["portrait"] for r in ok),
    "有 .LRF 代理": sum(bool(r["proxy"]) for r in ok),
    "日期来源": dict(collections.Counter(r["date_source"] for r in ok)),
    "每天条数": dict(sorted(collections.Counter(r["date"] or "未知" for r in ok).items())),
}
if raws:
    stats["RAW 原片"] = {"有同名代理": sum(len(r.get("companions") or []) for r in recs),
                       "没有能用的同名代理": sum(r["broken"] for r in lone),  # and no preview picture: cannot be classified
                       "按内嵌预览图分类": sum(r.get("raw", False) for r in ok)}
stats["manifest"] = P["manifest"]
print(json.dumps(stats, ensure_ascii=False, indent=1))
