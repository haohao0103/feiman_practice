#!/usr/bin/env python3
"""从原课程视频抽取课件用画面（讲义页 + K线图 + 讲师手绘标注）。

原则：**画面全部来自原课程视频**，不自制任何图。
方法：按 ASR 段落的关键时间点抽帧，每段抽多张候选，
      用"标注墨迹量"评分挑出标注最完整的一帧。

输出：work/course_frames/*.jpg
      work/course_frames_index.json
日志：logs/course_frames.log
"""
import shutil
import json
import os
import subprocess
import time

import numpy as np
from PIL import Image

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


FFMPEG = (os.environ.get("FEIMAN_FFMPEG") or shutil.which("ffmpeg") or
          "/Users/mac/.workbuddy/binaries/python/versions/3.14.3/lib/"
          "python3.14/site-packages/imageio_ffmpeg/binaries/ffmpeg-macos-x86_64-v7.1")
SRC = os.path.join(ROOT, "work", "src_video", "BV1rXXfBiECa_1080p.mp4")
OUT = os.path.join(ROOT, "work", "course_frames")
LOG = os.path.join(ROOT, "logs", "course_frames.log")
IDX = os.path.join(ROOT, "work", "course_frames_index.json")

# 关键画面需求（依据 ASR 时间戳）：
#   key       : 画面 id
#   at        : 时间戳列表（秒），在这些点抽候选帧
#   note      : 用途说明
SHOTS = [
    {"key": "F01_讲义页_五步骤总览", "at": [3, 8, 14, 22], "note": "第一章 反转的五个步骤 · 五步骤列表"},
    # 讲义页在多段被复用，但画面状态必须对应各自段落的时间点，
    # 因此同一页拆成多个 key（否则会出现"讲第二步却显示第一步的画面"）
    {"key": "F01b_讲义页_遇阻段", "at": [26, 30, 36, 40], "note": "讲义页 · 第一步遇阻时画面状态"},
    {"key": "F01c_讲义页_极点段", "at": [44, 48, 54, 58], "note": "讲义页 · 第二步测试极点时画面状态"},
    {"key": "F02_例1_双底反转_遇阻", "at": [96, 100, 104], "note": "例1 双底反转 · 第一步遇阻"},
    {"key": "F03_例1_双底反转_画趋势线", "at": [86, 92, 97], "note": "例1 · 讲师手绘下跌趋势线"},
    {"key": "F04_例1_双底反转_二次测试", "at": [136, 138, 145, 155], "note": "例1 · 第二步二次测试极点"},
    {"key": "F05_例1_双底反转_破趋势线", "at": [130, 133, 136, 140, 144, 148, 152], "note": "例1 · 第三步突破趋势线"},
    {"key": "F06_例1_双底反转_主要低点", "at": [160, 168, 176], "note": "例1 · 第四步突破主要高点/低点"},
    {"key": "F07_例1_双底反转_五步齐", "at": [190, 196, 205], "note": "例1 · 第五步高点上移/低点下移"},
    {"key": "F08_五步骤回顾", "at": [218, 224, 232], "note": "回顾五步骤"},
    {"key": "F09_三种归宿说明", "at": [258, 264, 270], "note": "反转未必形成新趋势，可能进区间或深度回调"},
    {"key": "F10_例2_双顶_三根大阳线", "at": [276, 280, 288], "note": "例2 双顶反转 · 左边三根强阳线"},
    {"key": "F11_例2_双顶_长下影线", "at": [300, 308, 316], "note": "例2 · 长下影大阳线=遇阻"},
    {"key": "F12_例2_双顶_二次测试", "at": [330, 338, 346], "note": "例2 · 二次测试极点"},
    {"key": "F13_例2_双顶_大阴线分水岭", "at": [392, 396, 404], "note": "例2 · ④大阴线=空头力量表现"},
    {"key": "F14_例2_双顶_破主要低点", "at": [410, 416, 424], "note": "例2 · 跌破主要低点+低点下移"},
    {"key": "F15_例2_双顶_趋势线", "at": [432, 440, 448], "note": "例2 · 73号K线首次跌破趋势线"},
    {"key": "F16_例3_通道转区间", "at": [480, 490, 500], "note": "例3 反转然后进入交易区间"},
    {"key": "F17_例3_无高点上移", "at": [526, 532, 538], "note": "例3 · 没有形成高点上移→进区间"},
    {"key": "F18_例4_三推_通道线", "at": [570, 580, 590], "note": "例4 三推 · 急速接通道，标1/2/3"},
    {"key": "F19_例4_三推_破趋势线", "at": [606, 612, 618], "note": "例4 · 第三次推动后突破趋势线"},
    {"key": "F20_例4_三推_低点下移", "at": [616, 620, 626], "note": "例4 · 低点持续下移→新空头趋势"},
    {"key": "F21_总结_五步骤回顾", "at": [622, 628, 634, 640, 646, 652, 658, 664], "note": "四例总结 · 五步骤回顾"},
    {"key": "F22_总结_80%失败率", "at": [700, 704, 708, 712, 716, 720], "note": "80%反转尝试失败（缺条件前缀）"},
    {"key": "F23_结尾_复盘建议", "at": [716, 722, 726, 730, 734, 738], "note": "结尾 · 对着图表复盘"},
]


def log(m):
    line = f"[cframe] {m}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def grab(t, out_path):
    cmd = [FFMPEG, "-y", "-hide_banner", "-v", "error",
           "-ss", f"{t:.2f}", "-i", SRC, "-frames:v", "1", "-q:v", "2", out_path]
    subprocess.run(cmd, capture_output=True, text=True)
    return os.path.exists(out_path)


def annotation_score(path):
    """画面价值评分：兼顾「手绘标注量」与「图表可见高度」。

    只看标注量会选错：有些帧讲义页占 900px、图表只露底部 160px，
    标注多但交付出去是一条残图。
    因此：
      1) 计算下半区（图表区）的标注墨迹量
      2) 计算黑底图表块的总高度
      3) 两者相乘 —— 标注多**且**图表露出多的帧才得高分
    """
    im = Image.open(path).convert("RGB")
    w, h = im.size
    gray = np.asarray(im.convert("L"))
    lower = np.asarray(im.crop((0, int(h * 0.45), w, h))).astype(np.int16)
    r, g, b = lower[..., 0], lower[..., 1], lower[..., 2]
    mx = lower.max(axis=2)
    mn = lower.min(axis=2)
    # 黄色标注: r,g 高且接近, b 明显低
    yellow = (r > 140) & (g > 120) & (b < 110) & ((r - b) > 70) & ((g - b) > 50)
    # 白色细线
    white = (mn > 190)
    ink = float(yellow.sum()) * 3.0 + float(white.sum()) * 0.3

    # 图表可见高度：下半区中"暗行"的总跨度
    row_mean = gray.mean(axis=1)
    dark_rows = np.where(row_mean[int(h * 0.45):] < 90)[0]
    chart_h = float(len(dark_rows)) if len(dark_rows) else 0.0

    # 归一化后相乘：标注量 × 图表可见度
    ink_n = min(ink / 260000.0, 3.0)
    chart_n = min(chart_h / h * 2.2, 1.0)
    return ink * chart_n


def _chart_height(path):
    """该帧黑底图表的总高度（像素）。"""
    im = Image.open(path).convert("L")
    w, h = im.size
    row_mean = np.asarray(im).mean(axis=1)
    dark = row_mean[int(h * 0.45):] < 90
    return int(dark.sum())


def main():
    log("=== course frames start")
    os.makedirs(OUT, exist_ok=True)
    for f in os.listdir(OUT):
        os.remove(os.path.join(OUT, f))
    index = []
    t0 = time.time()
    for shot in SHOTS:
        cands = []
        for t in shot["at"]:
            p = os.path.join(OUT, f"_cand_{shot['key']}_{int(t)}.jpg")
            if grab(t, p):
                s = annotation_score(p)
                cands.append({"t": t, "path": p, "score": round(s, 1),
                              "chart_h": _chart_height(p)})
        if not cands:
            log(f"  ✗ {shot['key']} 全部抽帧失败")
            continue
        # 选帧：优先图表露得最多的（内容主体），同高度内取标注最多的
        best = max(cands, key=lambda c: (c["chart_h"], c["score"]))
        final = os.path.join(OUT, f"{shot['key']}.jpg")
        os.replace(best["path"], final)
        # 清理其它候选
        for c in cands:
            if c["path"] != best["path"] and os.path.exists(c["path"]):
                os.remove(c["path"])
        index.append({
            "key": shot["key"], "file": final,
            "time_sec": best["t"], "score": best["score"],
            "chart_h": best["chart_h"], "note": shot["note"],
            "candidates": [(c["t"], c["score"], c["chart_h"]) for c in cands],
        })
        log(f"  {shot['key']:<32} t={best['t']:>4}s score={best['score']:>9.1f}  {shot['note']}")
    log(f"全部完成 wall={time.time()-t0:.1f}s  共 {len(index)} 张")
    with open(IDX, "w", encoding="utf-8") as fh:
        json.dump({"source": SRC, "frames": index}, fh, ensure_ascii=False, indent=1)
    log("=== course frames done")


if __name__ == "__main__":
    main()
