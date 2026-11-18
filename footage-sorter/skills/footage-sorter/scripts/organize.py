"""Step 7 (optional): put the footage into a new folder, sorted by category. Originals are never moved or changed.

usage: organize.py --work WORK --dest DEST [--mode copy|link]

DEST/
  按场景/<场景>/★★★ C0007 一句话描述 (原文件名).mov   every usable clip once, in its main scene folder
  按场景/<场景>/★★★ C0007 一句话描述 (原片名).R3D     its camera RAW original (.R3D / .NEV), wherever the clip goes
  按场景/<场景>/★★ C0008 一句话描述 (DSC_5444).R3D    a RAW original with no same-name clip, classified from the
                                                      preview picture stored in the file (first frame only)
  ★★★ 精选/…                                          all 3-star clips again
  ✕ 废片/…                                            0-star clips (not in the scene folders)
  未分类/…                                            clips the AI could not classify
  ⚠ 解不出画面（没分类）/C0001 (原文件名).MOV            clips this Mac cannot decode (RAW with neither a same-name clip
                                                      nor a preview picture / unusual codec / old ffmpeg)
                                                      or that look broken, ≥ 20000 bytes; extension kept as is
mode copy: APFS clone (clonefile(2), takes almost no space) when source and DEST are on the same Mac-formatted
           (APFS) disk; otherwise a real copy. One clone is tried first: if it fails, or the source is on another disk,
           the files count as real copies and free space is checked before anything is copied. A real copy is written
           as "<final name>.没拷完" and renamed when complete, so a stopped run never leaves a half file under the final
           name (a leftover .没拷完 file is overwritten next time); files that show as 1.0 GB or more (≥ 0.95e9 bytes) are
           announced before copying. Clones and copies never keep the "locked" flag of a protected original (a locked
           copy could not be renamed or archived later); half files nobody will finish are moved to _旧文件/.
mode link: Finder-visible symlinks (no space; originals must stay put).
Re-running skips files that already exist. Progress: one JSON line per event."""
import argparse
import ctypes
import glob
import json
import os
import re
import shutil
import stat
import sys
import time
import unicodedata

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


_clonefile = []


def clone(src, dst):
    """APFS clone via clonefile(2). True only if the clone was made; never falls back to a full copy
    (/bin/cp -c silently does, and still returns 0). False on another disk, a non-APFS disk, non-macOS, any error."""
    try:
        if not _clonefile:
            f = ctypes.CDLL(None, use_errno=True).clonefile
            f.argtypes, f.restype = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint32], ctypes.c_int
            _clonefile.append(f)
        return _clonefile[0](os.fsencode(src), os.fsencode(dst), 0) == 0
    except Exception:
        return False


TMP = ".没拷完"  # a real copy carries this ending until it is complete


def unlock(p):
    """Take the 'locked' flag off one of THIS TOOL'S OWN files in DEST (never an original: a symlink is not followed).
    A protected clip on a camera card shows up as locked, and a clone or copy would carry the flag along;
    a locked copy could not be renamed, archived or replaced later."""
    try:
        fl = os.lstat(p).st_flags
        if fl & stat.UF_IMMUTABLE:
            os.chflags(p, fl & ~stat.UF_IMMUTABLE, follow_symlinks=False)
    except (OSError, AttributeError, NotImplementedError):
        pass


def copy_real(src, dst):
    """Full copy under a temporary name, renamed when complete; on an error the temporary file is removed."""
    tmp = dst + TMP
    try:
        unlock(tmp)  # a leftover from a stopped run is simply overwritten
        shutil.copyfile(src, tmp)  # data only: the original's 'locked' flag must not land on the temporary file
        os.replace(tmp, dst)
    except BaseException:
        try:
            unlock(tmp)
            os.remove(tmp)
        except OSError:
            pass
        raise
    try:
        shutil.copystat(src, dst)  # dates and permissions like the original
    except OSError:
        pass
    unlock(dst)


def dev_of(p):
    try:
        return os.stat(p).st_dev
    except OSError:  # original not reachable (disk unplugged): reported as a failed file in the loop below
        return None


def size_of(p):
    try:
        return os.path.getsize(p)
    except OSError:
        return 0


GB = 1e9
BIG = 0.95 * GB  # announced before copying: shows as "1.0 GB" or more


def human(n):
    return f"{n / GB:.1f} GB" if n >= 0.05 * GB else f"{max(1, round(n / 1e6))} MB"


def clean(s, n=40):
    s = re.sub(r'[\\/:*?"<>|\n\r\t]+', " ", str(s or "")).strip().strip(".")
    return s[:n].strip()


man = read_jsonl(P["manifest"])
cls = {}
for p in sorted(glob.glob(os.path.join(P["cls"], "*.jsonl"))):
    for r in read_jsonl(p):
        cls[r["id"]] = r

def named(head, path):
    stem, ext = os.path.splitext(os.path.basename(path))
    return f"{head} ({clean(stem, 60)}){ext}".strip()


def mates(x, head):
    """The clip's RAW originals: same name start, own (name) and extension; a clash gets ' 2', ' 3'… (never overwritten)."""
    out, seen = [], set()
    for p in x.get("companions") or []:
        name = named(head, p)
        base, ext = os.path.splitext(name)
        k = 2
        while name.lower() in seen:  # APFS ignores case
            name, k = f"{base} {k}{ext}", k + 1
        seen.add(name.lower())
        out.append((os.path.realpath(p), name))
    return out


STARS = {3: "★★★", 2: "★★", 1: "★", 0: "✕"}
plan = []  # (src, [dest dirs], filename, [(RAW original src, filename)])
for x in classify_ids(man):
    c = cls.get(x["id"])
    src = os.path.realpath(x["path"])
    if not c:
        plan.append((src, ["未分类"], named(x["id"], x["path"]), mates(x, x["id"])))
        continue
    rating = c.get("rating")
    head = f"{STARS.get(rating, '')} {x['id']} {clean(c.get('desc'))}"
    name = named(head, x["path"])
    if rating == 0:
        plan.append((src, ["✕ 废片"], name, mates(x, head)))
        continue
    scene = c.get("scene") or ""
    dirs = [os.path.join("按场景", clean(scene, 30) if scene and scene != UNSURE else "场景不确定")]
    if rating == 3:
        dirs.append("★★★ 精选")
    plan.append((src, dirs, name, mates(x, head)))

# clips with no picture (RAW / unusual codec / old ffmpeg, or damaged): never classified, but must not vanish
UNREAD = "⚠ 解不出画面（没分类）"
classified = {(os.path.dirname(x["path"]), os.path.splitext(os.path.basename(x["path"]))[0].lower())
              for x in classify_ids(man) if x["id"] in cls and not x.get("raw")}  # a RAW original is not a proxy
unread = beside = lone = 0
for x in man:
    if x.get("duplicate_of") or not (x.get("extract_error") or x.get("broken")):
        continue
    if (x.get("size") or 0) < 20000:  # tiny = empty shell
        continue
    stem = os.path.splitext(os.path.basename(x["path"]))[0]
    plan.append((os.path.realpath(x["path"]), [UNREAD], named(x["id"], x["path"]), mates(x, x["id"])))
    unread += 1
    beside += (os.path.dirname(x["path"]), stem.lower()) in classified
    lone += bool(x.get("raw") and not x.get("preview"))  # RAW original with no same-name proxy and no preview picture
n_raw = sum(len(m) for _, _, _, m in plan)  # RAW originals riding along (files, not copies)
raw_single = sum(bool(x.get("raw")) for x in classify_ids(man))  # RAW originals classified from their preview picture

jobs = [(s, os.path.join(DEST, d, n)) for src, dirs, name, m in plan for d in dirs for s, n in [(src, name)] + m]

# files this tool created last time but no longer wants (labels changed after re-classifying): archive, never delete
oj = os.path.join(P["work"], "organize.json")
try:
    prev = json.load(open(oj, encoding="utf-8")) if os.path.exists(oj) else {}
except ValueError:  # cut off while being written: treated as no record, not as a reason to stop every later run
    prev = {}
want = {os.path.relpath(d, DEST) for _, d in jobs}
stale = [f for f in prev.get("files", []) if prev.get("dest") == DEST and f not in want and os.path.lexists(os.path.join(DEST, f))]
arch = os.path.join(DEST, "_旧文件", time.strftime("%Y%m%d-%H%M%S"))


def archive(p):
    """Move one of this tool's own files from DEST into _旧文件/ (same relative path). False if it cannot be moved."""
    try:
        t = os.path.join(arch, os.path.relpath(p, DEST))
        os.makedirs(os.path.dirname(t), exist_ok=True)
        unlock(p)
        os.replace(p, t)
        return True
    except OSError:
        return False


if stale:
    stuck = [f for f in stale if not archive(os.path.join(DEST, f))]
    stale = [f for f in stale if f not in stuck]  # the stuck ones stay listed in organize.json, tried again next time
    emit(message="，".join(([f"标签变了的 {len(stale)} 个旧文件挪到了 _旧文件/（没删）"] if stale else [])
                          + ([f"有 {len(stuck)} 个标签变了的旧文件挪不动，留在原处"] if stuck else [])))

# half files of a stopped real copy that no job of this run will overwrite (their final name changed since): archive too
nfc = lambda s: unicodedata.normalize("NFC", s)  # noqa: E731  (a Mac OS Extended disk hands names back decomposed)
dsts = {nfc(d) for _, d in jobs}
orphans = []
for dp, dn, fs in os.walk(DEST):
    if dp == DEST:
        dn[:] = [d for d in dn if d != "_旧文件"]
    orphans += [os.path.join(dp, f) for f in fs if f.endswith(TMP) and nfc(os.path.join(dp, f)[:-len(TMP)]) not in dsts]
if orphans:
    freed = sum(size_of(p) for p in orphans)
    moved = sum(archive(p) for p in orphans)
    if moved:
        emit(message=f"上次没拷完的 {moved} 个半截文件（共 {human(freed)}）已经用不上了，挪到了 _旧文件/，可以删掉")

todo = [(s, d) for s, d in jobs if not os.path.lexists(d)]
if args.mode == "copy" and todo:
    # a clone works only on the same Mac-formatted (APFS) disk: try one for real instead of trusting st_dev
    dev = os.stat(DEST).st_dev
    same = [(s, d) for s, d in todo if dev_of(s) == dev]
    probe = None
    if same:
        os.makedirs(os.path.dirname(same[0][1]), exist_ok=True)
        if clone(*same[0]):
            probe = same[0]
            unlock(probe[1])
    cloneable = set(same) if probe else set()  # probe failed / nothing on DEST's disk: everything is a real copy
    # a half file left by a stopped run is overwritten, so the room it takes comes back
    need = sum(max(0, size_of(s) - (size_of(d + TMP) if os.path.isfile(d + TMP) else 0))
               for s, d in todo if (s, d) not in cloneable)
    free = shutil.disk_usage(DEST).free
    if need > free * 0.95:
        if probe:
            os.remove(probe[1])
        emit(error=f"目标磁盘空间不够：需要约 {human(need)}，只剩 {human(free)}。可以把结果放到素材所在的 Mac 格式（APFS）硬盘上（那样几乎不占空间），或者改选「替身」。")
        sys.exit(2)
    if need:
        emit(message=f"这块硬盘不能用克隆（不是同一块 Mac 格式的硬盘），要真复制约 {human(need)}")
    if probe:
        todo.remove(probe)

counts = {}
for _, dirs, _, _ in plan:
    counts[dirs[0]] = counts.get(dirs[0], 0) + 1
keep = set(prev.get("files", [])) - set(stale) if prev.get("dest") == DEST else set()


def save(files):
    with open(oj + ".new", "w", encoding="utf-8") as f:  # written whole, then swapped in: never a half record
        json.dump({"dest": DEST, "mode": args.mode, "folders": counts, "files": sorted(files)}, f, ensure_ascii=False, indent=1)
    os.replace(oj + ".new", oj)


save(keep | want)  # before copying: if this run is stopped, the next one still knows which files in DEST are this tool's
emit(done=0, total=len(jobs), message=f"整理进新文件夹（{'复制' if args.mode == 'copy' else '替身'}）：{len(plan)} 条素材，{len(jobs)} 个文件"
     + (f"（含 {n_raw} 个跟着同名代理走的 RAW 原片）" if n_raw else ""))
failed = []
for k, (src, dst) in enumerate(todo, 1):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        if args.mode == "link":
            if not os.path.exists(src):  # original not reachable: a failed file, not a dead stand-in
                raise FileNotFoundError(f"找不到原文件 {src}")
            os.symlink(src, dst)
        elif clone(src, dst):  # APFS clone: no extra space
            unlock(dst)
        else:
            size = os.path.getsize(src)
            if size >= BIG:
                fname = os.path.basename(dst)
                emit(done=len(jobs) - len(todo) + k - 1, total=len(jobs), message=f"正在复制 {fname}（{size / GB:.1f} GB）…")
            copy_real(src, dst)
    except Exception as e:  # keep going; report at the end
        failed.append(f"{os.path.basename(dst)}: {e}")
    if k % 10 == 0 or k == len(todo):
        emit(done=len(jobs) - len(todo) + k, total=len(jobs))
save(f for f in keep | want if os.path.lexists(os.path.join(DEST, f)))  # what is really there now
if n_raw:
    emit(message=f"有 {n_raw} 个 RAW 原片（.R3D 等）这台电脑解不出画面，跟着相机同时录的同名文件（代理）走：代理放进哪个文件夹，原片就用同一个名字放在它旁边")
if unread:
    emit(message=f"有 {unread} 条素材这台电脑解不出画面，没法分类，原样放在「{UNREAD}」里（常见原因：RAW 或特殊编码、ffmpeg 版本太旧，或者文件没录完、没拷完）"
                 + (f"。其中 {beside} 条旁边有同名的其他格式文件（多半是相机录的代理），已分类的是那个代理，原片在这个文件夹里" if beside else "")
                 + (f"。其中 {lone} 条是没有能用的同名代理、文件里也读不出预览图的 RAW 原片" if lone else ""))
if raw_single:
    emit(message=f"有 {raw_single} 条 RAW 原片没有能用的同名代理，是按文件里自带的预览图分类的（只有开头一帧，评分和运镜仅供参考）")
emit(done=len(jobs), total=len(jobs), dest=DEST, failed=failed[:20], failed_count=len(failed), unread=unread, companions=n_raw, raw_single=raw_single,
     message=f"整理好了：{len(counts)} 个文件夹" + (f"，{n_raw} 个 RAW 原片和同名代理放在一起" if n_raw else "")
     + (f"，{unread} 条解不出画面没分类" if unread else "")
     + (f"，{len(failed)} 个文件没复制成功" if failed else ""))
sys.exit(1 if failed else 0)
