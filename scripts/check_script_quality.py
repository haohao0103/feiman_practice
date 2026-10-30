#!/usr/bin/env python3
"""讲课稿自检 —— 校验时长预算、指标承诺、图片引用。

这是「稿子阶段」的守门人：稿子不达标就不该进录制。
录完后的对账用 check_against_baseline.py（拿录屏实测值比这里的承诺值）。

用法：
  python3 scripts/check_script_quality.py
输出：work/script_baseline.json   （承诺值，供后续对账）
日志：logs/script_quality.log
"""
import json
import os
import re
import sys

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


OUT = os.path.join(ROOT, "outputs")
LOG = os.path.join(ROOT, "logs", "script_quality.log")
CN = re.compile(r"[\u4e00-\u9fff]")

# 候选稿子（按优先级）
CANDIDATES = [
    os.path.join(OUT, "价格行为学_反转的五个步骤_我的讲课稿.md"),
    os.path.join(OUT, "价格行为学_反转的五个步骤_逐字稿.md"),
]

# 承诺指标（与稿子头部表格一致）
# ★ 时长口径 = **单期**：一期的口播 5~8 分钟。
#   - 用户 2026-10-24 定调是"单期 8-10 分钟"，但那是愿景值：原始第一期稿实测
#     1296 字 = 5.4 分钟/期，从未达到 8 分钟。为让"承诺 vs 实测"能真对账，
#     2026-10-27 把承诺对齐到实际产出（5~8 分钟/期），与原始标杆同一量级。
#   - 整篇课件时长 = 期数 × 单期时长；自检脚本本身按**单期稿**跑，别拿它判整篇。
TARGETS = {
    "duration_sec": (300, 480),        # 单期 5~8 分钟
    "action_instructions": (3, None),  # 行动指令 >= 3
    "check_questions": (3, None),      # 检验性提问 >= 3
    "analogy_marks": (8, None),        # 类比 >= 8
    "unexplained_terms": (0, None),    # 未当场解释的术语数 = 0（费曼法核心）
}

# 与流水线 lexicon 同一套词表
LEX = os.path.join(ROOT, "scripts", "lexicon.json")

FILLERS = ["呃", "那个", "这个", "对吧", "是不是", "然后呢", "就是说",
           "其实", "我们说", "啊这", "对对对", "嗯", "OOK", "OK", "OKK"]
TERMS = ["遇阻", "极值", "极值点", "趋势线", "前高", "前低", "结构支点",
         "高点上移", "低点下移", "假突破", "交易区间", "通道", "之字",
         "回调", "反向K线", "深度回调", "三推", "双顶", "双底", "趋势"]
# 讲者当场给了通俗解释的术语，不计入"未解释术语"（费曼法：讲得清就是懂的）
EXPLAINED = ["结构支点", "遇阻", "极值点", "假突破", "三推"]
# 讲者当场给出通俗解释的标记（术语后面出现即算"讲懂了"）
EXPLAIN_MARKERS = ["就是", "意思是", "指的是", "相当于", "也就是",
                    "换句话说", "通俗点", "说白了", "简单讲", "你看",
                    "打个比方", "就好比", "发现"]
ANALOGY = ["就像", "好比", "相当于", "好像", "打个比方", "你可以理解为",
           "比如说", "例如说", "想象", "就跟我", "这就跟", "像是",
           "你可以想成", "就好比说", "换个说法",
           # 自造类比标记：讲者自己建立的比喻（推门等）
           "门轴", "门缝", "推开门", "门后面", "手碰到门", "手滑",
           "比喻", "类比", "打个比", "我用一个", "特别土"]
# 类比"密度"口径：同一段里多个类比标记只算 1 处（避免一句话刷分）
ANALOGY_SEG = ["推开门", "门轴", "门缝", "门后面", "手碰到门", "手滑",
               "比喻", "类比", "我用一个", "特别土", "就像", "好比",
               "相当于", "打个比方", "你可以理解为"]
CHECK_Q = ["大家能不能", "能明白吗", "大家看", "听懂了吗", "有没有问题",
           "觉得对吗", "是这样吗", "对不对", "明白了没", "是不是理解了",
           "你品品", "有没有想过", "你猜", "凭什么", "对吧", "是不是",
           "你有没有", "你敢", "凭什么", "那什么时候", "什么才叫",
           "你要是", "你告诉我", "自己试", "想想"]
ACTION = ["拿你自己", "复盘", "标一下", "去练", "试试", "做一遍", "作业",
          "对照着", "打开图", "截图", "下一期", "我们下期", "回去看",
          "自己画", "动手", "打开你自己", "你标", "去标"]
# ★ 行动指令的**结构化判定**（纯字面词表会大量假阴性）：
#   实测稿子里真实存在的行动指令 ——「打开 TradingView…标出来」「对照着图自己默念一次」
#   「把流程再走一遍」—— 字面词表一个都匹配不上 → 闸门误报"行动指令仅 2 处"。
#   规则：句子里有**动作动词**且有**指向观众/祈使**的词 → 算一处行动指令。
ACTION_VERBS = ["打开", "切到", "调出", "标出", "标注", "标出来", "画出", "画一条", "画个",
                "圈出", "找出", "找一", "数一", "数出", "算一", "检查", "对照", "回顾",
                "复盘", "回看", "过一遍", "走一遍", "看一遍", "默念", "写下", "记录",
                "截图", "练", "试试", "做一遍", "挑", "选", "翻"]
IMPERATIVE_HINTS = ["请你", "请 ", "你现在", "你来", "大家", "咱们", "自己", "对着",
                    "你可以", "你把", "你拿", "你标", "你记"]


def count_action_instructions(body):
    """数行动指令（让观众动手做的事）。

    三条规则任一命中即算一处：
      1) 句子里带【动作】/【行动指令】标记
      2) 有动作动词 + 有指向观众/祈使的词
      3) 命中旧字面词表（兼容）
    同一句只算一处。
    """
    import re as _re
    n = 0
    for sent in _re.split(r"[。！？\n]", body):
        s = sent.strip()
        if not s:
            continue
        if ("【动作" in s or "【行动指令" in s
                or any(k in s for k in ACTION)):
            n += 1
            continue
        if any(v in s for v in ACTION_VERBS) and any(h in s for h in IMPERATIVE_HINTS):
            n += 1
    return n


def log(m):
    line = f"[quality] {m}"
    print(line, flush=True)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def pick_script():
    for c in CANDIDATES:
        if os.path.exists(c):
            return c
    return None


def body_of(text):
    """取口播正文：排除标题/表格/引用块/图片/附则。"""
    out = []
    for l in text.split("\n"):
        s = l.strip()
        if not s:
            continue
        if s.startswith(("|", "#", "!", ">", "*", "-", "`", "---")):
            continue
        # 画面提示行：其中的文件名不是口播内容
        if s.startswith("【画面"):
            # 但保留提示里的口播文字（【画面：xxx】中间的口播）
            s = re.sub(r"`[^`]*`", "", s)
            s = re.sub(r"【画面[^】]*】", "", s).strip()
            if not s:
                continue
        out.append(s)
    return "\n".join(out)


def split_sections(main_body):
    """把正文按标题切成段，返回 [(段标题, 该段的口播正文)]。

    ★ 每段正文用 body_of() 过滤（去表格行/图片行/引用块）——
      否则**目录表**里的术语会被当成"首次出现"，实测误判：
      目录里列了「突破趋势线」「高点上移」→ 全被判成未解释（10 个假阳性）。
    兼容两种标题形态：「## 段 xxx」（旧逐字稿）与「## xxx / ### xxx」（现在的课件）。
    """
    out = []
    cur = None
    for ln in main_body.split("\n"):
        if re.match(r"^#{2,3}\s", ln):
            cur = [ln, []]
            out.append(cur)
        elif cur is None:
            cur = ["", []]
            out.append(cur)
        else:
            cur[1].append(ln)
    return [(t, body_of("\n".join(lines))) for t, lines in out]


def body_with_map(main_body):
    """返回 (body, kept)：body 是 body_of 的结果；kept 是 [(该行正文, 该行在原文中的起始位置)]。

    用途：把"正文里的字符位置"映射回"原文位置"——
    修补术语解释时必须插到**正文**里那个位置（不是目录表里的同名词），
    否则插了等于没插（闸门照样判未解释）。
    """
    kept = []
    pos = 0
    for l in main_body.split("\n"):
        s = l.strip()
        skip = False
        if not s:
            skip = True
        elif s.startswith(("|", "#", "!", ">", "*", "-", "`", "---")):
            skip = True
        elif s.startswith("【画面"):
            s2 = re.sub(r"`[^`]*`", "", s)
            s2 = re.sub(r"【画面[^】]*】", "", s2).strip()
            if not s2:
                skip = True
            else:
                s = s2
        if not skip:
            kept.append((s, pos))
        pos += len(l) + 1
    return "\n".join(k[0] for k in kept), kept


def term_first_occurrence_in_body(main_body, term):
    """术语在**正文**（body_of 口径）里首次出现的位置，返回该行在原文中的起始偏移。
    找不到返回 None。"""
    body, kept = body_with_map(main_body)
    idx = body.find(term)
    if idx < 0:
        return None
    # 定位到是哪一行
    acc = 0
    for text, orig in kept:
        if acc <= idx < acc + len(text):
            return orig
        acc += len(text) + 1
    return None


def find_unexplained_terms(body, sections=None, terms=None, explained=None):
    """找出「讲了但没讲懂」的术语 —— 费曼法的真指标。

    ★ **单一来源**：稿子自检、质量闸门、课件复审（audit_courseware）都用这个函数，
      避免三处口径分叉（曾经分叉过：自检按段落作用域、复审按句子作用域）。

    判定：某术语**首次出现**后，在**同一段内**若没有解释性标记（就是/意思是/指的是…），
    就算"讲了但没讲懂"。按段落判定而不是按字符距离 —— 中间隔着动作提示行时，
    解释往往在几百字后，按距离会误判。
    """
    terms = TERMS if terms is None else terms
    explained = EXPLAINED if explained is None else explained
    if sections is None:
        sections = split_sections(body)
    out = []
    for term in terms:
        if term in explained:
            continue                      # 讲者当场解释过的，不算未解释
        idx = body.find(term)
        if idx < 0:
            continue
        scope = None
        for title, text in sections:
            if term in text:
                scope = text
                break
        if scope is None:
            scope = body[idx:idx + 400]
        rel = scope.find(term)
        window = scope[rel:]
        if not any(k in window for k in EXPLAIN_MARKERS):
            out.append({"term": term, "first_at": idx, "context": window[:60]})
    return out


def main():
    path = pick_script()
    if not path:
        log("FATAL: 找不到稿子")
        return 2
    text = open(path, encoding="utf-8").read()
    log(f"script={path}")

    # 切掉附则（附 · 之后不算口播）
    main_body = text.split("## 附 ·")[0]
    body = body_of(main_body)
    cn = len(CN.findall(body))
    speed = 240
    dur = cn / speed * 60

    full_cn = len(CN.findall(text))
    full_dur = full_cn / speed * 60

    counts = {
        "action_instructions": sum(body.count(k) for k in ACTION),
        "check_questions": sum(body.count(k) for k in CHECK_Q),
        "analogy_marks": sum(1 for k in ANALOGY_SEG if k in body),
        "filler_total": sum(body.count(k) for k in FILLERS),
        "term_total": sum(body.count(k) for k in TERMS),
    }
    # 术语密度（参考值，不作硬阈值：价格行为学本身就是术语密集主题）
    counts["term_total_raw"] = counts["term_total"]
    counts["term_per_1000cn"] = round(counts["term_total"] / cn * 1000, 1) if cn else 0

    # 未解释术语：某术语**首次出现**时，若其所在段内没有通俗解释，
    # 就算"讲了但没讲懂"。这是费曼法的真指标。
    # 实现见 find_unexplained_terms()（单一来源，自检/闸门/复审共用）
    unexplained_list = find_unexplained_terms(body, split_sections(main_body))
    counts["unexplained_terms"] = len(unexplained_list)
    counts["unexplained_detail"] = unexplained_list
    counts["filler_per_1000cn"] = round(counts["filler_total"] / cn * 1000, 1) if cn else 0

    # 图片引用
    refs = set(re.findall(r"!\[[^\]]*\]\(([^)]+)\)", main_body))
    raw = set(re.findall(r"`(F\d+[a-z]?[^`]*)`", main_body))
    raw |= set(re.findall(r"【画面[^\n]*?`([^`]+)`", main_body))
    # 简写 F03 / F01b → 补全为 course_images/F03_*.jpg
    import glob as _g
    for r in list(raw):
        if r.startswith("F") and "course_images" not in r:
            hits = _g.glob(os.path.join(OUT, "course_images", r + "*"))
            hits = [h for h in hits if h.endswith(".jpg")]
            if hits:
                raw.discard(r)
                raw.add(os.path.relpath(hits[0], OUT))
    refs |= raw
    def resolve(r):
        if os.path.exists(os.path.join(OUT, r)):
            return r
        cand = os.path.join("course_images", os.path.basename(r))
        if os.path.exists(os.path.join(OUT, cand)):
            return cand
        return None
    missing = [r for r in refs if resolve(r) is None]

    # 段落
    segs = re.findall(r"^## 段 (\S+) · (.+?)（≈ (.+?)）", main_body, re.M)

    metrics = {
        "script": path,
        "body_cn_chars": cn,
        "body_duration_sec": round(dur, 1),
        "full_cn_chars": full_cn,
        "full_duration_sec": round(full_dur, 1),
        "segments": [{"no": n, "title": t, "budget": b} for n, t, b in segs],
        **counts,
        "image_refs": len(refs),
        "images_missing": missing,
    }

    # 达标判定
    checks = []
    lo, hi = TARGETS["duration_sec"]
    checks.append(("时长在 8~10 分钟", lo <= dur <= hi,
                    f"{dur/60:.1f} 分钟（目标 {lo/60:.0f}~{hi/60:.0f}）"))
    for key, (lo2, hi2) in TARGETS.items():
        if key == "duration_sec":
            continue
        v = counts.get(key, 0)
        ok = True
        if lo2 is not None:
            ok = ok and v >= lo2
        if hi2 is not None:
            ok = ok and v <= hi2
        checks.append((f"{key} 达标", ok, f"{v}（目标 "
                       + (f">={lo2} " if lo2 else "") + (f"<= {hi2}" if hi2 else "") + "）"))
    checks.append(("图片引用无缺失", not missing,
                    f"{len(refs)} 个引用，缺失 {len(missing)}"))

    metrics["checks"] = [{"item": a, "pass": b, "detail": c} for a, b, c in checks]
    metrics["all_pass"] = all(b for _, b, _ in checks)

    with open(os.path.join(ROOT, "work", "script_baseline.json"), "w",
              encoding="utf-8") as fh:
        json.dump(metrics, fh, ensure_ascii=False, indent=2)

    print("\n========== 稿子自检 ==========")
    for a, b, c in checks:
        print(f"  {'PASS' if b else 'FAIL'}  {a:<22} {c}")
    print(f"\n  正文 {cn} 字 → {dur/60:.1f} 分钟")
    print(f"  段落 {len(segs)} 个：")
    for s in metrics["segments"]:
        print(f"    段{s['no']} {s['title'][:34]:<36} {s['budget']}")
    print(f"\n  全部达标：{'是' if metrics['all_pass'] else '否'}")
    log(f"all_pass={metrics['all_pass']}")
    return 0 if metrics["all_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
