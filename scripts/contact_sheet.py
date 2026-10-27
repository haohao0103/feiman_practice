#!/usr/bin/env python3
"""把抽帧拼成 contact sheet，用于核查画面内容与切换。

输出：work/contact_sheet_1.jpg ... 每张 4x4 共16帧
日志：logs/contact_sheet.log
"""
import os
from PIL import Image, ImageDraw

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


FRAMES = os.path.join(ROOT, "work", "frames")
OUT = os.path.join(ROOT, "work")
LOG = os.path.join(ROOT, "logs", "contact_sheet.log")
INTERVAL = 10  # 每帧对应秒数


def log(m):
    line = f"[contact] {m}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def build(files, idx0):
    cols, rows = 4, 4
    tw, th = 460, 288
    sheet = Image.new("RGB", (cols * tw, rows * (th + 22)), "white")
    d = ImageDraw.Draw(sheet)
    for i, f in enumerate(files):
        try:
            im = Image.open(os.path.join(FRAMES, f)).convert("RGB")
        except Exception as e:
            log(f"open fail {f}: {e}")
            continue
        im.thumbnail((tw, th))
        x = (i % cols) * tw
        y = (i // cols) * (th + 22)
        sheet.paste(im, (x, y + 20))
        sec = (idx0 + i) * INTERVAL
        d.text((x + 4, y + 4), f"f{idx0+i+1:04d}  t={sec//60:02d}:{sec%60:02d}", fill="black")
    return sheet


def main():
    files = sorted(os.listdir(FRAMES))
    log(f"frames={len(files)}")
    for g in range(0, len(files), 16):
        chunk = files[g:g + 16]
        sheet = build(chunk, g)
        p = os.path.join(OUT, f"contact_sheet_{g//16+1}.jpg")
        sheet.save(p, quality=88)
        log(f"saved {p}  frames {g+1}-{g+len(chunk)}")
    log("done")


if __name__ == "__main__":
    main()
