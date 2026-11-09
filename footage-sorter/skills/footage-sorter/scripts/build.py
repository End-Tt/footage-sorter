"""Step 6: merge scan + metrics + classification and write the deliverables into OUT:
  index.html (offline catalog, open in a browser)   素材清单.csv (Excel/Numbers)   坏文件与重复副本.csv
  <event>.fcpxml (Final Cut Pro: 文件 › 导入 › XML…; keywords grouped into folders, ★3 = 个人收藏, 0 = 拒绝)
Originals are never copied, moved or modified; FCP and the web page point at them where they are.

usage: build.py --work WORK --out OUT [--event 素材分类] [--title "素材目录"] [--itinerary itinerary.json]

itinerary.json (optional; also picked up from WORK/itinerary.json) maps clips to places:
  [{"from": "2026-03-01", "to": "2026-03-05", "place": "日本·京都"},
   {"folder": "富士山航拍", "place": "日本·富士山"}]          # folder rules win over date rules"""
import argparse
import collections
import csv
import glob
import json
import os
import shutil
import sys
import xml.etree.ElementTree as ET
from fractions import Fraction
from urllib.parse import quote

sys.dont_write_bytecode = True  # no __pycache__ inside the installed skill folder
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import SKILL_DIR, UNSURE, classify_ids, load_vocab, read_jsonl, work_paths  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--work", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--event", default="素材分类")
ap.add_argument("--title", default="素材目录")
ap.add_argument("--itinerary")
ap.add_argument("--no-fcpxml", action="store_true", help="skip the Final Cut Pro file")
args = ap.parse_args()
P = work_paths(args.work)
V = load_vocab(args.work)
DIMS = V["dimensions"]
OUT = os.path.abspath(os.path.expanduser(args.out))
os.makedirs(os.path.join(OUT, "thumbs"), exist_ok=True)

man = read_jsonl(P["manifest"])
cls = {}
for p in sorted(glob.glob(os.path.join(P["cls"], "*.jsonl"))):
    for r in read_jsonl(p):
        cls[r["id"]] = r
clips = classify_ids(man)
missing = [x["id"] for x in clips if x["id"] not in cls]
if missing:
    print(f"注意：{len(missing)} 条还没分类（{' '.join(missing[:20])}…），先跑 check.py 补齐；这些条目会标成「未分类」。", file=sys.stderr)

it_path = args.itinerary or (P["itinerary"] if os.path.exists(P["itinerary"]) else None)
ITIN = json.load(open(it_path, encoding="utf-8")) if it_path else []


def place_of(x):
    top = x["rel"].split(os.sep)[0] if os.sep in x["rel"] else ""
    for r in ITIN:
        if r.get("folder") and (x["rel"].startswith(r["folder"].rstrip("/") + os.sep) or top == r["folder"]):
            return r["place"], "按文件夹"
    d = x.get("date") or ""
    for r in ITIN:
        if r.get("from") and d and r["from"] <= d <= r.get("to", r["from"]):
            return r["place"], "按日期"
    return ("地点待定" if ITIN else ""), ("日期不在行程表里" if d else "没有日期") if ITIN else ""


for x in clips:
    c = cls.get(x["id"], {})
    for d in DIMS:
        x[d["key"]] = c.get(d["key"], [] if d.get("multi") else "")
    x["rating"] = c.get("rating")
    for k in ("dup_group", "place_hint", "desc", "note"):
        x[k] = c.get(k, "")
    if not c:
        x["desc"] = "（未分类）"
    x["place"], x["place_src"] = place_of(x)
    x["folder"] = x["rel"].split(os.sep)[0] if os.sep in x["rel"] else "（根目录）"

# ---------- thumbnails ----------
for x in clips:
    src, dst = os.path.join(P["strips"], x["id"] + ".jpg"), os.path.join(OUT, "thumbs", x["id"] + ".jpg")
    if os.path.exists(src):
        shutil.copyfile(src, dst)


# ---------- CSV ----------
def cell(v):
    if isinstance(v, bool):
        return "是" if v else ""
    return "、".join(v) if isinstance(v, list) else ("" if v is None else v)


cols = [("编号", "id"), ("地点", "place"), ("地点依据", "place_src"), ("日期", "date"), ("日期依据", "date_source"),
        ("文件夹", "folder"), ("相对路径", "rel"), ("时长(秒)", "duration"), ("宽", "w"), ("高", "h"), ("帧率", "fps"),
        ("机型", "model"), ("竖拍", "portrait"), ("评分", "rating")] + [(d["label"], d["key"]) for d in DIMS] + \
       [("重复组", "dup_group"), ("地标线索", "place_hint"), ("描述", "desc"), ("备注", "note"), ("亮度", "brightness"),
        ("锐度", "sharpness"), ("画面变化", "motion"), ("色彩度", "colorfulness"), ("暗部比例", "dark_ratio"), ("完整路径", "path")]
with open(os.path.join(OUT, "素材清单.csv"), "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow([c for c, _ in cols])
    for x in clips:
        w.writerow([cell(x.get(k)) for _, k in cols])
other = [x for x in man if x.get("broken") or x.get("duplicate_of") or x.get("extract_error")]
with open(os.path.join(OUT, "坏文件与重复副本.csv"), "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(["类别", "文件", "大小(字节)", "时长", "说明", "完整路径"])
    for x in other:
        kind = "重复副本" if x.get("duplicate_of") else "坏文件（空、损坏或没录成）" if x.get("broken") else "解不出画面"
        w.writerow([kind, x["rel"], x["size"], x.get("duration"), ("和这个文件相同：" + x["duplicate_of"]) if x.get("duplicate_of") else x.get("extract_error", ""), x["path"]])

# ---------- FCPXML 1.9 (FCP from 2020 on) ----------
STD = [Fraction(24000, 1001), Fraction(24), Fraction(25), Fraction(30000, 1001), Fraction(30), Fraction(48), Fraction(50),
       Fraction(60000, 1001), Fraction(60), Fraction(100), Fraction(120000, 1001), Fraction(120), Fraction(240)]


def rate(fps):
    try:
        f = Fraction(fps) if fps else Fraction(30)
    except (ValueError, ZeroDivisionError):
        f = Fraction(30)
    if f <= 0:
        f = Fraction(30)
    best = min(STD, key=lambda s: abs(s - f))
    return best if abs(best - f) / best < 0.01 else f.limit_denominator(1001)


def rt(v):
    v = Fraction(v)
    return "0s" if v == 0 else (f"{v.numerator}s" if v.denominator == 1 else f"{v.numerator}/{v.denominator}s")


def media_start(x, fr):
    tc = x.get("timecode") or ""
    try:
        h, m, s, f = (int(p) for p in tc.replace(";", ":").split(":"))
    except ValueError:
        return Fraction(0)
    nominal = round(fr)
    frames = (h * 3600 + m * 60 + s) * nominal + f
    if ";" in tc and fr.denominator == 1001:
        per = 2 if nominal == 30 else 4
        total = h * 60 + m
        frames -= per * (total - total // 10)
    return frames / fr


root = ET.Element("fcpxml", version="1.9")
res = ET.SubElement(root, "resources")
ev = ET.SubElement(ET.SubElement(root, "library"), "event", name=args.event)
fmt_ids, folders = {}, collections.defaultdict(dict)  # folder -> {keyword: None} (ordered set)
dim_folder = {d["key"]: d.get("folder", d["label"]) for d in DIMS if d.get("keyword", True)}
n_folders = len({x["folder"] for x in clips})
for n, x in enumerate(clips, 1):
    fr = rate(x.get("fps"))
    fd = 1 / fr
    key = (x["w"], x["h"], fd)
    if key not in fmt_ids:
        fmt_ids[key] = f"f{len(fmt_ids) + 1}"
        ET.SubElement(res, "format", id=fmt_ids[key], frameDuration=rt(fd), width=str(x["w"]), height=str(x["h"]))
    dur = rt(int(Fraction(str(x["duration"])) / fd) * fd)
    st = rt(media_start(x, fr))
    stem = os.path.splitext(os.path.basename(x["path"]))[0]
    attrs = dict(id=f"a{n}", name=stem, start=st, duration=dur, hasVideo="1", format=fmt_ids[key], videoSources="1")
    if x.get("audio_streams"):
        attrs.update(hasAudio="1", audioSources="1")
    a = ET.SubElement(res, "asset", **attrs)
    ET.SubElement(a, "media-rep", kind="original-media", src="file://" + quote(x["path"]))
    clip = ET.SubElement(ev, "asset-clip", ref=f"a{n}", name=f"{x['id']} {x['desc'] or stem}"[:120], start=st, duration=dur, format=fmt_ids[key])
    ET.SubElement(clip, "note").text = "｜".join(v for v in [x["place"], x["date"], x["desc"], x["note"],
                                                            ("地标：" + x["place_hint"]) if x["place_hint"] else "", x["rel"]] if v)
    kws = []

    def add(folder, kw):
        kw = str(kw).replace(",", "，").strip()
        if kw and kw not in (UNSURE, "无", "不适合"):
            kws.append(kw)
            folders[folder].setdefault(kw)
    if x["place"]:
        add("地点", x["place"])
    if x.get("date"):
        add("日期", x["date"])
    if n_folders > 1:
        add("来源文件夹", x["folder"])
    for d in DIMS:
        if d["key"] in dim_folder:
            for v in (x[d["key"]] if isinstance(x[d["key"]], list) else [x[d["key"]]]):
                add(dim_folder[d["key"]], v)
    if x.get("portrait"):
        add("技术问题", "竖拍")
    if x["dup_group"]:
        add("重复组", "重复组 " + x["dup_group"])
    kws = list(dict.fromkeys(kws))
    if kws:
        ET.SubElement(clip, "keyword", start=st, duration=dur, value=", ".join(kws))
    if x["rating"] == 3:
        ET.SubElement(clip, "rating", start=st, duration=dur, value="favorite")
    elif x["rating"] == 0:
        ET.SubElement(clip, "rating", start=st, duration=dur, value="reject")
order = ["地点", "日期", "来源文件夹"] + list(dict.fromkeys(dim_folder.values())) + ["技术问题", "重复组"]
for name in dict.fromkeys(order):
    if folders.get(name):
        cf = ET.SubElement(ev, "collection-folder", name=name)
        for kw in sorted(folders[name]):
            ET.SubElement(cf, "keyword-collection", name=kw)
ET.indent(root, space="  ")
fcpxml = "" if args.no_fcpxml else os.path.join(OUT, f"{args.event}.fcpxml")
if fcpxml:
    with open(fcpxml, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n\n' + ET.tostring(root, encoding="unicode") + "\n")

# ---------- offline web page ----------
slim_keys = ["id", "place", "place_src", "date", "folder", "rel", "duration", "portrait", "rating", "dup_group", "place_hint", "desc", "note", "path"] + [d["key"] for d in DIMS]
data = {"title": args.title,
        "dims": [{"key": d["key"], "label": d["label"], "multi": bool(d.get("multi"))} for d in DIMS],
        "clips": [{k: x.get(k) for k in slim_keys} for x in clips],
        "other": [{"rel": x["rel"], "kind": "重复副本" if x.get("duplicate_of") else "坏文件"} for x in other]}
tpl = open(os.path.join(SKILL_DIR, "catalog_template.html"), encoding="utf-8").read()
page = tpl.replace("__TITLE__", args.title.replace("<", "&lt;")).replace("__DATA__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
open(os.path.join(OUT, "index.html"), "w", encoding="utf-8").write(page)

print(json.dumps({
    "输出文件夹": OUT,
    "条数": len(clips), "未分类": len(missing),
    "评分": dict(collections.Counter(str(x["rating"]) for x in clips)),
    "地点": dict(collections.Counter(x["place"] or "（没设行程表）" for x in clips)),
    "FCP 关键词文件夹": {k: len(v) for k, v in folders.items()},
    "fcpxml": fcpxml,
}, ensure_ascii=False, indent=1))
