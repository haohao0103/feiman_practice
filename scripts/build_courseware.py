#!/usr/bin/env python3
"""原课程视频 → 逐字稿课件 —— 一条命令跑完。

流程：
  1. 下载原视频 1080p（yt-dlp）
  2. 场景切分（找讲义页/图表切换点）
  3. 关键画面抽取（按 ASR 时间点，评标注墨迹量挑最佳帧）
  4. 画面加工（分区裁切 + TradingView 工具栏抹除）
  5. 生成逐字稿（口播=原ASR，画面=原视频帧）
  6. 覆盖度校验（保真度 Dice + 画面对齐）

用法：
  python3 scripts/build_courseware.py            # 全量
  python3 scripts/build_courseware.py --skip-download   # 复用已下载视频
日志：logs/courseware_pipeline.log
"""
import argparse
import os
import subprocess
import sys
import time

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


HERE = os.path.join(ROOT, "scripts")
PY = sys.executable
LOG = os.path.join(ROOT, "logs", "courseware_pipeline.log")
YTDLP = os.environ.get("FEIMAN_ORIGIN_REPO", "")
VIDEO_URL = "https://www.bilibili.com/video/BV1rXXfBiECa/"
VIDEO_DIR = os.path.join(ROOT, "work", "src_video")
VIDEO = os.path.join(VIDEO_DIR, "BV1rXXfBiECa_1080p.mp4")

# ASR 源（原仓库的完整转写，已含人工校对备注）
ASR_SRC = os.environ.get("FEIMAN_ORIGIN_REPO", "")


def log(m):
    line = f"[pipe] {m}"
    print(line, flush=True)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def run(name, cmd, cwd=ROOT, timeout=1800, env=None):
    t0 = time.time()
    log(f"▶ {name}")
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       errors="replace", timeout=timeout, env=env)
    tail = (r.stdout or "").strip().splitlines()[-1][:160] if (r.stdout or "").strip() else ""
    log(f"◀ {name} rc={r.returncode} {time.time()-t0:.1f}s :: {tail}")
    if r.returncode != 0:
        log(f"  !! stderr: {(r.stderr or '')[-500:]}")
    return r.returncode == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-download", action="store_true")
    args = ap.parse_args()

    os.makedirs(VIDEO_DIR, exist_ok=True)
    t_all = time.time()
    log("=" * 64)
    log("COURSEWARE PIPELINE START")

    steps = []
    if not args.skip_download or not os.path.exists(VIDEO):
        ok = run("download_1080p", [YTDLP, "-f", "30080", "-o", VIDEO, VIDEO_URL],
                 timeout=600)
        steps.append(("download", ok, time.time()))
        if not ok:
            log("下载失败，中止")
            return 1
    else:
        log(f"跳过下载（复用 {VIDEO}）")

    plan = [
        ("scene_split", "orig_scene_split.py"),
        ("extract_frames", "extract_course_frames.py"),
        ("clean_frames", "clean_course_frames.py"),
        ("build_script", "build_script_from_video.py"),
        ("coverage_check", "check_script_coverage.py"),
    ]
    failed = []
    for name, script in plan:
        t0 = time.time()
        ok = run(name, [PY, os.path.join(HERE, script)])
        steps.append((name, ok, time.time() - t0))
        if not ok:
            failed.append(name)
            # 覆盖率校验失败不阻断，但要报出来
            if name == "coverage_check":
                log("  （覆盖率校验未通过，但不阻断产出）")

    total = time.time() - t_all
    log("=" * 64)
    log("--- 阶段耗时 ---")
    for n, ok, dt in steps:
        log(f"  {n:<16} {dt:>7.1f}s  {'OK' if ok else 'FAIL'}")
    log(f"  {'TOTAL':<16} {total:>7.1f}s")
    if failed:
        log(f"FAILED: {failed}")
        log("=== PIPELINE COMPLETE WITH FAILURES")
        return 1
    log("=== PIPELINE COMPLETE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
