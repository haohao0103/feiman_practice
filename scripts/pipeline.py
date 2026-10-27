#!/usr/bin/env python3

"""讲课视频评估流水线 —— 一条命令跑完全部阶段。

用法：
  python3 scripts/pipeline.py                      # 全量（复用已有 ASR）
  python3 scripts/pipeline.py --force              # 清缓存重跑
  python3 scripts/pipeline.py --only metrics,scene # 只跑指定阶段
  python3 scripts/pipeline.py --skip media         # 跳过指定阶段
  python3 scripts/pipeline.py --dry-run             # 只打印计划，不执行
  python3 scripts/pipeline.py --parallel            # 无依赖阶段并发
  python3 scripts/pipeline.py --benchmark           # 跑完输出各阶段耗时表

设计要点（提速）：
  1. 阶段化 DAG —— 每个阶段独立、可单独重跑、可断点续跑
  2. 输入指纹缓存 —— 输入没变就跳过，结果命中即 0 成本
  3. 并发执行 —— 无依赖阶段用线程池同时跑
  4. 配置外置 —— config.json / lexicon.json，改口径不改代码
  5. 全程日志 —— 每阶段耗时 + 尾行完成标记写 logs/pipeline.log

日志：ROOT/logs/pipeline.log
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import os

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
LOG = os.path.join(ROOT, "logs", "pipeline.log")
_print_lock = threading.Lock()


# ---------------------------------------------------------------- 基础设施
def load_json(p, default=None):
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default if default is not None else {}


def log(msg):
    line = f"[{datetime.now():%H:%M:%S}] {msg}"
    with _print_lock:
        print(line, flush=True)
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def fingerprint(paths):
    """输入指纹：文件大小 + mtime + 路径。变了就重算。目录取其内文件集合摘要。"""
    h = hashlib.md5()
    for p in sorted(paths):
        if os.path.isdir(p):
            # 目录：取文件名 + 数量 + 目录 mtime，避免把目录当文件读
            try:
                entries = sorted(os.listdir(p))
                h.update(f"{p}:DIR:{len(entries)}:{int(os.stat(p).st_mtime)}".encode())
                for e in entries[:5]:
                    fp = os.path.join(p, e)
                    if os.path.isfile(fp):
                        st = os.stat(fp)
                        h.update(f"|{e}:{st.st_size}".encode())
            except OSError as e:
                h.update(f"{p}:DIRERR:{e}".encode())
        elif os.path.exists(p):
            st = os.stat(p)
            h.update(f"{p}:{st.st_size}:{int(st.st_mtime)}".encode())
        else:
            h.update(f"{p}:MISSING".encode())
    return h.hexdigest()[:16]


def cache_path(name):
    d = os.path.join(ROOT, "work", ".cache")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{name}.json")


def cache_get(name, fp, force=False):
    if force:
        return None
    c = load_json(cache_path(name), None)
    if c and c.get("fp") == fp:
        return c.get("result", {})
    return None


def cache_put(name, fp, result):
    with open(cache_path(name), "w", encoding="utf-8") as fh:
        json.dump({"fp": fp, "ts": datetime.now().isoformat(),
                   "result": result}, fh, ensure_ascii=False, indent=1)


def run_stage(name, cmd, cwd=ROOT, timeout=1800):
    """执行外部脚本。判据=日志时间戳+非零长度+尾行完成标记，不靠 exit code。"""
    t0 = time.time()
    log(f"▶ STAGE {name} START :: {' '.join(cmd[:3])}")
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           errors="replace", timeout=timeout)
        out = r.stdout or ""
        err = r.stderr or ""
        ok = r.returncode == 0
        detail = ""
        if out.strip():
            detail = out.strip().splitlines()[-1][:200]
        elif err.strip():
            detail = "ERR: " + err.strip().splitlines()[-1][:200]
        log(f"◀ STAGE {name} END   rc={r.returncode} {time.time()-t0:.1f}s :: {detail}")
        if not ok:
            log(f"  !! {name} stderr tail: {err[-600:]}")
        return ok, time.time() - t0, detail
    except subprocess.TimeoutExpired:
        log(f"◀ STAGE {name} TIMEOUT after {timeout}s")
        return False, time.time() - t0, "timeout"
    except Exception as e:
        log(f"◀ STAGE {name} EXC {e}")
        return False, time.time() - t0, str(e)[:200]


# ---------------------------------------------------------------- 阶段定义
def build_stages(cfg, lex, args):
    """返回 {stage_name: (cmd, fp_inputs, deps)}"""
    work = cfg["paths"]["work"]
    src = cfg["source"]["video"]
    asr_raw = cfg["source"]["asr_raw"]
    reuse = cfg["asr"].get("reuse_from") or ""
    meta = os.path.join(work, "probe_meta.json")
    segs = os.path.join(work, "asr_segments.json")

    S = {}
    # -- 媒体层
    S["media"] = ([PY, os.path.join(HERE, "probe_media.py")],
                  [src], [])
    # -- ASR 层（外部工具，此处仅做分片/合并的落地准备）
    if cfg["asr"].get("enable_chunking"):
        S["asr_split"] = ([PY, os.path.join(HERE, "asr_chunk.py"), "--split"],
                          [src], ["media"])
    S["parse"] = ([PY, os.path.join(HERE, "parse_asr.py"), asr_raw],
                  [asr_raw], [])
    # -- 分析层（全部依赖 segs）
    for name, script in [("metrics", "analyze_metrics.py"),
                         ("visual", "visual_structure.py"),
                         ("scene", "scene_timeline.py"),
                         ("repetition", "repetition.py"),
                         ("feynman", "feynman_score.py"),
                         ("volume", "volume_check.py"),
                         ("audio_quality", "audio_quality.py")]:
        S[name] = ([PY, os.path.join(HERE, script)],
                   [segs, os.path.join(work, "frames")], ["parse"])
    # -- 出图
    S["figures"] = ([PY, os.path.join(HERE, "make_figures.py")],
                    [os.path.join(work, "metrics.json"),
                     os.path.join(work, "feynman_scores.json")], ["metrics", "feynman"])
    # -- 报告
    S["report"] = ([PY, os.path.join(HERE, "render_report.py")],
                   [os.path.join(work, "metrics.json"),
                    os.path.join(work, "feynman_scores.json"),
                    os.path.join(work, "volume_check.json"),
                    os.path.join(work, "visual_stats.json"),
                    os.path.join(work, "repetition.json"),
                    os.path.join(work, "audio_quality.json")],
                   ["metrics", "visual", "scene", "repetition", "feynman",
                    "volume", "audio_quality"])
    return S


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="忽略缓存全部重跑")
    ap.add_argument("--only", default="", help="只跑这些阶段，逗号分隔")
    ap.add_argument("--skip", default="", help="跳过这些阶段")
    ap.add_argument("--parallel", action="store_true", help="并发跑无依赖阶段")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划")
    ap.add_argument("--benchmark", action="store_true", help="输出各阶段耗时表")
    args = ap.parse_args()

    cfg = load_json(os.path.join(HERE, "config.json"))
    lex = load_json(os.path.join(HERE, "lexicon.json"))
    if not cfg:
        log("FATAL: config.json 缺失或损坏")
        return 2
    # paths 允许写相对路径（相对仓库根）→ 统一解析成绝对路径
    for _k in ("root", "work", "logs", "outputs", "images"):
        _v = (cfg.get("paths") or {}).get(_k)
        if _v and not os.path.isabs(_v):
            cfg["paths"][_k] = os.path.join(ROOT, _v)
    cfg["analysis"]["parallel"] = cfg["analysis"]["parallel"] or args.parallel

    only = set(x for x in args.only.split(",") if x)
    skip = set(x for x in args.skip.split(",") if x)
    stages_cfg = cfg["analysis"]["stages"]

    S = build_stages(cfg, lex, args)
    plan = []
    for name, (cmd, inputs, deps) in S.items():
        if only and name not in only:
            continue
        if name in skip:
            continue
        if name in stages_cfg and not stages_cfg[name] and name not in ("media",):
            continue
        plan.append((name, cmd, inputs, deps))

    # 依赖裁剪：只保留实际需要的上游
    needed = set()
    changed = True
    while changed:
        changed = False
        for name, _, _, deps in plan:
            if name in needed:
                continue
            needed.add(name)
            for d in deps:
                if d not in needed:
                    changed = True

    # 拓扑排序
    ordered = []
    placed = set()
    while len(ordered) < len([p for p in plan if p[0] in needed]):
        for name, cmd, inputs, deps in plan:
            if name not in needed or name in placed:
                continue
            if all(d in placed for d in deps if d in needed):
                ordered.append((name, cmd, inputs, deps))
                placed.add(name)
        if len(placed) == len([p for p in plan if p[0] in needed]):
            break

    log("=" * 72)
    log(f"PIPELINE START  stages={len(ordered)}  parallel={cfg['analysis']['parallel']}"
        f"  force={args.force}")
    for n, c, i, d in ordered:
        log(f"  plan: {n:<16} deps={d or '-'}")

    if args.dry_run:
        log("DRY RUN —— 未执行")
        return 0

    t_all = time.time()
    results = {}
    timings = []
    done = set()

    def execute(item):
        name, cmd, inputs, deps = item
        if any(d not in done for d in deps if d in needed):
            return name, None, 0, "skipped(dep)"
        fp = fingerprint(inputs)
        if cfg["cache"]["enabled"]:
            hit = cache_get(name, fp, args.force)
            if hit is not None:
                log(f"⚡ STAGE {name} CACHE HIT ({fp}) —— 跳过")
                return name, hit, 0.0, "cache"
        ok, dt, detail = run_stage(name, cmd)
        if not ok:
            return name, None, dt, f"failed: {detail}"
        out = {}
        for cand in (os.path.join(cfg["paths"]["work"], f"{name}.json"),
                     os.path.join(cfg["paths"]["work"], "probe_meta.json")):
            if os.path.exists(cand):
                out[cand] = os.path.getmtime(cand)
        if cfg["cache"]["enabled"]:
            cache_put(name, fp, {"ok": True, "detail": detail})
        return name, {"ok": True}, dt, detail

    if cfg["analysis"]["parallel"]:
        # 按依赖分层，同层并发
        layer = 0
        while len(done) < len(ordered):
            layer_items = [(n, c, i, d) for n, c, i, d in ordered
                           if n not in done and all(x in done for x in d if x in needed)]
            if not layer_items:
                break
            log(f"--- layer {layer}: {[x[0] for x in layer_items]}")
            with ThreadPoolExecutor(max_workers=min(6, len(layer_items))) as ex:
                futs = [ex.submit(execute, it) for it in layer_items]
                for f in futs:
                    n, r, dt, d = f.result()
                    done.add(n)
                    results[n] = (r, d)
                    timings.append((n, dt, d))
            layer += 1
    else:
        for it in ordered:
            n, r, dt, d = execute(it)
            done.add(n)
            results[n] = (r, d)
            timings.append((n, dt, d))

    total = time.time() - t_all
    failed = [n for n, (r, d) in results.items() if not r]
    log("=" * 72)
    log(f"PIPELINE END  total={total:.1f}s  failed={failed or 'none'}")
    log("--- 阶段耗时表 ---")
    for n, dt, d in timings:
        mark = "  <<< FAILED" if n in failed else ""
        log(f"  {n:<16} {dt:>7.1f}s  {d[:60]}{mark}")
    log(f"  {'TOTAL':<16} {total:>7.1f}s")
    if failed:
        log(f"!!! {len(failed)} STAGE(S) FAILED: {failed}")
        log("=== PIPELINE COMPLETE WITH FAILURES")
    else:
        log("=== PIPELINE COMPLETE")

    if args.benchmark:
        print("\n阶段耗时表（秒）")
        for n, dt, d in timings:
            mark = "  <<< FAILED" if n in failed else ""
            print(f"  {n:<16} {dt:>7.1f}  {d[:50]}{mark}")
        print(f"  {'TOTAL':<16} {total:>7.1f}")
    if failed:
        print(f"\nPIPELINE_FAILED: {failed}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
