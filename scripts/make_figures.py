#!/usr/bin/env python3
"""生成报告用图表：
  fig1_timeline.png   三遍重复讲解的时间轴分布
  fig2_scores.png     费曼四维得分
  fig3_terms.png      术语 vs 类比 密度对比

输出：outputs/pa_images/*.png
日志：logs/figures.log
"""
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


WORK = os.path.join(ROOT, "work")
OUT = os.path.join(ROOT, "outputs", "pa_images")
LOG = os.path.join(ROOT, "logs", "figures.log")

for cand in ["PingFang SC", "Hiragino Sans GB", "Heiti SC", "STHeiti",
             "Arial Unicode MS", "Songti SC"]:
    if any(f.name == cand for f in font_manager.fontManager.ttflist):
        plt.rcParams["font.family"] = cand
        break
plt.rcParams["axes.unicode_minus"] = False


def log(m):
    line = f"[fig] {m}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def fig1():
    blocks = [
        ("五步 · 第1遍（主线）", 89, 269, "#2F6FB0"),
        ("三归宿 · 第1遍", 177, 302, "#3F8F6B"),
        ("五步 · 第2遍（重讲）", 534, 622, "#8FA8C8"),
        ("五步 · 第3遍（重讲）", 695, 890, "#B9C6D8"),
        ("三归宿 · 第2遍", 935, 985, "#9CC4B3"),
        ("例1 双底反转（图上）", 982, 1203, "#C2703D"),
    ]
    fig, ax = plt.subplots(figsize=(11, 4.2), dpi=150)
    for i, (name, a, b, c) in enumerate(blocks):
        y = len(blocks) - i
        ax.barh(y, b - a, left=a, height=0.55, color=c, edgecolor="white")
        ax.text(a + (b - a) / 2, y, f"{int(b-a)}s", ha="center", va="center",
                color="white", fontsize=9, fontweight="bold")
    ax.set_yticks([len(blocks) - i for i in range(len(blocks))])
    ax.set_yticklabels([b[0] for b in blocks], fontsize=10)
    ax.set_xlabel("录屏时间（秒）", fontsize=10)
    ax.set_xlim(0, 1210)
    ax.set_title("讲解内容的时间轴分布：五步骤被完整讲了 3 遍，三种归宿讲了 2 遍",
                 fontsize=12, fontweight="bold", pad=12)
    for x in (534, 695, 935):
        ax.axvline(x, color="#C0392B", ls="--", lw=1, alpha=0.5)
    ax.text(534, len(blocks) + 0.55, "▲ 开始重讲", color="#C0392B", fontsize=8)
    ax.grid(axis="x", alpha=0.25)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    p = os.path.join(OUT, "fig1_timeline.png")
    fig.tight_layout()
    fig.savefig(p, facecolor="white")
    plt.close(fig)
    log(f"saved {p}")


def fig2():
    dims = ["F1\n概念边界", "F2\n外行语言", "F3\n卡壳处理", "F4\n教学闭环", "画面\n执行"]
    scores = [3, 2, 3, 1, 2]
    notes = ["术语有定义但循环引用",
             "几乎零类比，术语密度高",
             "2 次自我纠错（有效）",
             "零行动指令，零检验",
             "高亮未执行，流程图静态"]
    fig, ax = plt.subplots(figsize=(9.5, 4.4), dpi=150)
    colors = ["#3F8F6B" if s >= 4 else "#C2703D" if s == 3 else "#C0392B" for s in scores]
    bars = ax.bar(dims, scores, color=colors, width=0.56, edgecolor="white")
    for b, s, n in zip(bars, scores, notes):
        ax.text(b.get_x() + b.get_width() / 2, s + 0.14, f"{s}/5", ha="center",
                fontsize=11, fontweight="bold")
        ax.text(b.get_x() + b.get_width() / 2, -0.42, n, ha="center", fontsize=7.6,
                color="#444", wrap=True)
    ax.set_ylim(0, 5.4)
    ax.set_ylabel("得分（5 分制）", fontsize=10)
    ax.set_title("费曼学习法四维评分（基于录屏实测，非印象）", fontsize=12,
                 fontweight="bold", pad=12)
    ax.axhline(3, color="#999", ls=":", lw=1)
    ax.grid(axis="y", alpha=0.25)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    p = os.path.join(OUT, "fig2_scores.png")
    fig.tight_layout()
    fig.savefig(p, facecolor="white")
    plt.close(fig)
    log(f"saved {p}")


def fig3():
    metrics = json.load(open(os.path.join(WORK, "feynman_scores.json"), encoding="utf-8"))
    m = metrics["F2_外行语言"]
    labels = ["口头禅\n（这个/是不是/其实…）", "价格行为学术语", "类比/具象化\n（就像/好比…）"]
    vals = [36.9, m["术语每千字"], m["类比每千字"]]
    colors = ["#C0392B", "#C2703D", "#3F8F6B"]
    fig, ax = plt.subplots(figsize=(7.6, 3.9), dpi=150)
    bars = ax.bar(labels, vals, color=colors, width=0.5, edgecolor="white")
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.7, f"{v}", ha="center",
                fontsize=12, fontweight="bold")
    ax.set_ylabel("每千中文字出现次数", fontsize=10)
    ax.set_title("外行语言诊断：术语与口头禅密集，类比几乎缺席", fontsize=12,
                 fontweight="bold", pad=12)
    ax.set_ylim(0, 42)
    ax.grid(axis="y", alpha=0.25)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    p = os.path.join(OUT, "fig3_terms.png")
    fig.tight_layout()
    fig.savefig(p, facecolor="white")
    plt.close(fig)
    log(f"saved {p}")


def main():
    os.makedirs(OUT, exist_ok=True)
    fig1()
    fig2()
    fig3()
    log("=== figures done")


if __name__ == "__main__":
    main()
