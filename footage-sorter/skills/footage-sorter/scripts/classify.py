"""Step 4 without an agent session: send the contact sheets to the user's own Codex or Claude Code CLI and get
schema-checked JSON back (used by the desktop app; the skill can use it too).

usage: classify.py --work WORK [--backend codex|claude] [--effort medium] [--sheets-per-call 3] [--jobs 2]

Codex:  codex exec -i sheet.jpg ... --output-schema WORK/schema.json -o answer.json  (prompt on stdin, read-only sandbox)
Claude: claude -p --json-schema ... --allowedTools Read  (the prompt lists the sheet paths to open)
Already-classified clips are skipped, so an interrupted run just continues. Progress = one JSON line per event."""
import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.dont_write_bytecode = True  # no __pycache__ inside the installed skill folder
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import find_cli, read_jsonl, work_paths  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument("--work", required=True)
ap.add_argument("--backend", choices=["codex", "claude"], default="codex")
ap.add_argument("--effort", default="medium", help="reasoning effort (codex: low/medium/high/xhigh)")
ap.add_argument("--sheets-per-call", type=int, default=3)
ap.add_argument("--jobs", type=int, default=2)
ap.add_argument("--timeout", type=int, default=900, help="seconds per call")
args = ap.parse_args()
P = work_paths(args.work)
os.makedirs(P["cls"], exist_ok=True)
tmp = os.path.join(P["work"], "_calls")
os.makedirs(tmp, exist_ok=True)


def emit(**kw):
    print(json.dumps(dict(stage="classify", **kw), ensure_ascii=False), flush=True)


def done_ids():
    ids = set()
    for p in glob.glob(os.path.join(P["cls"], "*.jsonl")):
        try:
            ids |= {r["id"] for r in read_jsonl(p)}
        except Exception:
            pass
    return ids


exe = find_cli(args.backend)
if not exe:
    emit(error=f"找不到 {args.backend} 命令。请先安装并登录 {'Codex' if args.backend == 'codex' else 'Claude Code'}。")
    sys.exit(2)
brief = open(os.path.join(P["work"], "brief_exec.md"), encoding="utf-8").read()
schema_path = os.path.join(P["work"], "schema.json")
batches = json.load(open(P["batches"], encoding="utf-8"))
sheets = [(s, None) for b in batches for s in b["sheets"]]
sheet_ids = {}
for b in batches:  # ids per sheet = consecutive groups of 10 inside the batch, in sheet order
    for k, s in enumerate(b["sheets"]):
        sheet_ids[s] = b["ids"][k * 10:(k + 1) * 10]
groups = [[s for s, _ in sheets[i:i + args.sheets_per_call]] for i in range(0, len(sheets), args.sheets_per_call)]


def call(group, extra=""):
    ids = [i for s in group for i in sheet_ids[s]]
    name = os.path.splitext(os.path.basename(group[0]))[0]
    prompt = brief + f"\n- 这次的联系表：{'、'.join(os.path.basename(s) for s in group)}，编号：{' '.join(ids)}。clips 里每个编号正好一个对象，按编号顺序。\n" + extra
    out = os.path.join(tmp, name + ".json")
    if os.path.exists(out):
        os.remove(out)
    if args.backend == "codex":
        cmd = [exe, "exec", "--skip-git-repo-check", "--ephemeral", "-s", "read-only", "-C", P["work"],
               "-c", f'model_reasoning_effort="{args.effort}"', "--output-schema", schema_path, "-o", out]
        for s in group:
            cmd += ["-i", s]
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=args.timeout)
        text = open(out, encoding="utf-8").read() if os.path.exists(out) else ""
    else:
        prompt += "\n联系表图片路径（用 Read 工具打开）：\n" + "\n".join(group) + "\n"
        cmd = [exe, "-p", "--output-format", "json", "--json-schema", open(schema_path, encoding="utf-8").read(),
               "--allowedTools", "Read", "--add-dir", P["work"]]
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=args.timeout, cwd=P["work"])
        text = ""
        try:
            res = json.loads(r.stdout)
            so = res.get("structured_output")
            text = json.dumps(so, ensure_ascii=False) if so else res.get("result", "")
        except Exception:
            text = r.stdout
    try:
        s = text[text.index("{"):text.rindex("}") + 1]
        clips = json.loads(s)["clips"]
    except Exception:
        return name, ids, [], (r.stderr or r.stdout or "没有返回结果")[-400:]
    return name, ids, clips, ""


def run(group):
    have = done_ids()
    ids = [i for s in group for i in sheet_ids[s]]
    if all(i in have for i in ids):
        return 0
    extra = ""
    for attempt in range(2):
        t0 = time.time()
        try:
            name, ids, clips, err = call(group, extra)
        except subprocess.TimeoutExpired:
            name, clips, err = os.path.splitext(os.path.basename(group[0]))[0], [], "超时"
        part = os.path.join(P["cls"], f"auto_{name}.jsonl")
        keep = [c for c in clips if c.get("id") in ids]
        with open(part, "w", encoding="utf-8") as f:
            for c in keep:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")
        chk = subprocess.run([sys.executable, os.path.join(HERE, "check.py"), "--work", P["work"], "--part", part, "--ids", ",".join(ids)],
                             capture_output=True, text=True)
        if chk.returncode == 0:
            return len(ids)
        if attempt == 1:  # keep the failed answer for inspection, but out of cls/ so a re-run retries these clips
            os.replace(part, os.path.join(tmp, f"failed_{name}.jsonl"))
        extra = "\n上一次的回答有这些问题，请改正后重新完整回答：\n" + (err or chk.stdout)[-1500:] + "\n"
        emit(message=f"{name} 第 {attempt + 1} 次结果不完整，{'重试' if attempt == 0 else '跳过'}：{(err or chk.stdout).strip()[:120]}",
             seconds=round(time.time() - t0))
    return 0


total = sum(len(v) for v in sheet_ids.values())
have = done_ids()
emit(done=len([i for s in sheet_ids for i in sheet_ids[s] if i in have]), total=total, message=f"开始 AI 分类（{args.backend}，{len(groups)} 次请求）")
with ThreadPoolExecutor(max(1, args.jobs)) as ex:
    for _ in ex.map(run, groups):
        have = done_ids()
        emit(done=len([i for s in sheet_ids for i in sheet_ids[s] if i in have]), total=total)
have = done_ids()
missing = [i for s in sheet_ids for i in sheet_ids[s] if i not in have]
emit(done=total - len(missing), total=total, missing=missing, message="AI 分类完成" if not missing else f"还有 {len(missing)} 条没分出来，可以再点一次开始补上")
sys.exit(0 if not missing else 1)
