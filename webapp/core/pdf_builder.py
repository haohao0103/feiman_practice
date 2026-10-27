#!/usr/bin/env python3
"""把「张雪峰风格逐字稿.md」（含内嵌 ![](frames_clean/xxx.jpg)）渲染成 PDF 课件。

只用 matplotlib（环境自带）+ PIL，不依赖 reportlab/weasyprint。
中文用系统 CJK 字体（PingFang / Hiragino Sans GB / STHeiti）。
图片按出现位置内嵌；相对路径相对于 md 所在目录解析（与交付目录 frames_clean 同级）。
"""
import os
import re

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib import font_manager as fm
from PIL import Image

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# (path, 期望 face 标签) —— 优先简体中文 face。
# 关键坑：matplotlib 对 .ttc 默认取 face0，而 PingFang.ttc 的 face0 是 HK（繁体），
# 会导致简体字（如「虑」）缺字警告。这里显式抽 SC(简体) face 或选 face0 即简体的字体。
_CANDIDATE_FONTS = [
    ("/System/Library/Fonts/PingFang.ttc", "SC"),
    ("/System/Library/Fonts/Hiragino Sans GB.ttc", None),  # face0 已是简体 GB
    ("/System/Library/Fonts/STHeiti Light.ttc", "SC"),
    ("System/Library/Fonts/STHeiti.ttc", "SC"),
]

_FONT = None


def _extract_face(path, want):
    """对 .ttc 抽出 want 标签(如 SC)的 Regular face 到临时 ttf 并返回路径；
    非 ttc 或 want=None 直接返回原路径（face0 即可）；失败返回 None。"""
    if not path.lower().endswith(".ttc"):
        return path
    if want is None:
        return path
    try:
        import tempfile
        from fontTools.ttLib import TTCollection
        col = TTCollection(path)
        picks = []
        for i, f in enumerate(col.fonts):
            nm = (f["name"].getDebugName(4) or "")
            if want and want in nm:
                picks.append((i, nm))
        if not picks:
            return None
        reg = [(i, nm) for i, nm in picks if "Regular" in nm]
        target = reg[0][0] if reg else picks[0][0]
        tmp = os.path.join(tempfile.gettempdir(),
                           f"pdf_cjk_{os.path.basename(path)}_{target}.ttf")
        if not os.path.exists(tmp):
            col.fonts[target].save(tmp)
        return tmp
    except Exception:
        return None


def _font():
    global _FONT
    if _FONT is None:
        for path, want in _CANDIDATE_FONTS:
            if not os.path.exists(path):
                continue
            ttf = _extract_face(path, want)
            if not ttf:
                continue
            try:
                _FONT = fm.FontProperties(fname=ttf)
                break
            except Exception:
                continue
        if _FONT is None:
            _FONT = fm.FontProperties(family="DejaVu Sans")
    return _FONT


IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def _clean(t):
    """去掉 markdown 强调/代码标记（PDF 无对应排版），仅做纯文本显示。"""
    return t.replace("**", "").replace("`", "").replace(">", "").strip()


def _parse(md):
    blocks = []
    buf = []

    def flush():
        if buf:
            blocks.append(("para", "\n".join(buf).strip()))
            buf.clear()

    for ln in md.split("\n"):
        m = IMG_RE.search(ln)
        if m:
            flush()
            blocks.append(("image", m.group(2).strip(), m.group(1).strip()))
            continue
        s = ln.strip()
        if s.startswith("# "):
            flush()
            blocks.append(("title", _clean(s[2:])))
        elif s.startswith("## "):
            flush()
            blocks.append(("h2", _clean(s[3:])))
        elif s == "":
            flush()
        else:
            buf.append(_clean(s))
    flush()
    return blocks


def _char_width_pt(fontsize):
    # CJK 全角字约 1em，留点余量避免溢出
    return fontsize * 0.96


def markdown_to_pdf(md_path, out_pdf):
    import matplotlib
    matplotlib.rcParams["figure.max_open_warning"] = 100  # 长课件多页，避免误报警告
    base = os.path.dirname(md_path)
    with open(md_path, encoding="utf-8") as f:
        md = f.read()
    blocks = _parse(md)

    fig_w, fig_h = 8.27, 11.69  # A4 英寸
    margin_x = 0.08
    top_y, bot_y = 0.95, 0.05
    text_w_frac = 1 - 2 * margin_x
    body, title_sz, h2_sz = 11, 17, 13
    line_gap = 1.5
    font = _font()

    pages = []
    fig = plt.figure(figsize=(fig_w, fig_h))
    pages.append(fig)
    y = top_y

    def new_page():
        nonlocal fig, y
        fig = plt.figure(figsize=(fig_w, fig_h))
        pages.append(fig)
        y = top_y

    def line_h_frac(sz):
        return (sz / 72.0) * line_gap / fig_h

    def draw_text(text, sz, gap=0.0):
        nonlocal y
        cw = _char_width_pt(sz)
        max_chars = max(8, int(text_w_frac * fig_w * 72 / cw))
        lines = []
        for raw in text.split("\n"):
            if not raw:
                lines.append("")
                continue
            while len(raw) > max_chars:
                lines.append(raw[:max_chars])
                raw = raw[max_chars:]
            lines.append(raw)
        for ln in lines:
            lh = line_h_frac(sz)
            if y - lh < bot_y:
                new_page()
            fig.text(margin_x, y, ln, fontproperties=font, fontsize=sz,
                     ha="left", va="top")
            y -= lh
        if gap:
            y -= line_h_frac(sz) * gap

    for b in blocks:
        if b[0] == "title":
            draw_text(b[1], title_sz, gap=0.4)
        elif b[0] == "h2":
            draw_text(b[1], h2_sz, gap=0.25)
        elif b[0] == "para":
            draw_text(b[1], body)
            y -= line_h_frac(body) * 0.3
        elif b[0] == "image":
            rel = b[1]
            ip = rel if os.path.isabs(rel) else os.path.join(base, rel)
            if not os.path.exists(ip):
                altp = os.path.join(base, os.path.basename(rel))
                ip = altp if os.path.exists(altp) else None
            if not ip:
                draw_text(f"[图片缺失：{rel}]", body)
                continue
            try:
                im = Image.open(ip)
                wpx, hpx = im.size
            except Exception:
                draw_text(f"[图片无法读取：{rel}]", body)
                continue
            aspect = hpx / wpx
            W = text_w_frac * 0.95
            H = W * (fig_w / fig_h) * aspect
            avail = y - bot_y
            if H > avail:
                new_page()
                avail = y - bot_y
                H = min(H, avail * 0.95)
                W = H * (fig_h / fig_w) / aspect
            if H <= 0 or W <= 0:
                continue
            x0 = margin_x + (text_w_frac - W) / 2
            bottom = y - H
            ax = fig.add_axes([x0, bottom, W, H])
            ax.imshow(im)
            ax.set_xticks([])
            ax.set_yticks([])
            ax.axis("off")
            y = bottom - line_h_frac(body) * 0.5

    with PdfPages(out_pdf) as pdf:
        for pg in pages:
            pdf.savefig(pg)
            plt.close(pg)
    return out_pdf
