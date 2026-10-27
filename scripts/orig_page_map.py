#!/usr/bin/env python3
"""原视频讲义页识别 —— 建立「时间 → 讲义页标题」映射。

方法：
  1. 对每个场景代表帧，用 tesseract (chi_sim) OCR 标题区域
  2. 归一化标题（去空白、去页码水印）
  3. 输出每个场景识别到的标题 + 归并后的"讲义页段落"

标题区域：画面左上角（讲义页标题 "反转" / "第一章 反转的五个步骤" / "例1 双底反转"）。
输出：work/orig_page_map.json
日志：logs/orig_page_map.log
"""
import json
import os
import re
import subprocess
import time

from PIL import Image

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


FRAMES = os.path.join(ROOT, "work", "orig_frames")
SCENES = os.path.join(ROOT, "work", "orig_scenes.json")
OUT = os.path.join(ROOT, "work", "orig_page_map.json")
LOG = os.path.join(ROOT, "logs", "orig_page_map.log")
TESS = "/usr/local/bin/tesseract"

# 讲义页标题在画面上部；这里取 y∈[0, 45%] 的左侧 60%
TITLE_BOX = (0.02, 0.00, 0.62, 0.45)


def log(m):
    line = f"[pagemap] {m}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def ocr_title(path):
    im = Image.open(path).convert("RGB")
    w, h = im.size
    box = (int(w * TITLE_BOX[0]), int(h * TITLE_BOX[1]),
           int(w * TITLE_BOX[2]), int(h * TITLE_BOX[3]))
    crop = im.crop(box).resize((int((box[2]-box[0]) * 1.6),
                                int((box[3]-box[1]) * 1.6)), Image.LANCZOS)
    tmp = os.path.join(ROOT, "work", "_ocr_tmp.png")
    crop.save(tmp)
    try:
        r = subprocess.run([TESS, tmp, "-", "-l", "chi_sim+chi_tra",
                            "--psm", "6"],
                           capture_output=True, text=True, errors="replace",
                           timeout=60)
        return r.stdout
    except Exception as e:
        log(f"  OCR 失败 {path}: {e}")
        return ""


def normalize(text):
    """清洗 OCR 结果：去噪声行、合并、去掉水印。"""
    lines = []
    for l in text.splitlines():
        s = l.strip()
        if not s:
            continue
        # 去掉明显的噪声（单字符、纯符号、b站水印）
        if len(s) <= 1 and not s.isalnum():
            continue
        if "bilibili" in s.lower() or "鸿毅" in s:
            continue
        if re.fullmatch(r"[\W_]+", s):
            continue
        lines.append(s)
    # 取前 3 行拼成标题
    return " / ".join(lines[:3])


def main():
    log("=== orig page map start")
    d = json.load(open(SCENES, encoding="utf-8"))
    scenes = d["scenes"]
    t0 = time.time()
    out = []
    for sc in scenes:
        if not sc["frame"] or not os.path.exists(sc["frame"]):
            out.append({**sc, "title": None, "title_raw": ""})
            continue
        raw = ocr_title(sc["frame"])
        title = normalize(raw)
        out.append({**sc, "title": title, "title_raw": raw.strip()[:200]})
        log(f"  S{sc['idx']:02d} {sc['start_sec']:>6.1f}s :: {title[:70]}")
    log(f"OCR 完成 wall={time.time()-t0:.1f}s")

    # 归并：相同标题的连续场景合并成"页面段落"
    pages = []
    for r in out:
        key = (r["title"] or "").strip()
        if pages and pages[-1]["title"] == key:
            pages[-1]["end_sec"] = r["end_sec"]
            pages[-1]["scene_idxs"].append(r["idx"])
        else:
            pages.append({
                "title": key,
                "start_sec": r["start_sec"],
                "end_sec": r["end_sec"],
                "scene_idxs": [r["idx"]],
                "representative_frame": r["frame"],
                "mid_sec": r["mid_sec"],
            })
    log(f"--- 归并出 {len(pages)} 个讲义页段落 ---")
    for i, p in enumerate(pages, 1):
        log(f"  P{i:02d} {p['start_sec']:>6.1f}-{p['end_sec']:>6.1f}s :: {p['title'][:70]}")

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump({"scenes": out, "pages": pages}, fh, ensure_ascii=False, indent=1)
    log("=== orig page map done")


if __name__ == "__main__":
    main()
