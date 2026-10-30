#!/usr/bin/env python3
"""可懂度 / 外行程度 / 画面结构 的指标测量 —— 用来**标定闸门阈值**。

方法论：阈值不拍脑袋，先在现有产出上量分布，看"标杆产出"与"早期产出"落在哪，
再把阈值定在能区分它们的位置（和画面去重阈值 0.03 一个套路）。

指标：
  ① 未解释术语密度   次/百字   （讲得清才算懂；费曼核心）
  ② 长句率         %        （超过 N 字无标点的句子占比 —— 外行听不懂）
  ③ 平均句长       字
  ④ 书面连接词密度 次/百字   （正向：也就是说/换句话说/具体来说…）
  ⑤ 口语填充词密度 次/百字   （负向：这个/那个/然后/其实/对吧…）
  ⑥ 类比段覆盖率   %        （多少段有类比）
  ⑦ 段配图率       %        （多少段有配图）
  ⑧ 孤图率         %        （抽了却没被正文引用的图 = 白抽）

用法：python3 scripts/measure_readability.py [课件md ...]
"""
import importlib.util
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location(
    "csq", os.path.join(ROOT, "scripts", "check_script_quality.py"))
csq = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(csq)

CN = re.compile(r"[一-鿿]")

# 书面连接词（正向信号：把话讲明白的措辞）
CONNECTORS = ["也就是说", "换句话说", "通俗点", "说白了", "简单讲", "具体来说",
              "换言之", "意思是", "指的是", "你可以理解为", "打个比方", "就好比",
              "比如", "例如", "所以", "因此", "因为", "但是", "所以记住"]
# 口语填充词（负向信号）
FILLERS = ["这个", "那个", "然后", "其实", "对吧", "是不是", "就是说", "我们说",
           "呃", "嗯", "啊", "对不对", "怎么讲", "那么"]


def metrics(md_path):
    with open(md_path, encoding="utf-8") as f:
        md = f.read()
    m = re.search(r"^##\s*附", md, re.M)
    main_body = md[:m.start()] if m else md
    body = csq.body_of(main_body)
    cn = max(1, len(CN.findall(body)))
    per100 = lambda n: round(n * 100 / cn, 2)

    # ① 未解释术语密度
    unexp = csq.find_unexplained_terms(body, csq.split_sections(main_body))
    terms = [u["term"] for u in unexp]

    # 句子切分（按中文/英文句末标点）
    sents = [s.strip() for s in re.split(r"[。！？!?\n]", body) if s.strip()]
    lens = [len(CN.findall(s)) for s in sents]
    long_sents = [n for n in lens if n > 40]

    # ⑥ 类比段覆盖率（按 body_of 口径过滤后的段正文）
    segs = csq.split_sections(main_body)
    seg_with_analogy = sum(1 for _t, txt in segs
                            if any(k in txt for k in csq.ANALOGY_SEG))
    seg_total = max(1, len(segs))

    # ⑦ 段配图率：必须看**原文**（body_of 会把图片行滤掉 → 用它算永远是 0）
    raw_segs, cur = [], None
    for l in main_body.split("\n"):
        if re.match(r"^#{2,3}\s", l):
            cur = []
            raw_segs.append(cur)
        elif cur is not None:
            cur.append(l)
    seg_with_pic = sum(1 for g in raw_segs if any("![" in x for x in g))

    # ⑧ 孤图率：抽出来却没被正文引用的图（= 白抽，白占磁盘）
    all_imgs = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", main_body)
    referenced = set(os.path.basename(i) for i in all_imgs)
    base = os.path.dirname(os.path.abspath(md_path))
    on_disk = []
    for sub in ("frames_clean", "diagrams"):
        d = os.path.join(base, sub)
        if os.path.isdir(d):
            for fn in os.listdir(d):
                if fn.lower().endswith((".jpg", ".png")):
                    on_disk.append(fn)
    orphans = [fn for fn in on_disk if fn not in referenced]

    return {
        "cn": cn,
        "unexp_terms": terms,
        "unexp_density": per100(len(unexp)),
        "long_sen_rate": round(len(long_sents) * 100 / max(1, len(sents)), 1),
        "avg_sen_len": round(sum(lens) / max(1, len(lens)), 1),
        "connector_density": per100(sum(body.count(k) for k in CONNECTORS)),
        "filler_density": per100(sum(body.count(k) for k in FILLERS)),
        "analogy_seg_rate": round(seg_with_analogy * 100 / seg_total, 1),
        "seg_pic_rate": round(seg_with_pic * 100 / seg_total, 1),
        "orphan_rate": round(len(orphans) * 100 / max(1, len(on_disk)), 1),
        "n_segs": seg_total,
        "n_imgs": len(on_disk),
        "n_orphan": len(orphans),
    }


def main():
    files = sys.argv[1:]
    if not files:
        base = os.path.join(ROOT, "outputs")
        files = [os.path.join(base, d, "讲课稿.md")
                 for d in sorted(os.listdir(base))
                 if d.startswith("课件_")
                 and os.path.exists(os.path.join(base, d, "讲课稿.md"))]
    print(f"{'课件':40} {'字':>6} {'未解术语/百字':>10} {'长句%':>6} {'均句长':>6} "
          f"{'连接词':>6} {'填充词':>6} {'类比段%':>7} {'配图段%':>7} {'孤图%':>6}")
    rows = []
    for f in files:
        if not os.path.exists(f):
            continue
        m = metrics(f)
        name = os.path.basename(os.path.dirname(f))[:38]
        rows.append((name, m))
        print(f"  {name:40} {m['cn']:>6} {m['unexp_density']:>10} "
              f"{m['long_sen_rate']:>6} {m['avg_sen_len']:>6} {m['connector_density']:>6} "
              f"{m['filler_density']:>6} {m['analogy_seg_rate']:>7} "
              f"{m['seg_pic_rate']:>7} {m['orphan_rate']:>6}")
    if rows:
        def stat(key):
            vals = sorted(m[key] for _n, m in rows)
            return (vals[0], vals[len(vals) // 2], vals[-1])
        print("\n各指标分布（min / 中位 / max）：")
        for k in ("unexp_density", "long_sen_rate", "avg_sen_len",
                  "connector_density", "filler_density",
                  "analogy_seg_rate", "seg_pic_rate", "orphan_rate"):
            lo, mid, hi = stat(k)
            print(f"  {k:20} {lo:>8} / {mid:>8} / {hi:>8}")


if __name__ == "__main__":
    main()
