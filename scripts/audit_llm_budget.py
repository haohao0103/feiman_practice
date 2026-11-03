#!/usr/bin/env python3
"""LLM 预算体检：逐个调用点跑一次真实 payload，看 token 预算够不够。

★ 为什么要有这个：reasoning 模型（MiMo）**思考 token 计入 max_tokens**，
  给紧了 → finish_reason='length' → content 空 → 看起来像"没话说"。
  实测：事实核查 8000 预算 → 思考烧满 8000 → 空返回（改了分批+关思考+24000 才稳）。

判定标准（看 usage 里的思考占比）：
  危险：finish_reason='length' 或 completion 触顶（≥上限的 98%）
  偏紧：思考占比 > 60%（再涨一点就要触顶）
  宽裕：其余

用法：python3 scripts/audit_llm_budget.py [转写txt]
"""
import io
import json
import os
import re
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from webapp.core import config as cfgmod            # noqa: E402
from webapp.core import courseware_gen as cg        # noqa: E402
from webapp.core import pipeline_ops as ops         # noqa: E402
from webapp.core import rehearsal_review as rr      # noqa: E402

DEFAULT_ASR = os.path.join(ROOT, "work", "projects",
                           "video_to_script_20261103_002912_1810f9", "asr.txt")
LATEST_MD = os.path.join(ROOT, "outputs",
                         "课件_video_to_script_20261103_013845_3b452c", "讲课稿.md")


def probe(label, cfg, system, user, max_tokens, thinking=None):
    """打一次 API，返回 (耗时, usage, finish_reason, content长度)。"""
    channels = cfgmod.llm_channels(cfg)
    base, key, model, _lb = channels[0]
    payload = {"model": model,
               "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": user}],
               "temperature": cfg["llm_temperature"], "max_tokens": max_tokens}
    if thinking is not None:
        payload["thinking"] = thinking
    req = urllib.request.Request(
        base + "/chat/completions", data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}", "api-key": key})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=cfg["llm_timeout_long"]) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"  {label:18} 调用失败 {type(e).__name__}: {str(e)[:70]}")
        return None
    dt = time.time() - t0
    ch = (data.get("choices") or [{}])[0]
    content = (ch.get("message") or {}).get("content") or ""
    u = data.get("usage") or {}
    comp = u.get("completion_tokens") or 0
    think = (u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
    ratio = (think * 100 // comp) if comp else 0
    touch = comp >= max_tokens * 0.98
    headroom = (max_tokens / comp) if comp else 99.0
    # ★ 判据看**余量**（会不会触顶），思考占比只作参考 ——
    #   思考占 78% 但只用了 1416/12000（余量 8.5 倍）→ 宽裕；
    #   思考占 33% 但用了 7900/8000（余量 1.01 倍）→ 危险。
    if ch.get("finish_reason") == "length" or touch:
        verdict = "危险（触顶/被截断）"
    elif headroom < 2:
        verdict = f"偏紧（余量仅 {headroom:.1f} 倍，思考占 {ratio}%）"
    else:
        verdict = f"宽裕（余量 {headroom:.1f} 倍，思考占 {ratio}%）"
    print(f"  {label:18} {dt:5.0f}s | 上限 {max_tokens:>6} | 用 {comp:>6}"
          f"（思考 {think:>6} = {ratio:>3}%）| prompt {u.get('prompt_tokens')}"
          f" | 输出 {len(content):>5} 字 | finish={ch.get('finish_reason')}"
          f" | {verdict}")
    return {"dt": dt, "comp": comp, "think": think, "ratio": ratio,
            "verdict": verdict, "finish": ch.get("finish_reason")}


def main():
    cfg = cfgmod.load()
    asr_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ASR
    transcript = open(asr_path, encoding="utf-8").read()
    script = ""
    if os.path.exists(LATEST_MD):
        t = open(LATEST_MD, encoding="utf-8").read()
        m = re.search(r"^##\s*附一", t, re.M)
        script = t[:m.start()] if m else t
    print("LLM 预算体检（真实 payload）")
    print(f"  转写 {len(transcript)} 字符 | 成稿 {len(script)} 字符\n")

    rows = []

    # ① 大纲
    u = (f"视频时长约 20.0 分钟。以下是带时间戳的中文转写：\n\n{transcript}\n\n"
         f"请输出结构化大纲 JSON。")
    r = probe("① 大纲", cfg, cg._outline_sys(cfg), u,
              cfg.get("outline_max_tokens", 16000))
    if r:
        rows.append(("大纲", r))

    # ② 成稿（保留思考 —— 创作任务，质量优先）
    if script:
        outline = {"parts": [{"title": "第1期", "segments": [
            {"title": "示例段", "start_sec": 0, "end_sec": 60,
             "summary": "示例", "frame_at_sec": 30}]}]}
        u2 = (f"【分段大纲】\n{json.dumps(outline, ensure_ascii=False)}\n\n"
              f"【原始素材】\n{transcript}\n\n请产出完整讲课逐字稿 markdown。")
        r = probe("② 成稿", cfg, cg._script_sys(cfg), u2,
                  cfg.get("script_max_tokens", 20000))
        if r:
            rows.append(("成稿", r))

    # ③ 事实核查（已改成关思考）
    if script:
        segs = cg._split_script_segments(script)
        batch = segs[:5]
        body = "\n\n".join(s[3] for s in batch)
        u3 = (f"【原始转写】\n{transcript}\n\n【本批要核查的段落】\n{body}\n\n"
              f"只核查本批这几段的问题，输出 JSON 列表。")
        r = probe("③ 事实核查", cfg, cg._FACT_SYS, u3,
                  cfg.get("factcheck_max_tokens", 24000),
                  thinking={"type": "disabled"})
        if r:
            rows.append(("事实核查", r))

    # ④ 外科式修补（动作清单）
    if script:
        u4 = ("请补写「课后可执行动作清单」这一节。\n\n" + script[:6000])
        r = probe("④ 外科式修补", cfg, cg._REPAIR_SYS, u4,
                  cfg.get("repair_max_tokens", 12000))
        if r:
            rows.append(("外科式修补", r))

    # ⑤ 改进稿
    if script:
        secs = rr.parse_courseware_sections(script)
        u5 = (f"【课件段落清单】\n{chr(10).join(s['title'] for s in secs[:8])}\n\n"
              f"【学员试讲转写片段】\n这是转写内容\n\n请产出下一版改进稿。")
        r = probe("⑤ 改进稿", cfg, rr._NEXT_SYS, u5,
                  cfg.get("next_draft_max_tokens", 24000))
        if r:
            rows.append(("改进稿", r))

    print("\n结论：")
    bad = [n for n, r in rows if "危险" in r["verdict"]]
    tight = [n for n, r in rows if "偏紧" in r["verdict"]]
    for n, r in rows:
        print(f"  {n:12} {r['verdict']}")
    print()
    if bad:
        print(f"  ✗ 危险项：{bad} → 必须加预算（否则会空返回）")
    elif tight:
        print(f"  ! 偏紧项：{tight} → 建议再加预算")
    else:
        print("  ✓ 全部宽裕")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
