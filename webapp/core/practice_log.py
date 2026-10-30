#!/usr/bin/env python3
"""练习迭代追踪：把「一轮轮试讲对账」串成可追踪的掌握度曲线。

闭环的最后一块：每一轮对账都会产出一个掌握度分数，但单看一次没有意义 ——
要知道的是**趋势**（在进步还是卡住）、**哪几段反复出问题**、**离达标还差多少**。

本模块：
  - archive_round()   把一轮对账结果归档进练习项目 JSON
  - compute_trend()   掌握度趋势（up/flat/down）+ 每轮增减
  - recurring_weak_segments() 跨轮累计薄弱段（同一段反复漏/说错 → 优先练）
  - build_trend_chart() 掌握度趋势图（PNG）
  - build_practice_report() 练习报告（Markdown，内嵌趋势图 + 薄弱段 + 下一步建议）
  - next_advice()     下一步该干什么（按趋势 + 薄弱段 + 达标情况）

练习项目划分：按**课件标题**归组（同一份课件反复练 = 同一个项目）；
没有课件时按试讲文件名归组。可显式传 practice_key 覆盖。
"""
import json
import os
import re
import time

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


STORAGE_DIR = "练习记录"
DEFAULT_THRESHOLD = 85.0


# ============================================================ 存储
def practice_dir(cfg):
    d = os.path.join(cfg["outputs_dir"], STORAGE_DIR)
    os.makedirs(d, exist_ok=True)
    return d


def _safe_name(key):
    s = re.sub(r"[^\w一-鿿\-]+", "_", str(key)).strip("_")
    return s[:60] or "practice"


def _path(cfg, key):
    return os.path.join(practice_dir(cfg), _safe_name(key) + ".json")


def practice_key_from_courseware(md_text, fallback=""):
    """从课件 md 提取练习项目键（取第一个 # 标题，去掉序号前缀）。"""
    for ln in (md_text or "").split("\n"):
        if ln.startswith("# "):
            t = ln[2:].strip()
            t = re.sub(r"^第?\s*[0-9一二三四五六七八九十]+\s*[期讲]\s*[·、:：]?\s*", "", t)
            if t:
                return t[:60]
    return (os.path.splitext(os.path.basename(fallback))[0] or "practice")[:60]


def load_practice(cfg, key):
    p = _path(cfg, key)
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"key": key, "title": key, "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "updated_at": "", "threshold": DEFAULT_THRESHOLD, "rounds": []}


def save_practice(cfg, data):
    data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    p = _path(cfg, data["key"])
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return p


def list_practices(cfg):
    out = []
    d = practice_dir(cfg)
    for f in sorted(os.listdir(d)):
        if not f.endswith(".json"):
            continue
        try:
            with open(os.path.join(d, f), encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            continue
        rounds = data.get("rounds", [])
        scores = [r.get("score", 0) for r in rounds]
        out.append({
            "key": data.get("key", f[:-5]),
            "title": data.get("title", data.get("key")),
            "rounds": len(rounds),
            "last_score": scores[-1] if scores else None,
            "best_score": max(scores) if scores else None,
            "first_score": scores[0] if scores else None,
            "trend": compute_trend(rounds)["trend"],
            "mastered": bool(scores and scores[-1] >= data.get("threshold", DEFAULT_THRESHOLD)),
            "updated_at": data.get("updated_at", ""),
        })
    out.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    return out


# ============================================================ 归档
def _bigram_jac(a, b):
    """字符 bigram 的 Jaccard 相似度（衡量两个项目名像不像同一门课）。"""
    def grams(s):
        s = re.sub(r"[^\w一-鿿]", "", s or "")
        return set(s[i:i + 2] for i in range(max(1, len(s) - 1)))
    ga, gb = grams(a), grams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def list_practice_keys(cfg):
    """已有练习项目的 key 列表（**原始 key**，不是文件名）。

    文件名是 key 经 _safe_name() 消毒后的（"·" → "_"），拿文件名去比相似度
    会和原始 key 对不上 → 归组永远落空（实测：同名 key 都认不出来）。
    """
    d = practice_dir(cfg)
    if not os.path.isdir(d):
        return []
    out = []
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(d, fn), encoding="utf-8") as f:
                data = json.load(f)
            k = data.get("key") or os.path.splitext(fn)[0]
            out.append(k)
        except Exception:
            out.append(os.path.splitext(fn)[0])
    return out


def resolve_practice_key(cfg, raw_key):
    """把算出来的 key 落到**已有项目**上 —— 标题微调不该让历史分数断档。

    ★ 实测踩过：同一门课两次生成的课件标题会微调
      （"反转的原理与识别步骤" → "反转的原理与基本步骤"），
      而 key 取自课件标题 → 直接新建项目 → **历史分数断了，趋势从零开始**。

    相似度阈值 0.60（实测：同课微调 0.647 / 同源大改 0.412 / 不同课 0.000）。
    阈值偏保守是刻意的：把两门不同的课合并成一个项目（趋势图失真）
    比断档（多练几轮就有趋势）更糟。
    """
    raw = (raw_key or "").strip()
    if not raw:
        return raw_key
    existing = list_practice_keys(cfg)
    if raw in existing:
        return raw
    best, score = None, 0.0
    for k in existing:
        s = _bigram_jac(raw, k)
        if s > score:
            best, score = k, s
    if best and score >= 0.60:
        try:
            data = load_practice(cfg, best)
            aliases = data.get("aliases") or []
            if raw not in aliases:
                aliases.append(raw)
            data["aliases"] = aliases
            data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            save_practice(cfg, data)
        except Exception:
            pass
        return best
    return raw


def archive_round(cfg, key, round_data):
    """把一轮对账结果归档（同一 key 追加轮次）。返回更新后的项目数据。"""
    key = resolve_practice_key(cfg, key)
    data = load_practice(cfg, key)
    rounds = data.get("rounds", [])
    rd = dict(round_data)
    rd["round"] = len(rounds) + 1
    rd.setdefault("at", time.strftime("%Y-%m-%d %H:%M:%S"))
    rounds.append(rd)
    data["rounds"] = rounds
    data["title"] = data.get("title") or round_data.get("practice_title") or key
    data["threshold"] = round_data.get("threshold", data.get("threshold", DEFAULT_THRESHOLD))
    save_practice(cfg, data)
    return data


# ============================================================ 趋势
def compute_trend(rounds):
    """掌握度趋势。up=在进步 / flat=卡住 / down=退步。"""
    scores = [r.get("score", 0) for r in rounds]
    if len(scores) < 2:
        return {"scores": scores, "deltas": [], "trend": "first",
                "avg_delta": 0.0, "best": max(scores) if scores else None}
    deltas = [round(scores[i] - scores[i - 1], 1) for i in range(1, len(scores))]
    last3 = deltas[-3:]
    avg = sum(last3) / len(last3)
    if avg >= 3:
        trend = "up"
    elif avg <= -3:
        trend = "down"
    else:
        trend = "flat"
    return {"scores": scores, "deltas": deltas, "trend": trend,
            "avg_delta": round(avg, 1), "best": max(scores)}


def recurring_weak_segments(rounds, top=5):
    """跨轮累计薄弱段：同一段反复漏讲/说错 → 优先练这个。"""
    stat = {}
    for r in rounds:
        for s in r.get("segments", []) or []:
            title = s.get("title", "")
            if not title:
                continue
            k = title
            e = stat.setdefault(k, {"title": title, "part": s.get("part", ""),
                                    "weak_rounds": 0, "total": 0, "reasons": []})
            e["total"] += 1
            cov = s.get("covered", "none")
            if cov != "full":
                e["weak_rounds"] += 1
                e["reasons"].append(f"第{r.get('round','?')}轮 {cov}")
            elif s.get("wrong"):
                e["weak_rounds"] += 1
                e["reasons"].append(f"第{r.get('round','?')}轮 讲错")
    out = [e for e in stat.values() if e["weak_rounds"] >= 1]
    out.sort(key=lambda e: (-e["weak_rounds"], -e["total"]))
    return out[:top]


# ============================================================ 趋势图
def build_trend_chart(cfg, data, out_png, log=None):
    """掌握度趋势图（左：总分+达标线；右：各维度）。返回路径或 None。

    注意：Axes.legend 用的是 prop=（不是 fontproperties=），传错会 TypeError。
    """
    rounds = data.get("rounds", [])
    if not rounds:
        return None
    scores = [r.get("score", 0) for r in rounds]
    xs = list(range(1, len(scores) + 1))
    th = data.get("threshold", DEFAULT_THRESHOLD)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from . import courseware_gen as cg
        fp = cg._sc_cjk_font()
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4),
                                       gridspec_kw={"width_ratios": [1, 1.4]})
        ax1.plot(xs, scores, "-o", color="#2563eb", lw=2, markersize=7,
                 markerfacecolor="#fff", markeredgewidth=2)
        ax1.axhline(th, color="#dc2626", ls="--", lw=1.5, label=f"达标线 {th:.0f} 分")
        for x, y in zip(xs, scores):
            ax1.annotate(f"{y:.0f}", (x, y), textcoords="offset points",
                         xytext=(0, 9), ha="center", fontsize=10, fontproperties=fp)
        ax1.set_title("掌握度趋势", fontproperties=fp, fontsize=13)
        ax1.set_xlabel("练习轮次", fontproperties=fp)
        ax1.set_ylabel("掌握度", fontproperties=fp)
        ax1.set_xticks(xs)
        ax1.set_ylim(0, 105)
        ax1.grid(alpha=.3)
        ax1.legend(prop=fp, fontsize=9)
        for lb in ax1.get_xticklabels() + ax1.get_yticklabels():
            lb.set_fontproperties(fp)

        dims = [("coverage", "知识点覆盖"), ("fidelity", "忠实度"),
                ("check_q_rate", "检验提问"), ("action_rate", "行动指令"),
                ("picture_rate", "画面引用")]
        for k, label in dims:
            ys = [((r.get("dims") or {}).get(k) or 0) * 100 for r in rounds]
            ax2.plot(xs, ys, "-o", lw=1.8, ms=5, label=label)
        ax2.set_title("各维度执行率（%）", fontproperties=fp, fontsize=13)
        ax2.set_xlabel("练习轮次", fontproperties=fp)
        ax2.set_ylim(-5, 105)
        ax2.set_xticks(xs)
        ax2.grid(alpha=.3)
        ax2.legend(prop=fp, fontsize=9, ncol=3)
        for lb in ax2.get_xticklabels() + ax2.get_yticklabels():
            lb.set_fontproperties(fp)
        fig.tight_layout()
        fig.savefig(out_png, dpi=130)
        plt.close(fig)
        return out_png
    except Exception as e:
        # 不静默：图表失败要让上层知道（否则报告里就是空图）
        if log:
            log(f"  趋势图生成失败: {e}")
        else:
            import traceback
            traceback.print_exc()
        return None


# ============================================================ 下一步建议
def next_advice(data, trend, weak):
    """根据趋势 + 薄弱段给下一步建议。"""
    rounds = data.get("rounds", [])
    th = data.get("threshold", DEFAULT_THRESHOLD)
    last = trend["scores"][-1] if trend["scores"] else 0
    tips = []
    if last >= th:
        tips.append(f"**已达标（{last:.0f} ≥ {th:.0f}）**：可以换更难的课题/更严的风格强度再练。")
        return tips
    gap = th - last
    if trend["trend"] == "first":
        tips.append(f"**第 1 轮基线 {last:.0f} 分**，距达标 {th:.0f} 还差 {gap:.0f} 分。先看下面「反复薄弱段」，"
                    f"把它们拆成 2~3 段单独练，别整篇重录。")
    elif trend["trend"] == "up":
        tips.append(f"**在进步**（最近每轮约 +{trend['avg_delta']:.1f} 分），距达标还差 {gap:.0f} 分。"
                    f"保持同样练法，重点补下面「反复薄弱段」。")
    elif trend["trend"] == "flat":
        tips.append(f"**卡住了**（最近 3 轮平均只动了 {trend['avg_delta']:+.1f} 分），距达标 {gap:.0f} 分。"
                    f"整篇重录性价比低 → 改成**只练薄弱段**；或把风格强度调低一档、"
                    f"或把课件拆成两半分别练。")
    else:
        tips.append(f"**退步了**（最近每轮 {trend['avg_delta']:.1f} 分），距达标 {gap:.0f} 分。"
                    f"先别加量：回到第一版课件，只练「反复薄弱段」，其余段可以跳过/口述带过。")
    if weak:
        tips.append("**优先练这几段**（跨轮反复出问题）：")
        for w in weak:
            tips.append(f"- {w['part']} / {w['title']}（{w['weak_rounds']}/{w['total']} 轮有问题）")
    return tips


# ============================================================ 练习报告
def build_practice_report(cfg, data, chart_rel=None):
    rounds = data.get("rounds", [])
    trend = compute_trend(rounds)
    weak = recurring_weak_segments(rounds)
    th = data.get("threshold", DEFAULT_THRESHOLD)
    L = []
    A = L.append
    A(f"# 练习追踪报告 · {data.get('title', data.get('key'))}")
    A("")
    A(f"> 练习项目：`{data.get('key')}` ｜ 共 {len(rounds)} 轮 ｜ "
      f"达标线 {th:.0f} 分 ｜ 更新时间 {data.get('updated_at','')}")
    A("")
    A("## 一、结论")
    A("")
    last = trend["scores"][-1] if trend["scores"] else 0
    state = "已达标" if last >= th else ("在进步" if trend["trend"] == "up"
                                         else ("卡住" if trend["trend"] == "flat" else "退步"))
    A(f"**当前掌握度 {last:.0f} 分（{state}）｜ 历史最高 {trend.get('best', 0):.0f} ｜ 趋势 {trend['trend']}**")
    A("")
    if chart_rel:
        A(f"![掌握度趋势]({chart_rel})")
        A("")
    A("## 二、各轮明细")
    A("")
    A("| 轮 | 时间 | 得分 | 覆盖 | 忠实 | 提问 | 行动 | 画面 | 课件版本 | 试讲 | 改进稿 |")
    A("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rounds:
        d = r.get("dims") or {}

        def pct(k):
            v = d.get(k)
            return f"{v*100:.0f}%" if isinstance(v, (int, float)) else "—"
        A(f"| {r.get('round','?')} | {r.get('at','')} | **{r.get('score',0):.0f}** | "
          f"{pct('coverage')} | {pct('fidelity')} | {pct('check_q_rate')} | "
          f"{pct('action_rate')} | {pct('picture_rate')} | "
          f"{r.get('courseware_sig','—')} | {r.get('trial','')} | "
          f"{'有' if r.get('improve_path') else '—'} |")
    A("")
    A("## 三、反复薄弱段（跨轮累计）")
    A("")
    if weak:
        A("| 段 | 所属期 | 出问题轮次/总轮次 | 具体情况 |")
        A("|---|---|---|---|")
        for w in weak:
            A(f"| {w['title']} | {w['part']} | {w['weak_rounds']}/{w['total']} | "
              f"{'；'.join(w['reasons'][:4])} |")
    else:
        A("- （暂无反复出问题的段）")
    A("")
    A("## 四、下一步怎么做")
    A("")
    for t in next_advice(data, trend, weak):
        A(f"- {t}")
    A("")
    A("---")
    A("")
    A("*由 `practice_log` 自动生成。每一轮的数据来自该轮试讲的「课件↔试讲逐段对账」。*")
    return "\n".join(L)


def render_practice(cfg, key):
    """生成/刷新练习报告 + 趋势图。返回 {data, report_path, chart_path}。"""
    data = load_practice(cfg, key)
    if not data.get("rounds"):
        return {"data": data, "report_path": None, "chart_path": None}
    d = practice_dir(cfg)
    chart_png = os.path.join(d, _safe_name(key) + "_趋势.png")
    chart = build_trend_chart(cfg, data, chart_png)
    chart_rel = os.path.basename(chart) if chart else None
    rep = build_practice_report(cfg, data, chart_rel)
    rep_path = os.path.join(d, _safe_name(key) + "_练习报告.md")
    with open(rep_path, "w", encoding="utf-8") as f:
        f.write(rep)
    return {"data": data, "report_path": rep_path, "chart_path": chart}
