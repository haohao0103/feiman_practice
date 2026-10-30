#!/usr/bin/env python3
"""课件质量审计（用项目**同一套口径**，不另立标准）。

复用 scripts/check_script_quality.py 的词表与 body_of()，
额外审计课件特有项：结构完整性 / 表演脚本格式 / 图片内嵌位置 / 附录 / 术语当场解释。

用法: python3 scripts/audit_courseware.py [课件md ...]
日志: work/courseware_audit.log
"""
import importlib.util
import os
import re
import sys

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


LOG = os.path.join(ROOT, "work/courseware_audit.log")
CN = re.compile(r"[一-鿿]")

# 复用项目口径（保证与 check_script_quality 同源）
_spec = importlib.util.spec_from_file_location(
    "csq", os.path.join(ROOT, "scripts/check_script_quality.py"))
csq = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(csq)

TARGETS = csq.TARGETS
SPEED = 240  # 字/分


def audit(md_path):
    with open(md_path, encoding="utf-8") as f:
        md = f.read()
    base = os.path.dirname(os.path.abspath(md_path))
    # 正文范围：排除附录（与 self_check 同一套切法，见 pipeline_ops.self_check）
    _m = re.search(r"^##\s*附", md, re.M)
    main_body = md[:_m.start()] if _m else md
    body = csq.body_of(main_body)
    cn = len(CN.findall(body))
    dur = cn / SPEED * 60

    # 段落切分（按 ## 段标题）→ 类比密度口径
    segs = []
    cur = None
    for l in md.split("\n"):
        if l.startswith("## ") and not l[3:].strip().startswith("附"):
            cur = {"title": l[3:].strip(), "text": ""}
            segs.append(cur)
            continue
        if cur is not None:
            cur["text"] += l + "\n"
    segs = [s for s in segs if s["title"] != "目录（期 / 段 / 关键画面）"]
    seg_texts = [csq.body_of(s["text"]) for s in segs]
    # 类比：同一段里多个标记只算 1 处
    analogy_dense = sum(1 for t in seg_texts
                        if any(k in t for k in csq.ANALOGY_SEG))
    analogy_raw = sum(1 for k in csq.ANALOGY if k in body)
    # 检验提问：逐句判定（句里必须真有问号/问词）
    q_hits = []
    for s in segs:
        for sent in re.split(r"[。！？\n]", s["text"]):
            if any(k in sent for k in csq.CHECK_Q) or sent.strip().endswith("？"):
                if sent.strip():
                    q_hits.append(sent.strip())
    # 行动指令：必须带"让观众做事"的动词
    a_hits = [sent.strip() for sent in re.split(r"[。！？\n]", body)
              if any(k in sent for k in csq.ACTION) and sent.strip()]
    # 未当场解释的术语（费曼核心）
    unexplained = []
    for t in csq.TERMS:
        if t in body and t not in csq.EXPLAINED:
            # 该术语所在句子必须带解释标记（就是/等于/意思是/指的是/所谓）
            ok = False
            for sent in re.split(r"[。！？\n]", body):
                if t in sent and re.search(r"(就是|等于|意思是|指的是|所谓|即|叫做|叫作)", sent):
                    ok = True
                    break
            if not ok:
                unexplained.append(t)
    # ★ 口径统一：最终以 check_script_quality 的单一来源实现为准
    #   （段内作用域 + EXPLAINED 白名单 + 同一组解释标记）
    unexplained = [u["term"] for u in
                   csq.find_unexplained_terms(body, csq.split_sections(main_body))]

    # 图片：引用数 / 文件是否存在 / 是否内嵌正文（而非全堆末尾）
    imgs = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", md)
    missing = [i for i in set(imgs)
               if not os.path.exists(os.path.join(base, i))]
    # 堆末尾检测：正文最后 15% 行内的图片占比
    lines = [l for l in md.split("\n") if l.strip()]
    tail_n = max(1, int(len(lines) * 0.15))
    tail_imgs = sum(1 for l in lines[-tail_n:] if l.strip().startswith("!["))
    body_imgs = len(imgs) - tail_imgs

    # 结构
    parts = re.findall(r"^# .+", md, re.M)
    part_titles = [p for p in parts if "第" in p and "期" in p]
    secs = [l for l in lines if l.startswith("## ") and not l[3:].strip().startswith("附")]
    has_toc = any("目录" in l for l in lines[:40])

    # 时长承诺 = **单期 8~10 分钟**（用户 2026-10-24 定调）；整篇 = 期数 × 单期
    n_parts = len(part_titles) or 1
    per_part_min = round(dur / 60 / n_parts, 1) if n_parts else 0.0
    lo, hi = TARGETS["duration_sec"]
    dur_ok = (lo <= per_part_min * 60 <= hi)
    appendices = [x for x in ("附一", "附二", "附三", "附四")
                  if any(f"## {x}" in l for l in lines)]

    # 表演脚本格式
    act_marks = re.findall(r"【[^】]{1,12}】", body)
    pauses = body.count("——")
    dur_tags = len(re.findall(r"[（(]?\s*约\s*[0-9.]+\s*分钟", body))

    # 重复（相邻段高重合）
    def grams(t, n=6):
        t = re.sub(r"[^一-鿿]", "", t)
        return set(t[i:i + n] for i in range(max(1, len(t) - n + 1)))
    rep = 0
    gs = [grams(t) for t in seg_texts]
    for i in range(len(gs)):
        for j in range(i + 1, len(gs)):
            if gs[i] and gs[j]:
                inter = len(gs[i] & gs[j])
                if inter / min(len(gs[i]), len(gs[j])) >= 0.5:
                    rep += 1

    return {
        "file": md_path,
        "cn_chars": cn,
        "duration_min": round(dur / 60, 1),
        "n_parts": n_parts,
        "per_part_min": per_part_min,
        "per_part_ok": dur_ok,
        "parts": len(part_titles),
        "sections": len(secs),
        "has_toc": has_toc,
        "appendices": appendices,
        "action_instructions": len(a_hits),
        "check_questions": len(q_hits),
        "analogy_raw": analogy_raw,
        "analogy_density": analogy_dense,
        "unexplained_terms": unexplained,
        "images_total": len(imgs),
        "images_missing": missing,
        "images_in_body": body_imgs,
        "images_at_tail": tail_imgs,
        "act_marks": len(act_marks),
        "pauses": pauses,
        "duration_tags": dur_tags,
        "repeat_pairs": rep,
        "filler_total": sum(body.count(k) for k in csq.FILLERS),
        "sample_q": q_hits[:3],
        "sample_a": a_hits[:2],
    }


def verdict(r):
    """按项目 TARGETS 打分。返回 (通过项数, 总项数, 问题列表)。"""
    probs = []
    ok = 0
    total = 6
    lo, hi = TARGETS["duration_sec"]
    if r.get("per_part_ok"):
        ok += 1
    else:
        probs.append(f"单期时长 {r['per_part_min']:.1f} 分钟不在 {lo//60}~{hi//60} 分钟"
                     f"（整篇 {r['duration_min']:.1f} 分钟 / {r['n_parts']} 期，"
                     f"按 {SPEED} 字/分折算）")
    if r["action_instructions"] >= 3:
        ok += 1
    else:
        probs.append(f"行动指令只有 {r['action_instructions']} 处（要求 ≥3）")
    if r["check_questions"] >= 3:
        ok += 1
    else:
        probs.append(f"检验性提问只有 {r['check_questions']} 处（要求 ≥3）")
    if r["analogy_density"] >= 8:
        ok += 1
    else:
        probs.append(f"类比覆盖 {r['analogy_density']} 段（要求 ≥8 段有类比）")
    if not r["unexplained_terms"]:
        ok += 1
    else:
        probs.append(f"未当场解释的术语 {len(r['unexplained_terms'])} 个: "
                     f"{'、'.join(r['unexplained_terms'])}")
    if not r["images_missing"] and r["images_total"] > 0:
        ok += 1
    else:
        probs.append(f"图片引用 {r['images_total']} 个，缺失文件 {len(r['images_missing'])} 个")
    return ok, total, probs


def main():
    files = sys.argv[1:]
    if not files:
        files = [
            os.path.join(ROOT, "outputs/课件_bench_BV1rXXfBiECa/讲课稿.md"),
            os.path.join(ROOT, "outputs/课件_doc_to_courseware_20261027_101745_a9f0c1/讲课稿.md"),
            os.path.join(ROOT, "outputs/课件_video_to_script_20261027_093859_1e1e65/讲课稿.md"),
        ]
    out = []
    for f in files:
        if not os.path.exists(f):
            print(f"跳过（不存在）: {f}")
            continue
        r = audit(f)
        ok, total, probs = verdict(r)
        out.append((r, ok, total, probs))
    with open(LOG, "w", encoding="utf-8") as fh:
        for r, ok, total, probs in out:
            fh.write("=" * 78 + "\n")
            fh.write(f"课件: {r['file']}\n")
            fh.write(json.dumps(r, ensure_ascii=False, indent=1) + "\n")
            fh.write(f"→ 达标 {ok}/{total}\n")
            for p in probs:
                fh.write(f"   ✗ {p}\n")
    # 控制台简报
    for r, ok, total, probs in out:
        print("=" * 78)
        print(f"课件: {os.path.basename(os.path.dirname(r['file']))}")
        print(f"  字数 {r['cn_chars']} / 整篇 {r['duration_min']} 分钟 / "
              f"**单期 {r['per_part_min']} 分钟**（{r['n_parts']} 期，"
              f"承诺单期 {TARGETS['duration_sec'][0]//60}~{TARGETS['duration_sec'][1]//60} 分钟）"
              f" / {r['parts']} 期 {r['sections']} 段 / 目录{'有' if r['has_toc'] else '无'} / "
              f"附录{r['appendices']}")
        print(f"  类比 覆盖{r['analogy_density']}段(原始标记{r['analogy_raw']}) "
              f"检验提问{r['check_questions']} 行动指令{r['action_instructions']} "
              f"未解释术语{len(r['unexplained_terms'])}")
        print(f"  图 {r['images_total']}（正文{r['images_in_body']}/尾部{r['images_at_tail']}）"
              f"缺失{len(r['images_missing'])} | 表演标记{r['act_marks']} "
              f"停顿{r['pauses']} 时长标注{r['duration_tags']} | 重复段对{r['repeat_pairs']}")
        print(f"  → 达标 {ok}/{total}")
        for p in probs:
            print(f"     ✗ {p}")
    print(f"\n日志: {LOG}")


if __name__ == "__main__":
    import json
    main()
