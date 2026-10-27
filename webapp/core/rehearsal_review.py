#!/usr/bin/env python3
"""课件 ↔ 试讲 逐段对齐评估引擎（闭环的核心）。

现状痛点：链路B 的评估是**通用指标**（时长/类比/提问数/重复率/画面可见率），
它不知道「这一段你到底讲了没有」「该问的你问了吗」「这张图你指了吗」。
本模块做真正的**逐段对账**：

  1. 解析课件 md → 段清单（期/段 + 该段要点 + 该段检验提问 + 该段行动指令 + 该段画面）
  2. LLM 把「试讲转写」逐段对齐到课件段 → 每段判定：
       覆盖(全/部分/未) / 漏了什么 / 说错了什么 / 检验提问是否问了
       行动指令是否给了 / 该指的画面是否指了 / 讲多了什么
  3. 算掌握度（覆盖率/忠实度/提问执行率/行动执行率/画面引用率/时长匹配）
  4. 产出逐段对账报告 + 下一轮改进稿

原则：所有判定必须有原文依据（转写里的原句），禁止泛泛而评。
"""
import json
import os
import re

from . import pipeline_ops as ops
from . import styles

# 复用链路B 的口径词表，保证两处判定一致
from .runner import CHECK_Q, ACTION

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


_SENT_SPLIT = re.compile(r"[。！？；\n]")
_DUR_RE = re.compile(r"[（(]?\s*约\s*([0-9.]+)\s*分钟")
_IMG_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")


# ============================================================ 1. 解析课件
def _sentences(text):
    return [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]


def _sentences_with(text, markers):
    return [s for s in _sentences(text) if any(m in s for m in markers)]


def parse_courseware_sections(md_text):
    """把课件 md 解析成段清单。

    识别规则（**层级无关**）：标题文字里含「第N期」→ 判为期；其余标题 → 判为段。
    不依赖 # 的个数，因为 LLM 稿的层级不总守约定（写过 `## 第N期` + `### 段标题`），
    死认 ## 会把期当成段 → 掌握度失真。
    附录（`## 附…`）与目录不作为正文段。
    返回 [{part, title, body, key_point, check_questions, actions, pictures, duration_min}]
    """
    sections = []
    part = ""
    cur = None
    _part_re = re.compile(r"^第\s*[0-9一二三四五六七八九十]+\s*期")
    for ln in md_text.split("\n"):
        s = ln.strip()
        m = re.match(r"^#{1,6}\s+(.*)$", s)
        if m:
            title = m.group(1).strip()
            if title.startswith("附"):
                break                      # 附录之后不作为正文段
            if title.startswith("目录"):
                continue                   # 目录表不是正文段
            if _part_re.match(title) or title[:6].endswith("期"):
                part = title               # 这是「期」
                continue
            cur = {"part": part, "title": title, "body": "",
                   "pictures": [], "duration_min": None}
            sections.append(cur)
            continue
        if cur is None:
            continue
        cur["body"] += ln + "\n"

    for s in sections:
        s["body"] = s["body"].strip()
        s["pictures"] = _IMG_RE.findall(s["body"])
        m = _DUR_RE.search(s["body"])
        if m:
            try:
                s["duration_min"] = float(m.group(1))
            except ValueError:
                pass
        s["check_questions"] = _sentences_with(s["body"], CHECK_Q)
        s["actions"] = _sentences_with(s["body"], ACTION)
        # 要点摘要：正文前 100 字（够 LLM 判断覆盖与否）
        s["key_point"] = re.sub(r"\s+", " ", s["body"])[:100]
    return sections


def build_spec(sections, offset=0):
    """给 LLM 的紧凑段清单（每段一行，够判断覆盖与遗漏）。"""
    lines = []
    for i, s in enumerate(sections, 1):
        q = ("｜检验提问:" + s["check_questions"][0][:30]) if s["check_questions"] else ""
        a = ("｜行动指令:" + s["actions"][0][:30]) if s["actions"] else ""
        p = ("｜该指画面:" + s["pictures"][0].split("/")[-1]) if s["pictures"] else ""
        lines.append(f"[{i + offset}] {s['part']} / {s['title']}｜要点: {s['key_point']}{q}{a}{p}")
    return "\n".join(lines)


# ============================================================ 2. LLM 逐段对齐
_ALIGN_SYS = """你是试讲评估官。对比「课件段清单」与「学员试讲转写」，逐段判定讲得怎么样。

判定必须**有转写原句依据**，禁止泛泛而评。每个课件段输出：
{
 "seg": 段序号(整数，与清单 [n] 对应),
 "covered": "full" | "partial" | "none",
 "covered_points": ["讲到的要点(转写原句片段，最多3条)"],
 "missed_points": ["该讲但没讲的要点(最多3条)"],
 "wrong": [{"said":"转写原句","issue":"与课件/公认规则冲突之处","fix":"应该怎么说"}],
 "extra": "讲多了/跑偏的内容(没有则空字符串)",
 "check_question_asked": true/false,   // 课件该段有检验提问时，学员是否真的问了
 "action_instruction_given": true/false,// 课件该段有行动指令时，学员是否真的给了
 "picture_referred": true/false,        // 课件该段配了画面，学员讲到这里时是否提到了/指了这张图
 "comment": "一句话点评(不超过30字，具体到内容，不说套话)"
}
再输出 "summary": {"strong":"讲得好的地方","weak":"最该改的3件事","overall":"总评"}。

只输出 JSON。保持紧凑，不要复述课件原文。"""


def align_rehearsal(cfg, sections, transcript, log=None, chunk_size=8,
                    max_tokens=8000):
    """试讲转写 → 逐段判定。返回 {segments:[...], summary:{...}}

    ★ 分批：一次输出 20+ 段的判定 JSON 会撞 max_tokens 被**截断**（实测 22 段时
    JSON 断在半路解析失败）。所以按 chunk_size 分批调用，段号加偏移拼回。
    ★ 兜底：万一还是被截断，从残缺 JSON 里抢救出**完整**的段对象，不整批丢弃。
    """
    if not sections:
        return {"segments": [], "summary": {}}
    all_segs = []
    summary = {}
    n = len(sections)
    for start in range(0, n, chunk_size):
        chunk = sections[start:start + chunk_size]
        spec = build_spec(chunk, offset=start)
        user = (f"【课件段清单】（本批 {len(chunk)} 段，编号 {start+1}~{start+len(chunk)}）\n"
                f"{spec}\n\n【学员试讲转写】\n{transcript}\n\n"
                f"请逐段判定，输出 JSON（每段 missed_points 最多 3 条、wrong 最多 2 条）。"
                f"只输出 JSON，不要任何解释文字。")
        raw = None
        data = None
        for attempt in range(2):          # 失败重试一次：LLM 输出是非确定的
            raw = ops.call_llm(cfg, _ALIGN_SYS, user, log=log, max_tokens=max_tokens)
            data = _extract_json(raw)
            if isinstance(data, dict) and "segments" in data:
                break
            if isinstance(data, list):
                data = {"segments": data, "summary": {}}
                break
            if attempt == 0:
                log(f"  对账第 {start+1}~{start+len(chunk)} 段 JSON 解析失败，"
                    f"重试一次（原文前 200 字：{(raw or '')[:200]!r}）")
                continue
            salvaged = _salvage_segments(raw)
            if salvaged:
                log(f"  对账第 {start+1}~{start+len(chunk)} 段：JSON 仍不完整，"
                    f"抢救出 {len(salvaged)} 段完整判定")
                data = {"segments": salvaged, "summary": {}}
            else:
                log(f"  对账第 {start+1}~{start+len(chunk)} 段解析失败，本批跳过"
                    f"（原文前 200 字：{(raw or '')[:200]!r}）")
        if not data:
            continue
        for v in data.get("segments", []):
            try:
                v["seg"] = int(v.get("seg", 0)) + start
            except (TypeError, ValueError):
                pass
            all_segs.append(v)
        if not summary and data.get("summary"):
            summary = data["summary"]
        if log:
            log(f"  对账第 {start+1}~{start+len(chunk)} 段完成（累计 {len(all_segs)} 段）")
    return {"segments": all_segs, "summary": summary}


def _salvage_segments(text):
    """从被截断的 JSON 文本里抢救出所有**完整**的段对象。"""
    if not text:
        return []
    objs = _salvage_objects(text)
    out = []
    for o in objs:
        if isinstance(o, dict) and ("covered" in o or "seg" in o):
            out.append(o)
    return out


def _salvage_objects(text):
    """扫描文本，抓出所有括号配平、可被 json.loads 的 {...} 对象。"""
    out = []
    depth = 0
    start = None
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                frag = text[start:i + 1]
                try:
                    out.append(json.loads(frag))
                except Exception:
                    pass
                start = None
    return out


def _extract_json(text):
    if not text:
        return None
    s = text.strip()
    if "```" in s:
        m = re.search(r"```(?:json)?\s*([\{\[].*?[\}\]])\s*```", s, re.S)
        if m:
            s = m.group(1)
    cands = []
    for lo, hi in (("{", "}"), ("[", "]")):
        a, b = s.find(lo), s.rfind(hi)
        if a != -1 and b != -1 and b > a:
            cands.append((a, b))
    if not cands:
        return None
    a, b = min(cands, key=lambda x: x[0])
    try:
        return json.loads(s[a:b + 1])
    except Exception:
        try:
            return json.loads(s[a:b + 1].replace(",}", "}").replace(",]", "]"))
        except Exception:
            return None


# ============================================================ 3. 掌握度
def compute_mastery(verdicts, sections, actual_sec=None, planned_sec=None):
    """算掌握度。verdicts: 对齐结果 segments 列表。返回各维度 + 总分(0~100)。"""
    vs = verdicts.get("segments", []) or []
    n = max(1, len(sections))
    by_idx = {}
    for v in vs:
        try:
            by_idx[int(v.get("seg", -1))] = v
        except (TypeError, ValueError):
            continue

    cov_sum = 0.0
    n_cov = 0
    wrong_cnt = 0
    q_ask = q_have = 0
    a_give = a_have = 0
    p_ref = p_have = 0
    detail = []
    for i, s in enumerate(sections, 1):
        v = by_idx.get(i, {})
        c = v.get("covered", "none")
        w = {"full": 1.0, "partial": 0.5}.get(c, 0.0)
        cov_sum += w
        n_cov += 1
        wrongs = v.get("wrong") or []
        wrong_cnt += len(wrongs)
        if s["check_questions"]:
            q_have += 1
            if v.get("check_question_asked"):
                q_ask += 1
        if s["actions"]:
            a_have += 1
            if v.get("action_instruction_given"):
                a_give += 1
        if s["pictures"]:
            p_have += 1
            if v.get("picture_referred"):
                p_ref += 1
        detail.append({
            "idx": i, "part": s["part"], "title": s["title"],
            "covered": c, "coverage": w,
            "missed": v.get("missed_points") or [],
            "wrong": wrongs, "extra": v.get("extra", ""),
            "check_q": bool(s["check_questions"]), "check_q_asked": bool(v.get("check_question_asked")),
            "action": bool(s["actions"]), "action_given": bool(v.get("action_instruction_given")),
            "picture": bool(s["pictures"]), "picture_ref": bool(v.get("picture_referred")),
            "comment": v.get("comment", ""),
        })

    coverage = cov_sum / n_cov if n_cov else 0.0
    fidelity = max(0.0, 1.0 - wrong_cnt / n_cov) if n_cov else 0.0
    q_rate = q_ask / q_have if q_have else None
    a_rate = a_give / a_have if a_have else None
    p_rate = p_ref / p_have if p_have else None
    dur_fit = None
    if actual_sec and planned_sec:
        ratio = actual_sec / planned_sec
        dur_fit = max(0.0, 1.0 - min(1.0, abs(ratio - 1.0) / 0.5))   # 差 50% 以内线性扣分

    # 总分：覆盖 40% + 忠实 20% + 提问 15% + 行动 15% + 画面 10%
    parts = [coverage * 40, fidelity * 20]
    parts.append((q_rate if q_rate is not None else 0.6) * 15)
    parts.append((a_rate if a_rate is not None else 0.6) * 15)
    parts.append((p_rate if p_rate is not None else 0.6) * 10)
    score = round(sum(parts), 1)
    return {
        "score": score,
        "coverage": round(coverage, 3),
        "fidelity": round(fidelity, 3),
        "check_q_rate": round(q_rate, 3) if q_rate is not None else None,
        "action_rate": round(a_rate, 3) if a_rate is not None else None,
        "picture_rate": round(p_rate, 3) if p_rate is not None else None,
        "duration_fit": round(dur_fit, 3) if dur_fit is not None else None,
        "wrong_count": wrong_cnt,
        "segments_total": len(sections),
        "segments_covered": sum(1 for d in detail if d["covered"] == "full"),
        "segments_partial": sum(1 for d in detail if d["covered"] == "partial"),
        "segments_missed": sum(1 for d in detail if d["covered"] == "none"),
        "detail": detail,
        "summary": verdicts.get("summary", {}) or {},
    }


def mastery_grade(score):
    if score >= 85:
        return "A · 已掌握"
    if score >= 70:
        return "B · 基本掌握，需补漏"
    if score >= 55:
        return "C · 半懂，需重讲重点段"
    return "D · 未掌握，建议按课件重练"


# ============================================================ 4. 报告
def build_alignment_report(mastery, sections, meta):
    L = []
    A = L.append
    A(f"# 试讲对账报告 · {meta.get('title','课件')}")
    A("")
    A(f"> 课件：`{meta.get('courseware','')}` ｜ 试讲：{meta.get('trial','')} ｜ "
      f"实际时长 {meta.get('actual_min', 0):.1f} 分钟 ｜ 生成时间 {meta.get('time','')}")
    A("")
    A("## 一、结论")
    A("")
    A(f"**掌握度 {mastery['score']} 分 —— {mastery_grade(mastery['score'])}**")
    A("")
    A("| 维度 | 得分 | 说明 |")
    A("|---|---|---|")
    A(f"| 知识点覆盖 | {mastery['coverage']*100:.0f}% | 全覆盖 {mastery['segments_covered']} 段 / "
      f"部分 {mastery['segments_partial']} 段 / 未讲 {mastery['segments_missed']} 段 |")
    A(f"| 忠实度 | {mastery['fidelity']*100:.0f}% | 与课件/规则冲突 {mastery['wrong_count']} 处 |")
    A(f"| 检验提问执行 | "
      f"{(f'{mastery['check_q_rate']*100:.0f}%' if mastery['check_q_rate'] is not None else '—')} | "
      f"课件设了提问的段落里，你实际问出来的比例 |")
    A(f"| 行动指令执行 | "
      f"{(f'{mastery['action_rate']*100:.0f}%' if mastery['action_rate'] is not None else '—')} | "
      f"该给的动作有没有给出去 |")
    A(f"| 画面引用 | "
      f"{(f'{mastery['picture_rate']*100:.0f}%' if mastery['picture_rate'] is not None else '—')} | "
      f"讲到该指图时有没有指 |")
    if mastery["duration_fit"] is not None:
        A(f"| 时长匹配 | {mastery['duration_fit']*100:.0f}% | 与课件预估时长的贴合度 |")
    A("")
    s = mastery.get("summary") or {}
    if s.get("strong"):
        A(f"**讲得好的**：{s['strong']}")
        A("")
    if s.get("weak"):
        A(f"**最该改的**：{s['weak']}")
        A("")
    if s.get("overall"):
        A(f"**总评**：{s['overall']}")
        A("")

    A("## 二、逐段对账")
    A("")
    A("| # | 期/段 | 覆盖 | 漏讲 | 说错 | 提问 | 行动 | 画面 | 点评 |")
    A("|---|---|---|---|---|---|---|---|---|")
    for d in mastery["detail"]:
        cov = {"full": "✅ 全", "partial": "🟡 部分", "none": "❌ 未讲"}.get(d["covered"], d["covered"])
        def tick(need, did):
            if not need:
                return "—"
            return "✅" if did else "❌"
        A(f"| {d['idx']} | {d['part']} / {d['title']} | {cov} | "
          f"{len(d['missed'])} | {len(d['wrong'])} | "
          f"{tick(d['check_q'], d['check_q_asked'])} | {tick(d['action'], d['action_given'])} | "
          f"{tick(d['picture'], d['picture_ref'])} | {d['comment']} |")
    A("")

    A("## 三、要改的具体地方")
    A("")
    A("### ① 漏讲（按段）")
    A("")
    any_miss = False
    for d in mastery["detail"]:
        if d["missed"]:
            any_miss = True
            A(f"- **{d['part']} / {d['title']}**")
            for m in d["missed"]:
                A(f"  - 漏：{m}")
    if not any_miss:
        A("- （无）")
    A("")
    A("### ② 说错/讲歪（按段）")
    A("")
    any_wrong = False
    for d in mastery["detail"]:
        if d["wrong"]:
            any_wrong = True
            A(f"- **{d['part']} / {d['title']}**")
            for w in d["wrong"]:
                A(f"  - 你说：{w.get('said','')}")
                A(f"  - 问题：{w.get('issue','')}")
                A(f"  - 应该：{w.get('fix','')}")
    if not any_wrong:
        A("- （无）")
    A("")
    A("### ③ 讲多了/跑偏")
    A("")
    any_extra = False
    for d in mastery["detail"]:
        if d["extra"]:
            any_extra = True
            A(f"- **{d['part']} / {d['title']}**：{d['extra']}")
    if not any_extra:
        A("- （无）")
    A("")
    A("---")
    A("")
    A("*由 `rehearsal_review` 自动生成，判定依据为试讲转写原句。*")
    return "\n".join(L)


# ============================================================ 5. 下一轮改进稿
_NEXT_SYS = """你是讲课稿改写助手。基于「原课件」+「试讲对账结果」，产出**下一版**讲课稿。

改写原则（只动对账指出的问题，别把整篇重写）：
1. 漏讲的段：把漏掉的要点**补进**该段，用承接句自然过渡，标注【补：…】。
2. 说错的段：按「应该怎么说」改写该段对应句子，保留原结构与画面引用。
3. 讲歪的段：删掉跑偏内容，补回该段应有的要点。
4. 检验提问/行动指令/画面没执行的段：把该段该问的问、该给的动作明确写出来（用【动作】提示 + —— 停顿）。
5. 保持原课件的「期 / 段」结构、【动作】表演提示、停顿 ——、段末时长标注、附录结构不变。
6. **不要**编造课件里没有的知识点。

输出完整 markdown 课件正文（包含期/段标题与内嵌图片引用）。"""


def build_next_draft(cfg, courseware_md, mastery, style_name="faithful", intensity=3, log=None):
    """按对账结果产出下一版讲课稿。"""
    prob = []
    for d in mastery["detail"]:
        bits = []
        if d["missed"]:
            bits.append("漏讲：" + "；".join(d["missed"][:4]))
        for w in d["wrong"]:
            bits.append(f"说错（{w.get('said','')[:30]}）→应说 {w.get('fix','')[:60]}")
        if d["extra"]:
            bits.append("跑偏：" + d["extra"][:60])
        if d["check_q"] and not d["check_q_asked"]:
            bits.append("该段设了检验提问但没问出来")
        if d["action"] and not d["action_given"]:
            bits.append("该段有行动指令但没给")
        if d["picture"] and not d["picture_ref"]:
            bits.append("该段配了画面但没指")
        if bits:
            prob.append(f"【{d['part']} / {d['title']}】" + "；".join(bits))
    if not prob:
        prob = ["（对账未发现明显问题，仅做口语润色）"]
    user = ("【本次试讲对账发现的问题】\n" + "\n".join(f"- {x}" for x in prob)
            + "\n\n【原课件】\n" + courseware_md + "\n\n请产出改进后的完整讲课稿 markdown。")
    raw = ops.call_llm(cfg, _NEXT_SYS, user, log=log)
    if not raw:
        raise RuntimeError("改进稿 LLM 调用失败（双通道均不可用）")
    return ops.extract_markdown(raw)
