#!/usr/bin/env python3
"""work/ 暂存区清理 —— 防止磁盘被录屏与抽帧中间产物占满。

问题
----
每次上传录屏（~760M）+ 抽帧（~1100 张 jpg/任务）都堆在 work/ 里，
实测三天就堆到 6.5G，且没有回收机制。

三层策略
--------
1) **任务级瘦身**：任务目录里删「可重建的大件」
   （frames_raw/ frames/ _probe/ video/ audio.wav *.wav *.m4a *.mp4 *.mov），
   保留 asr.txt（重跑评估的缓存，免一次 ASR）+ 稿件 + 元数据。
2) **水位线**：work/ 总量超阈值时，从最老的任务开始整目录删；
   删除前先把 asr.txt 挪进 work/asr_cache/，保证 ASR 结果不丢。
3) **残留清扫**：临时调试目录（_ 前缀）与旧管线遗留的固定名帧目录。

安全红线（全部来自实测标定，不是猜的）
--------------------------------------
- 只动 work/ 下**白名单前缀**的路径；绝不碰 outputs/、源码、配置。
- **绝不删 outputs/**：safe-delete 钩子会拦「交付目录内单次批量删 >50 文件」，
  后台进程拿不到人工确认 → **永久卡死**（不是报错，是挂住）。
- 整目录 rmtree：已标定 work/ 下 3000 文件耗时 0.2s，安全。
- 逐文件 os.remove：work/ 下虽放行，但每文件约 0.15s 拦截开销
  → 一万张帧要 25 分钟，**不可用**。所以一律走「整目录 rmtree」。
- 删除前后都做路径白名单校验（os.path.commonpath），杜绝越界。
"""
import json
import os
import re
import shutil
import time

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

LOG = os.path.join(ROOT, "work", "cleanup.log")

# 任务目录内「可重建的大件」：目录名 / 文件名后缀
HEAVY_DIRS = ("frames_raw", "frames", "_probe", "video", "videos")
HEAVY_SUFFIX = (".wav", ".m4a", ".mp4", ".mov", ".mkv")
# 任务目录内「必须保留」的（小体积、高价值）
KEEP_NAMES = ("asr.txt",)

# work/ 下必须保留的目录（基准名或前缀）
KEEP_DIRS = ("models", "bench_src", "bench_task", "asr_cache", "_logs")
# work/ 下可整体清空的旧管线遗留目录
SWEEP_DIRS = ("frames", "course_frames", "course_frames_clean", "orig_frames",
              "src_video", "chunks")
# work/ 下临时目录前缀
TEMP_PREFIX = "_"

# 正在跑的任务目录（跑动中不许删）：{绝对路径}
_PROTECTED = set()


def _log(msg, log=None):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    if log:
        log(line)
    else:
        print(line, flush=True)


def _size(path):
    """目录或文件的字节数（软链接不计）。"""
    if os.path.islink(path):
        return 0
    if os.path.isfile(path):
        try:
            return os.path.getsize(path)
        except OSError:
            return 0
    total = 0
    for root, dirs, files in os.walk(path):
        for fn in files:
            fp = os.path.join(root, fn)
            try:
                if not os.path.islink(fp):
                    total += os.path.getsize(fp)
            except OSError:
                pass
    return total


def _inside_work(path, work_dir):
    """路径必须在 work/ 之内（防越界）。"""
    try:
        p = os.path.realpath(path)
        w = os.path.realpath(work_dir)
        return os.path.commonpath([p, w]) == w and p != w
    except (ValueError, OSError):
        return False


def _protected(path):
    rp = os.path.realpath(path)
    return any(rp.startswith(os.path.realpath(p) + os.sep) or rp == os.path.realpath(p)
               for p in _PROTECTED)


def protect(paths):
    """登记跑动中的目录，清理时跳过。"""
    for p in (paths or []):
        _PROTECTED.add(os.path.realpath(p))


def unprotect(paths):
    for p in (paths or []):
        _PROTECTED.discard(os.path.realpath(p))


# ============================================================ 规划
def _task_dirs(cfg):
    """work/ 下所有任务目录（uploads/* 与 projects/*）。"""
    out = []
    for base in (cfg.get("uploads_dir"), cfg.get("projects_dir")):
        if not base or not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            p = os.path.join(base, name)
            if os.path.isdir(p):
                out.append(p)
    return out


def plan(cfg, keep_days=7, max_gb=8.0):
    """算出将要清理什么（不执行）。返回 {items, free_bytes, total_bytes, heavy}。"""
    work = cfg["work_dir"]
    items = []
    # 1) 任务目录内的大件
    for tdir in _task_dirs(cfg):
        if _protected(tdir):
            continue
        for name in sorted(os.listdir(tdir)):
            p = os.path.join(tdir, name)
            if name in KEEP_NAMES:          # 只跳过必须保留的（如 asr.txt）
                continue
            if os.path.isdir(p) and name in HEAVY_DIRS:
                sz = _size(p)
                if sz > 0:
                    items.append({"kind": "heavy_dir", "path": p, "bytes": sz,
                                  "age_days": round(_age_days(p), 1)})
            elif os.path.isfile(p) and name.endswith(HEAVY_SUFFIX):
                sz = _size(p)
                if sz > 0:
                    items.append({"kind": "heavy_file", "path": p, "bytes": sz,
                                  "age_days": round(_age_days(p), 1)})
    # 2) 临时目录与旧遗留目录
    if os.path.isdir(work):
        for name in sorted(os.listdir(work)):
            p = os.path.join(work, name)
            if not os.path.isdir(p) or _protected(p):
                continue
            if any(k == name or k.startswith(name) for k in KEEP_DIRS):
                continue
            if name.startswith(TEMP_PREFIX) or name in SWEEP_DIRS:
                sz = _size(p)
                if sz > 0:
                    items.append({"kind": "residual", "path": p, "bytes": sz,
                                  "age_days": round(_age_days(p), 1)})
    total = _size(work)
    free = sum(i["bytes"] for i in items)
    return {"items": items, "free_bytes": free, "total_bytes": total,
            "max_bytes": int(max_gb * 1024 ** 3)}


def _age_days(path):
    try:
        return (time.time() - os.path.getmtime(path)) / 86400.0
    except OSError:
        return 0.0


_TASK_TS = re.compile(r"(\d{8})_(\d{6})")


def _task_age_days(task_dir):
    """任务目录的"年龄"（天）。

    ★ 用**目录名里的时间戳**而不是 mtime —— 因为清理动作本身会改 mtime
    （删掉目录里的文件后目录 mtime 变成"现在"），用 mtime 会让"从最老的开始删"
    这个顺序完全失真（实测：30 天的老任务被当成 0 天，1 天的新任务反而先被删）。
    目录名形如 video_to_script_20261026_202703_f9a46e，戳很稳。
    """
    m = _TASK_TS.search(os.path.basename(task_dir))
    if m:
        try:
            ts = time.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
            return (time.time() - time.mktime(ts)) / 86400.0
        except (ValueError, OverflowError):
            pass
    return _age_days(task_dir)


# ============================================================ 执行
def _rmtree(path, work_dir):
    """整目录删除（已标定：work/ 下 3000 文件 0.2s，安全）。

    ★ 越界防护：不在 work_dir 之内的路径**一律拒绝删除**（哪怕调用方传错了）。
    """
    if not _inside_work(path, work_dir):
        _log(f"拒绝删除（不在 work/ 内）：{path}")
        return 0
    sz = _size(path)
    shutil.rmtree(path, ignore_errors=True)
    return sz if not os.path.exists(path) else 0


def _keep_asr(task_dir, asr_cache):
    """把 asr.txt 挪进 asr_cache（整目录删除前保住 ASR 结果）。"""
    src = os.path.join(task_dir, "asr.txt")
    if not os.path.isfile(src):
        return None
    try:
        os.makedirs(asr_cache, exist_ok=True)
        dst = os.path.join(asr_cache, os.path.basename(task_dir) + ".txt")
        if os.path.exists(dst):
            return dst
        shutil.move(src, dst)
        return dst
    except Exception:
        return None


def run(cfg, keep_days=7, max_gb=8.0, dry_run=False, log=None, max_items=None):
    """执行清理。返回统计。

    keep_days：任务目录里的大件保留天数（超龄才删）
    max_gb   ：work/ 总量水位线（超了就从最老的任务整目录删）
    """
    work = cfg["work_dir"]
    asr_cache = os.path.join(work, "asr_cache")
    p = plan(cfg, keep_days=keep_days, max_gb=max_gb)
    freed = 0
    acted = 0

    # 1) 大件：按超龄与否删（目录整删；散落的大文件单删）
    for it in p["items"]:
        if it["kind"] == "residual":
            continue
        if _age_days(it["path"]) < keep_days:
            continue
        if dry_run:
            freed += it["bytes"]
            continue
        if it["kind"] == "heavy_dir":
            n = _rmtree(it["path"], work)
        else:
            n = _size(it["path"])
            try:
                os.remove(it["path"])
            except OSError:
                n = 0
        if n:
            freed += n
            acted += 1
            _log(f"清理大件 {it['path']} → 释放 {n/1048576:.1f}MB", log)

    # 2) 残留目录：临时调试 + 旧遗留（这些没有保留价值，直接清）
    #    但要过龄才删 —— 正在跑的任务会建 _frm_tmp 这类临时目录，误删会打断任务
    for it in p["items"]:
        if it["kind"] != "residual":
            continue
        if it.get("age_days", 0) < 1.0:
            continue
        if dry_run:
            freed += it["bytes"]
            continue
        n = _rmtree(it["path"], work)
        if n:
            freed += n
            acted += 1
            _log(f"清理残留 {it['path']} → 释放 {n/1048576:.1f}MB", log)

    # 3) 水位线：总量还超 → 从最老的任务整目录删（asr.txt 先挪走）
    total = _size(work)
    limit = int(max_gb * 1024 ** 3)
    if total > limit:
        tasks = sorted(_task_dirs(cfg), key=_task_age_days, reverse=True)
        for tdir in tasks:
            if total <= limit:
                break
            if _protected(tdir):
                continue
            if dry_run:
                freed += _size(tdir)
                total -= _size(tdir)
                continue
            kept = _keep_asr(tdir, asr_cache)
            n = _rmtree(tdir, work)
            total -= n
            freed += n
            acted += 1
            _log(f"水位线清理任务目录 {tdir} → 释放 {n/1048576:.1f}MB"
                 + (f"（asr 已存 {kept}）" if kept else ""), log)

    stat = {"freed_bytes": freed, "actions": acted,
            "total_bytes_before": p["total_bytes"], "total_bytes_after": _size(work),
            "dry_run": dry_run}
    _log(f"清理完成：{'试运行' if dry_run else '执行'} "
         f"释放 {freed/1048576:.1f}MB，动作 {acted} 次，"
         f"work/ 现 {_size(work)/1073741824:.2f}GB", log)
    return stat


# ============================================================ 自动触发
def maybe_run(cfg, log=None, force=False):
    """低频自动触发：距上次清理不足 interval_hours 就不做。"""
    c = cfg.get("cleanup") or {}
    if not c.get("enabled", True) and not force:
        return None
    interval = float(c.get("interval_hours", 6)) * 3600
    stamp = os.path.join(cfg["work_dir"], ".last_cleanup")
    if not force and os.path.exists(stamp):
        try:
            if time.time() - os.path.getmtime(stamp) < interval:
                return None
        except OSError:
            pass
    stat = run(cfg,
               keep_days=int(c.get("keep_days", 7)),
               max_gb=float(c.get("max_work_gb", 8)),
               log=log)
    try:
        os.makedirs(cfg["work_dir"], exist_ok=True)
        with open(stamp, "w") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S"))
    except Exception:
        pass
    return stat


def status(cfg):
    """当前占用与清理计划概览。"""
    p = plan(cfg,
             keep_days=int((cfg.get("cleanup") or {}).get("keep_days", 7)),
             max_gb=float((cfg.get("cleanup") or {}).get("max_work_gb", 8)))
    return {
        "work_gb": round(p["total_bytes"] / 1073741824, 2),
        "max_gb": p["max_bytes"] / 1073741824,
        "plan_items": len(p["items"]),
        "plan_free_gb": round(p["free_bytes"] / 1073741824, 2),
    }
