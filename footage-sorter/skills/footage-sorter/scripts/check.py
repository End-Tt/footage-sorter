"""Step 4: validate classifier output against vocab.json and the batch plan.

usage: check.py --work WORK                 # all parts: which IDs are missing / invalid, per batch
       check.py --work WORK --part FILE     # one classifier's file against its own batch
Exit code 0 = clean, 1 = problems (listed). Light fixes are applied in place
(string → list for multi fields, "4" → 4, 、-separated strings split); anything else is reported."""
import argparse
import glob
import json
import os
import re
import sys

sys.dont_write_bytecode = True  # no __pycache__ inside the installed skill folder
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import UNSURE, load_vocab, work_paths  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--work", required=True)
ap.add_argument("--part")
ap.add_argument("--ids", help="with --part: the exact IDs this file must contain (comma-separated); default = its whole batch")
args = ap.parse_args()
P = work_paths(args.work)
V = load_vocab(args.work)
batches = json.load(open(P["batches"], encoding="utf-8"))
owner = {i: b["batch"] for b in batches for i in b["ids"]}
DIMS = V["dimensions"]


def fix_and_check(r):
    errs = []
    for d in DIMS:
        k, allowed = d["key"], set(d["values"]) | {UNSURE}
        v = r.get(k, [] if d.get("multi") else "")
        if d.get("multi"):
            if isinstance(v, str):
                v = [s.strip() for s in re.split(r"[、,，/]", v) if s.strip()]
            v = list(dict.fromkeys(v))
            bad = [s for s in v if s not in allowed]
            if bad:
                errs.append(f"{k} 有不在词表里的值 {bad}")
            if not v and not d.get("optional"):
                errs.append(f"{k} 不能为空")
        else:
            if v is None:
                v = ""
            if v == "" and not d.get("optional"):
                errs.append(f"{k} 不能为空")
            elif v != "" and v not in allowed:
                errs.append(f"{k}={v!r} 不在词表里")
        r[k] = v
    try:
        r["rating"] = int(r.get("rating"))
        if r["rating"] not in (0, 1, 2, 3):
            raise ValueError
    except (TypeError, ValueError):
        errs.append(f"rating={r.get('rating')!r} 必须是 0-3 的整数")
    for k in ("dup_group", "place_hint", "desc", "note"):
        r[k] = r.get(k) or ""
    if r["dup_group"] and not re.fullmatch(r"G-[A-Za-z]*\d+", r["dup_group"]):
        errs.append(f"dup_group={r['dup_group']!r} 格式应为 G-C0123")
    if not r["desc"]:
        errs.append("desc 为空")
    elif len(r["desc"]) > 30:
        errs.append(f"desc 太长（{len(r['desc'])} 字，要 20 字左右）")
    return errs


def check_file(path, expect=None):
    problems, rows, seen, bad_json = [], [], set(), False
    for n, line in enumerate(open(path, encoding="utf-8"), 1):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError as e:
            problems.append(f"第 {n} 行不是合法 JSON：{e}")
            bad_json = True
            continue
        i = r.get("id")
        if i not in owner:
            problems.append(f"第 {n} 行 id={i!r} 不存在")
            continue
        if expect is not None and i not in expect:
            problems.append(f"{i} 不属于这一批")
        if i in seen:
            problems.append(f"{i} 重复出现")
        seen.add(i)
        problems += [f"{i}: {e}" for e in fix_and_check(r)]
        rows.append(r)
    if not bad_json:  # write back the light fixes; never drop a line the classifier still has to repair
        with open(path, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return problems, seen


if args.part:
    ids = []
    for line in open(args.part, encoding="utf-8"):
        try:
            ids.append(json.loads(line).get("id"))
        except json.JSONDecodeError:
            pass
    bnums = {owner[i] for i in ids if i in owner}
    expect = {i for b in batches if b["batch"] in bnums for i in b["ids"]}
    if args.ids:
        expect = {i.strip() for i in args.ids.split(",") if i.strip()}
    problems, seen = check_file(args.part, expect)
    missing = sorted(expect - seen)
    if missing:
        problems.append(f"漏了 {len(missing)} 条：{' '.join(missing)}")
    print("\n".join(problems) if problems else f"OK：{len(seen)} 条，全部合法")
    sys.exit(1 if problems else 0)

problems, seen = [], set()
for p in sorted(glob.glob(os.path.join(P["cls"], "*.jsonl"))):
    pr, s = check_file(p)
    problems += [f"[{os.path.basename(p)}] {x}" for x in pr]
    seen |= s
redo = {}
for b in batches:
    miss = [i for i in b["ids"] if i not in seen]
    if miss:
        redo[b["batch"]] = miss
report = {"已分类": len(seen), "应分类": len(owner), "问题": problems[:200], "需要重做（批次: 编号）": redo}
print(json.dumps(report, ensure_ascii=False, indent=1))
sys.exit(1 if problems or redo else 0)
