"""Step 3: write the classifier instructions from WORK/vocab.json (the same file drives check.py, so they never disagree):
  WORK/brief.md       for an agent that looks at the sheets itself and writes a JSONL file (Claude Code / Codex skill)
  WORK/brief_exec.md  for classify.py: images are attached to the request, the answer is JSON matching WORK/schema.json
  WORK/schema.json    JSON Schema of that answer (enums = the vocab)

usage: make_brief.py --work WORK"""
import argparse
import json
import os
import sys

sys.dont_write_bytecode = True  # no __pycache__ inside the installed skill folder
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import UNSURE, load_vocab, work_paths  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--work", required=True)
args = ap.parse_args()
P = work_paths(args.work)
V = load_vocab(args.work)
check = os.path.join(os.path.dirname(os.path.abspath(__file__)), "check.py")

lines = []
for d in V["dimensions"]:
    vals = f"同 `{d['values_from']}` 的列表" if d.get("values_from") else " / ".join(d["values"])
    kind = "可多选，JSON 列表" if d.get("multi") else "选一个，字符串"
    opt = "；可以为空" + ("列表 []" if d.get("multi") else "字符串 \"\"") if d.get("optional") else ""
    hint = f"。{d['hint']}" if d.get("hint") else ""
    lines.append(f"- `{d['key']}`（{d['label']}，{kind}{opt}{hint}）：{vals}")
fields = "\n".join(lines)
rating = "；".join(f"{k} = {v}" for k, v in sorted(V["rating"].items(), reverse=True))
example = {"id": "C0001"}
for d in V["dimensions"]:
    example[d["key"]] = ([d["values"][0]] if d.get("multi") else d["values"][0]) if not d.get("optional") else ([] if d.get("multi") else "")
example.update({"rating": 2, "dup_group": "", "place_hint": "", "desc": "航拍沿海公路，远处雪山", "note": ""})

ALL = {v for d in V["dimensions"] for v in d["values"]}
rules = []  # rules that name vocab words are only written when those words exist in this vocab
if "竖拍" in ALL:
    rules.append("- 竖拍的素材 quality 里加「竖拍」，rating 最多给 2。")
else:
    rules.append("- 竖拍的素材 rating 最多给 2。")
rules.append("- 评分：整条全黑、误拍、镜头被挡住的给 0；只有一段能用的，按能用那段的好坏打分（最多 2 分），在 note 里写清哪段能用；明显糊、内容有用的给 1。")
if "严重抖动" in ALL:
    rules.append("- 「严重抖动」只在 6 帧里看得出明显拖影、画面歪来歪去时才标；静态缩略图看不出抖就不标。")
if {"日出日落金光", "蓝调时刻"} <= ALL:
    rules.append("- 光线：暖光照在主体上、或暖色是画面主角，算「日出日落金光」；整体偏蓝、只有天边一条暖色，算「蓝调时刻」。")
if "行进中拍摄（车船缆车）" in ALL:
    rules.append("- 在车、船、缆车里往外拍的，运镜选「行进中拍摄（车船缆车）」。")
rules = "\n".join(rules)

def build(mode):
    if mode == "agent":
        intro = "你负责看一批素材的联系表，给每条素材打标签。用看图工具（Claude Code 的 Read、Codex 的 view_image）打开联系表图片；原视频不要打开、不要改动。"
        out_head = "## 每条素材输出一行 JSON（只输出 JSON 行，不要别的文字）"
        tail = f"""- 结果写到派活时给你的输出文件（也就是 batches.json 里你这一批的 `out`），JSONL，UTF-8，一行一条。写完运行：
  `python3 "{check}" --work "{P['work']}" --part <你的输出文件>`
  有报错就改到没有报错为止。检查脚本会顺手把格式小问题（比如该是列表的写成了字符串）就地改好；有不合法 JSON 的行时它不会改动文件，只报行号。
- 最后交回：总条数、各 rating 数量、最多 10 条最推荐的（编号 + 一句理由，废片不凑数）、拿不准的地方。"""
    else:
        intro = "你负责看这批素材的联系表（图片附在这条消息里，或者按下面列出的路径打开），给每条素材打标签。不需要运行任何命令，也不要写文件。"
        out_head = "## 每条素材输出一个 JSON 对象，全部放进 clips 列表"
        tail = "- 只交回符合格式的 JSON，不要别的文字。"
    return f"""# 素材分类说明（给分类助手）

## 背景
{V['project']}
{intro}

## 联系表怎么看
每张联系表（sheet_XXX.jpg）最多 10 行，每行一条素材：
- 左边五行字：① 素材编号（黄字，如 C0123）② 拍摄日期（「日期未知」表示读不到）③ 时长和所在文件夹 ④ `mot`（相邻抽帧的画面平均差异，越大说明画面变化越大，分不清是机位在动还是画面里的东西在动，要你看图判断）和 `br`（平均亮度 0-255）⑤ `sh`（锐度，越大越清楚；雪地、雾天、夜景本来就低），竖拍的素材在这一行末尾写 PORTRAIT。
- 右边：这条素材从头到尾均匀抽的 6 帧。
- 文件夹名只是参考（比如叫「手机」的文件夹里也可能有航拍），**和画面对不上时以画面为准**，在 note 里写一句。

{out_head}
下面的结构化字段只能从列表里选；实在拿不准填「{UNSURE}」（多选字段就写 ["{UNSURE}"]）。

- `id`：联系表上的编号，照抄，不要自己编。
{fields}
- `rating`（可用度，整数）：{rating}
- `dup_group`（重复组）：同一机位、同一画面拍了好几条的，给它们同一个组名，格式「G-<这一组里最小的编号>」，例如 G-C0123；不重复就填空字符串。只在你这一批里判断。
- `place_hint`（地标线索，可空）：画面里**直接看得出**的地点线索：招牌上的字、车牌，或者一眼就能认出的著名地标（建筑、山峰、瀑布，名字后面加「？」表示是认出来的）。没有就空字符串。**普通风景不要猜国家或城市。**
- `desc`（一句话描述，中文，20 字以内）：具体写画面里有什么、怎么拍的。
- `note`（可空）：补充，比如「前 3 秒在找构图，后段可用」「第 4 帧起光线变好」。

示例（只是格式示例）：
{json.dumps(example, ensure_ascii=False)}

## 要求
- 你这一批的每条素材都要有一行，不能漏、不能多、不能自己编号。
- 结构化字段不要写解释性的长句。
{rules}
{tail}
"""


open(P["brief"], "w", encoding="utf-8").write(build("agent"))
open(os.path.join(P["work"], "brief_exec.md"), "w", encoding="utf-8").write(build("exec"))

# JSON Schema for the exec answer: {"clips": [ {...}, ... ]} with the vocab as enums (strict: every key required)
props = {"id": {"type": "string"}}
for d in V["dimensions"]:
    vals = list(d["values"]) + [UNSURE]
    if d.get("multi"):
        props[d["key"]] = {"type": "array", "items": {"type": "string", "enum": vals}}
    else:
        props[d["key"]] = {"type": "string", "enum": vals + ([""] if d.get("optional") else [])}
props["rating"] = {"type": "integer", "enum": [0, 1, 2, 3]}
for k in ("dup_group", "place_hint", "desc", "note"):
    props[k] = {"type": "string"}
item = {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}
schema = {"type": "object", "properties": {"clips": {"type": "array", "items": item}}, "required": ["clips"], "additionalProperties": False}
json.dump(schema, open(os.path.join(P["work"], "schema.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(P["brief"])
