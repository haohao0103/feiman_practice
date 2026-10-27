#!/usr/bin/env python3
"""原视频画面加工 —— 把 1080p 帧变成可直接用作课件的干净画面。

处理三件事：
  1. **分区裁切**：原视频画面 = 上半"讲义页(白底)" + 下半"K线图(黑底)"，
     一张帧里常有多个区块。按白/黑区块自动切分，产出：
       - 讲义页（白底，含标题和步骤列表）
       - K线图（黑底，含讲师手绘标注）
  2. **工具栏抹除**：TradingView 浮动工具栏、右下角缩放条、左上角
     "1440 x 900 pt" 尺寸标尺 —— 全部检测并用邻域像素覆盖。
  3. **水印保留**：UP 主自己的 bilibili 水印保留（内容归属原视频的证明），
     但不遮挡正文。

输出：work/course_frames_clean/*.jpg
      work/course_frames_clean_index.json
日志：logs/course_frames_clean.log
"""
import json
import os

import numpy as np
from PIL import Image

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


SRC_DIR = os.path.join(ROOT, "work", "course_frames")
OUT_DIR = os.path.join(ROOT, "work", "course_frames_clean")
IDX = os.path.join(ROOT, "work", "course_frames_clean_index.json")
LOG = os.path.join(ROOT, "logs", "course_frames_clean.log")


def log(m):
    line = f"[clean] {m}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def find_blocks(gray):
    """按行平均亮度找黑底(K线图)区块。白底讲义页 = 亮行。"""
    row_mean = gray.mean(axis=1)
    dark = row_mean < 90
    blocks = []
    start = None
    for i, d in enumerate(dark):
        if d and start is None:
            start = i
        elif not d and start is not None:
            if i - start > 40:            # 黑带至少 40px 高才算是图表
                blocks.append((start, i))
            start = None
    if start is not None and len(dark) - start > 40:
        blocks.append((start, len(dark)))
    return blocks


def find_toolbar(gray):
    """检测右下角 TradingView 浮动工具栏。

    工具栏特征：位于右下角，是一个**水平细长**的高频纹理矩形，
    且内部含**彩色**像素（画笔色块）。
    判据：
      1) 在右下区域找高频行（行间差分大）
      2) 在该行带内找高频列
      3) 要求区域内存在饱和度高的像素（彩色画笔条）
    """
    h, w = gray.shape
    x0, y0 = int(w * 0.55), int(h * 0.78)
    reg = gray[y0:, x0:].astype(np.float32)
    if reg.size == 0:
        return None
    row_var = np.abs(np.diff(reg, axis=0)).mean(axis=1)
    hot_rows = np.where(row_var > 6)[0]
    if len(hot_rows) == 0:
        return None
    top = y0 + int(hot_rows.min())
    bottom = y0 + int(hot_rows.max()) + 2

    # 在该行带内找高频列
    strip = gray[top:bottom, x0:].astype(np.float32)
    col_var = np.abs(np.diff(strip, axis=1)).mean(axis=0)
    hot_cols = np.where(col_var > 6)[0]
    if len(hot_cols) == 0:
        return None
    left = x0 + int(hot_cols.min())
    right = x0 + int(hot_cols.max()) + 2

    # 必须是"细长条"：宽度占比 < 45%，高度占比 < 25%
    bw = right - left
    bh = bottom - top
    if bw > w * 0.45 or bh > h * 0.25:
        return None
    # 必须贴右侧
    if right < w * 0.80:
        return None
    return (left, top, min(w, right + 4), min(h, bottom + 4))


def fill_rect(im, box, mode="neighbor"):
    """用邻域像素覆盖矩形区域（抹掉工具栏）。"""
    x0, y0, x1, y1 = box
    x0 = max(0, x0); y0 = max(0, y0)
    x1 = min(im.width, x1); y1 = min(im.height, y1)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return False
    a = np.asarray(im).astype(np.int16)
    # 取左侧邻域列作为填充源（TradingView 工具栏背景通常是白/浅色）
    src_x0 = max(0, x0 - 60)
    patch = a[y0:y1, src_x0:x0]
    if patch.shape[1] == 0:
        patch = a[y0:y1, x0:x1]
    fill = np.repeat(patch[:, -1:, :], x1 - x0, axis=1)
    a[y0:y1, x0:x1] = fill
    Image.fromarray(a.astype(np.uint8)).save(im.filename) if hasattr(im, "filename") else None
    return True


def fill_rect_safe(im_path, box):
    im = Image.open(im_path).convert("RGB")
    a = np.asarray(im).astype(np.int16)
    x0, y0, x1, y1 = box
    x0 = max(0, x0); y0 = max(0, y0)
    x1 = min(im.width, x1); y1 = min(im.height, y1)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return False
    src_x0 = max(0, x0 - 80)
    patch = a[y0:y1, src_x0:x0]
    if patch.shape[1] == 0:
        return False
    fill = np.repeat(patch[:, -1:, :], x1 - x0, axis=1)
    a[y0:y1, x0:x1] = fill
    im2 = Image.fromarray(a.astype(np.uint8))
    im2.save(im_path)
    return True


def process(path, out_prefix):
    """处理一帧：抹工具栏 → 分区裁切 → 输出。"""
    im = Image.open(path).convert("RGB")
    gray = np.asarray(im.convert("L"))
    h, w = gray.shape
    out = {}

    # 1) 工具栏抹除（先在副本上做）
    im2 = im.copy()
    tb = find_toolbar(gray)
    if tb:
        x0, y0, x1, y1 = tb
        # 校验：该区域必须含高饱和度像素（彩色画笔条），否则可能是K线，放弃抹除
        reg = np.asarray(im.crop((x0, y0, x1, y1))).astype(np.int16)
        if reg.size:
            mx = reg.max(axis=2); mn = reg.min(axis=2)
            sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1), 0)
            color_px = int((sat > 0.35).sum())
        else:
            color_px = 0
        if color_px >= 40:
            tmp = out_prefix + "_tmp.png"
            im2.save(tmp)
            if fill_rect_safe(tmp, tb):
                im2 = Image.open(tmp).convert("RGB")
                os.remove(tmp)
                out["toolbar_removed"] = tb
                out["toolbar_color_px"] = color_px
        else:
            out["toolbar_skipped"] = {"box": tb, "color_px": color_px}
    # 2) 尺寸标尺（左上角 "1440 x 900 pt"）
    a = np.asarray(im2).astype(np.int16)
    # 检测左上角浅色矩形区域（标尺是白色半透明块）
    tl = a[0:int(h * 0.05), 0:int(w * 0.28)]
    if tl.size and (tl.max(axis=2) > 225).mean() > 0.5:
        a[0:int(h * 0.045), 0:int(w * 0.26)] = a[int(h * 0.05):int(h * 0.095),
                                                   0:int(w * 0.26)]
    im2 = Image.fromarray(a.astype(np.uint8))
    out["ruler_removed"] = True

    # 3) 分区裁切
    gray2 = np.asarray(im2.convert("L"))
    blocks = find_blocks(gray2)
    out["dark_blocks"] = blocks

    # 整帧（清理后）
    full = out_prefix + "_full.jpg"
    im2.save(full, quality=92)
    out["full"] = full

    # 讲义页区（白底，= 黑带之间的白区）
    # 逐块切出黑底图表
    charts = []
    for i, (y0, y1) in enumerate(blocks, 1):
        c = out_prefix + f"_chart{i}.jpg"
        im2.crop((0, y0, w, y1)).save(c, quality=92)
        charts.append({"file": c, "y0": y0, "y1": y1,
                       "h": y1 - y0, "w": w})
    out["charts"] = charts

    # 讲义页：取第一个黑带以上的白区（含标题）
    # 若首块黑带从 y=0 开始（整屏都是图表），则没有讲义页区
    if blocks and blocks[0][0] > 20:
        top_white = im2.crop((0, 0, w, blocks[0][0]))
        if top_white.height > 20 and top_white.width > 20:
            p = out_prefix + "_slide.jpg"
            top_white.save(p, quality=92)
            out["slide"] = p
    else:
        out["slide"] = None
        out["slide_note"] = "整屏为图表，无讲义页白区"
    return out


def main():
    log("=== clean start")
    os.makedirs(OUT_DIR, exist_ok=True)
    for f in os.listdir(OUT_DIR):
        os.remove(os.path.join(OUT_DIR, f))
    idx = json.load(open(os.path.join(ROOT, "work", "course_frames_index.json"),
                         encoding="utf-8"))
    results = []
    for item in idx["frames"]:
        key = item["key"]
        src = item["file"]
        base = os.path.join(OUT_DIR, key)
        try:
            r = process(src, base)
        except Exception as e:
            log(f"  ✗ {key}: {e}")
            continue
        results.append({**item, "clean": r})
        nch = len(r.get("charts", []))
        log(f"  {key:<32} 图表块={nch} 工具栏={'已抹' if r.get('toolbar_removed') else '无'}")
    with open(IDX, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=1)
    log(f"=== clean done  {len(results)} 帧")


if __name__ == "__main__":
    main()
