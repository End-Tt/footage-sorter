"""Step 4 without an agent session: send the contact sheets to an AI and get schema-checked JSON back
(used by the desktop app; the skill can use it too).

usage: classify.py --work WORK [--backend codex|claude|api] [--effort medium] [--sheets-per-call N] [--jobs N]
       classify.py --backend api --api-base URL --api-model MODEL --test      (connection + image test only)

Codex:  codex exec -i sheet.jpg ... --output-schema WORK/schema.json -o answer.json  (prompt on stdin, read-only sandbox)
Claude: claude -p --json-schema ... --allowedTools Read  (the prompt lists the sheet paths to open)
API:    any OpenAI-compatible /chat/completions endpoint with a vision model (通义千问、豆包、智谱、Kimi、硅基流动 …);
        images go as base64 data URLs; the key is read from the environment variable FOOTAGE_API_KEY (never an argument)
Already-classified clips are skipped, so an interrupted run just continues. Progress = one JSON line per event."""
import argparse
import base64
import glob
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.dont_write_bytecode = True  # no __pycache__ inside the installed skill folder
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import UNSURE, find_cli, load_vocab, read_jsonl, work_paths  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument("--work")
ap.add_argument("--backend", choices=["codex", "claude", "api"], default="codex")
ap.add_argument("--effort", default="medium", help="reasoning effort (codex: low/medium/high/xhigh)")
ap.add_argument("--sheets-per-call", type=int, help="default: codex/claude 3, api 1")
ap.add_argument("--jobs", type=int, help="parallel requests; default: codex/claude 2, api 3")
ap.add_argument("--timeout", type=int, default=900, help="seconds per call")
ap.add_argument("--api-base", default="", help="OpenAI-compatible base URL, e.g. https://dashscope.aliyuncs.com/compatible-mode/v1")
ap.add_argument("--api-model", default="", help="a vision model name")
ap.add_argument("--api-provider", default="", help="qwen|doubao|glm|kimi|deepseek|siliconflow|ollama|custom (default: guessed from the URL)")
ap.add_argument("--test", action="store_true", help="api only: send one tiny picture and report whether the model answers")
args = ap.parse_args()
API = args.backend == "api"
LOCAL = API and (args.api_provider == "ollama" or ":11434" in args.api_base)
args.sheets_per_call = args.sheets_per_call or (1 if API else 3)
args.jobs = args.jobs or (1 if LOCAL else 3 if API else 2)  # a local model answers one request at a time


def emit(**kw):
    print(json.dumps(dict(stage="test" if args.test else "classify", **kw), ensure_ascii=False), flush=True)


class Fatal(Exception):
    """Configuration problems (bad key, wrong model, no network): stop the whole run with a clear message."""


# Per-provider quirks (official docs, checked 2026-11-09): thinking switched off where possible (slower, billed as output),
# JSON mode off where vision models do not take it, Kimi K3 rejects temperature and wants a low reasoning effort.
TWEAKS = {
    "qwen": {"host": "dashscope", "json": True, "extra": {}},
    "doubao": {"host": "volces.com", "json": True, "extra": {"thinking": {"type": "disabled"}}},
    "glm": {"host": "bigmodel.cn", "json": False, "extra": {"thinking": {"type": "disabled"}}},
    "kimi": {"host": "moonshot", "json": True, "extra": {"reasoning_effort": "low"}, "no_temperature": True, "max_key": "max_completion_tokens"},
    "deepseek": {"host": "deepseek.com", "json": True, "extra": {"thinking": {"type": "disabled"}}},
    "siliconflow": {"host": "siliconflow", "json": False, "extra": {}},
    "ollama": {"host": ":11434", "json": True, "extra": {}, "local": True, "schema": True},  # free, on this Mac; enforces the schema
    "custom": {"host": "", "json": True, "extra": {}},
}
prov = args.api_provider or next((k for k, t in TWEAKS.items() if t["host"] and t["host"] in args.api_base), "custom")
if prov == "kimi" or "kimi.com" in args.api_base:
    prov = "kimi"
TW = TWEAKS.get(prov, TWEAKS["custom"])
if prov == "qwen" and args.api_model.startswith("qwen3."):  # qwen3.x text+vision models think by default
    TW = dict(TW, extra={"enable_thinking": False})
_json_mode = [TW["json"]]  # also switched off at the first refusal


def ollama_chat(content, system=""):
    """Ollama's native /api/chat: small context window (the default 256k one does not fit in RAM for a 32B model),
    thinking off, output constrained to the vocab schema."""
    host = re.sub(r"/v1/?$", "", args.api_base.rstrip("/"))
    text = "".join(p.get("text", "") for p in content if p.get("type") == "text")
    images = [p["image_url"]["url"].split(",", 1)[1] for p in content if p.get("type") == "image_url"]
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": text, "images": images}]
    body = {"model": args.api_model, "messages": msgs, "stream": False, "think": False, "keep_alive": "10m",
            "format": SCHEMA if SCHEMA else "json", "options": {"num_ctx": 16384, "temperature": 0.2, "num_predict": 8192}}
    req = urllib.request.Request(host + "/api/chat", data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=args.timeout) as r:
                return json.loads(r.read().decode("utf-8"))["message"].get("content", "")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            if e.code == 404:
                raise Fatal(f"本机还没有这个模型：在终端运行 ollama pull {args.api_model}（{detail[:120]}）")
            if e.code == 400 and "think" in detail and body.get("think") is False:
                body.pop("think")  # models without a thinking switch
                req = urllib.request.Request(host + "/api/chat", data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                             method="POST", headers={"Content-Type": "application/json"})
                continue
            raise RuntimeError(f"HTTP {e.code}: {detail}")
        except urllib.error.URLError as e:
            raise Fatal(f"连不上本机的 Ollama：{e.reason}。请先打开 Ollama（或在终端运行 ollama serve）")
        except (TimeoutError, OSError) as e:
            if attempt < 1:
                continue
            raise RuntimeError(f"本机模型没有正常回答：{e!r}"[:300])
    raise RuntimeError("多次重试后仍失败")


def api_chat(content, system=""):
    """POST {base}/chat/completions; returns the assistant text. Retries 429/5xx; raises Fatal on config errors."""
    if TW.get("local"):
        return ollama_chat(content, system)
    key = os.environ.get("FOOTAGE_API_KEY", "").strip() or ("ollama" if TW.get("local") else "")
    if not key:
        raise Fatal("没有填 API Key")
    if not args.api_base or not args.api_model:
        raise Fatal("没有填接口地址或模型名")
    url = args.api_base.rstrip("/") + ("" if args.api_base.rstrip("/").endswith("/chat/completions") else "/chat/completions")
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": content}]
    for attempt in range(4):
        body = {"model": args.api_model, "messages": msgs, TW.get("max_key", "max_tokens"): 8192, **TW["extra"]}
        if not TW.get("no_temperature"):
            body["temperature"] = 0.2
        if _json_mode[0]:
            body["response_format"] = {"type": "json_object"}
            if TW.get("schema") and SCHEMA:  # constrained decoding: only vocab words can come out
                body["response_format"] = {"type": "json_schema", "json_schema": {"name": "clips", "schema": SCHEMA, "strict": True}}
        req = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
        try:
            with urllib.request.urlopen(req, timeout=min(args.timeout, 300)) as r:
                res = json.loads(r.read().decode("utf-8"))
            msg = res["choices"][0]["message"]
            text = msg.get("content") or ""
            if isinstance(text, list):  # some providers return content parts
                text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
            if not text.strip() and "response_format" in body:  # e.g. DeepSeek blanks invalid JSON-mode answers
                _json_mode[0] = False
                continue
            return text
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:400]
            if e.code == 400 and "response_format" in body and ("response_format" in detail or "json" in detail.lower()):
                _json_mode[0] = False  # this provider/model does not take JSON mode: resend without it (all threads)
                continue
            if e.code in (401, 403):
                raise Fatal(f"API Key 不对、没开通这个模型，或者没有权限（HTTP {e.code}）：{detail[:200]}")
            if e.code == 404:
                hint = f"。本机模型要先下载：在终端运行 ollama pull {args.api_model}" if TW.get("local") else ""
                raise Fatal(f"接口地址或模型名不对（HTTP 404）：{detail[:200]}{hint}")
            if e.code == 400 and ("image" in detail.lower() or "vision" in detail.lower() or "multimodal" in detail.lower()):
                raise Fatal(f"这个模型可能不支持看图，请换一个视觉模型：{detail[:200]}")
            if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep((3, 10, 25)[attempt])
                continue
            raise RuntimeError(f"HTTP {e.code}: {detail[:300]}")
        except urllib.error.URLError as e:
            if attempt < 2:
                time.sleep(5)
                continue
            raise Fatal(f"连不上接口地址：{e.reason}" + ("。本机模型要先打开 Ollama（或在终端运行 ollama serve）" if TW.get("local") else ""))
        except (TimeoutError, OSError, ValueError, KeyError, IndexError) as e:  # read timeout, bad JSON body …
            if attempt < 2:
                time.sleep(5)
                continue
            raise RuntimeError(f"接口没有正常回答：{e!r}"[:300])
    raise RuntimeError("多次重试后仍失败")


SCHEMA = None  # set once the work folder is known


def sanitize(c, dims, rating_ok=(0, 1, 2, 3)):
    """Last resort for weaker models: out-of-vocab labels become 不确定 instead of losing the whole sheet."""
    fixed = 0
    for d in dims:
        allowed = set(d["values"]) | {UNSURE}
        v = c.get(d["key"])
        if d.get("multi"):
            vals = v if isinstance(v, list) else [x for x in str(v or "").replace("，", "、").split("、") if x]
            keep = [x for x in vals if x in allowed]
            fixed += len(vals) - len(keep)
            c[d["key"]] = keep or ([] if d.get("optional") else [UNSURE])
        else:
            if v in allowed or (d.get("optional") and v in ("", None)):
                c[d["key"]] = v or ""
            else:
                fixed += 1
                c[d["key"]] = "" if d.get("optional") else UNSURE
    try:
        c["rating"] = int(c.get("rating"))
        assert c["rating"] in rating_ok
    except Exception:
        c["rating"], fixed = 1, fixed + 1
    for k in ("dup_group", "place_hint", "desc", "note"):
        c[k] = str(c.get(k) or "")
    if c["dup_group"] and not c["dup_group"].startswith("G-"):
        c["dup_group"] = ""
    c["desc"] = c["desc"][:30] or "（AI 没写描述）"
    if fixed:
        c["note"] = (c["note"] + "；" if c["note"] else "") + f"有 {fixed} 个标签不合规，已改成「不确定」"
    return fixed


def data_url(path):
    return "data:image/jpeg;base64," + base64.b64encode(open(path, "rb").read()).decode("ascii")


if args.test:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (96, 96), (220, 30, 30)).save(buf, "JPEG")
    t0 = time.time()
    try:
        ans = api_chat([{"type": "text", "text": '这张图主要是什么颜色？只回一个 JSON 对象，例如 {"color": "蓝色"}。'},
                        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()}}])
        try:
            color = str(json.loads(ans[ans.index("{"):ans.rindex("}") + 1]).get("color", ""))
        except Exception:
            color = ""
        red = any(w in (color or ans).lower() for w in ("红", "red", "赤", "绯", "朱"))
        ok = bool(color) or red
        secs = round(time.time() - t0, 1)
        if ok and red:
            msg = f"连接正常，模型能看图（{secs} 秒）"
        elif ok:
            msg = f"连接正常，但模型说测试图是「{color}」（其实是红色），看图能力可能偏弱（{secs} 秒）"
        else:
            msg = f"连上了，但回答不对，这个模型可能不支持看图：{ans.strip()[:80]}"
        emit(ok=ok, answer=ans.strip()[:200], seconds=secs, message=msg)
        sys.exit(0 if ok else 3)
    except (Fatal, RuntimeError) as e:
        emit(ok=False, message=str(e))
        sys.exit(2)

if not args.work:
    sys.exit("缺少 --work")
P = work_paths(args.work)
os.makedirs(P["cls"], exist_ok=True)
tmp = os.path.join(P["work"], "_calls")
os.makedirs(tmp, exist_ok=True)


def done_ids():
    ids = set()
    for p in glob.glob(os.path.join(P["cls"], "*.jsonl")):
        try:
            ids |= {r["id"] for r in read_jsonl(p)}
        except Exception:
            pass
    return ids


exe = "api" if API else find_cli(args.backend)
if not exe:
    emit(error=f"找不到 {args.backend} 命令。请先安装并登录 {'Codex' if args.backend == 'codex' else 'Claude Code'}。")
    sys.exit(2)
brief = open(os.path.join(P["work"], "brief_exec.md"), encoding="utf-8").read()
schema_path = os.path.join(P["work"], "schema.json")
SCHEMA = json.load(open(schema_path, encoding="utf-8")) if os.path.exists(schema_path) else None
DIMS = load_vocab(P["work"])["dimensions"]
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
    if API:
        content = [{"type": "text", "text": f"这次的联系表：{'、'.join(os.path.basename(s) for s in group)}，编号：{' '.join(ids)}。"
                                            f"clips 里每个编号正好一个对象，按编号顺序。只输出 {{\"clips\": [...]}} 这一个 JSON 对象。\n" + extra}]
        content += [{"type": "image_url", "image_url": {"url": data_url(s)}} for s in group]
        try:
            text = api_chat(content, system=brief)
        except RuntimeError as e:
            return name, ids, [], str(e)
        r = None
    elif args.backend == "codex":
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
        if r is None:
            return name, ids, [], "回答不是有效的 JSON：" + text.strip()[:300]
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
        if attempt == 1 and keep:  # second miss: repair out-of-vocab labels to 不确定 rather than drop the sheet
            fixed = sum(sanitize(c, DIMS) for c in keep)
            with open(part, "w", encoding="utf-8") as f:
                for c in keep:
                    f.write(json.dumps(c, ensure_ascii=False) + "\n")
            chk = subprocess.run([sys.executable, os.path.join(HERE, "check.py"), "--work", P["work"], "--part", part, "--ids", ",".join(ids)],
                                 capture_output=True, text=True)
            if chk.returncode == 0:
                emit(message=f"{name}：有 {fixed} 个标签不合规，已改成「不确定」后收下")
                return len(ids)
        if attempt == 1:  # keep the failed answer for inspection, but out of cls/ so a re-run retries these clips
            os.replace(part, os.path.join(tmp, f"failed_{name}.jsonl"))
        extra = "\n上一次的回答有这些问题，请改正后重新完整回答：\n" + (err or chk.stdout)[-1500:] + "\n"
        emit(message=f"{name} 第 {attempt + 1} 次结果不完整，{'重试' if attempt == 0 else '跳过'}：{(err or chk.stdout).strip()[:120]}",
             seconds=round(time.time() - t0))
    return 0


total = sum(len(v) for v in sheet_ids.values())
have = done_ids()
who = f"{args.api_model}（接口）" if API else args.backend
emit(done=len([i for s in sheet_ids for i in sheet_ids[s] if i in have]), total=total, message=f"开始 AI 分类（{who}，{len(groups)} 次请求）")
try:
    with ThreadPoolExecutor(max(1, args.jobs)) as ex:
        for _ in ex.map(run, groups):
            have = done_ids()
            emit(done=len([i for s in sheet_ids for i in sheet_ids[s] if i in have]), total=total)
except Fatal as e:
    emit(error=str(e), message="AI 分类停下了：" + str(e))
    sys.exit(2)
have = done_ids()
missing = [i for s in sheet_ids for i in sheet_ids[s] if i not in have]
emit(done=total - len(missing), total=total, missing=missing, message="AI 分类完成" if not missing else f"还有 {len(missing)} 条没分出来，可以再点一次开始补上")
sys.exit(0 if not missing else 1)
