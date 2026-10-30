#!/usr/bin/env python3
"""通用化讲课课件流水线（自动化、不绑定具体视频）。

目标：任意 B 站 URL → 产出「标杆级别」讲课逐字稿课件（结构化分期、展开不压缩、
表演脚本格式、抽全讲义图、自制概念图、事实核查纠错、附录齐全），全程无人工校对 /
无硬编码段落 / 无预制图。

流程：
  1. segment_outline  —— LLM 读带时间戳转写，自动产出结构化大纲（期/段/起止秒/画面时刻/需图描述）
  2. extract_segment_frames —— 按大纲每段时间窗精准抽帧 + 墨迹量评分挑最佳（替代硬编码 SHOTS）
  3. generate_script  —— LLM 展开式生成结构化逐字稿（表演格式、内嵌画面与自制图占位）
  4. fact_check       —— LLM 二次比对原转写，产出「三处纠正」式纠错列表
  5. build_courseware —— 组装正稿 + 附录（画面清单/三处纠正/录制清单/回炉记录）+ PDF

自制图（task15）由 agent 在运行流中通过 ImageGen 生成后，以 image_gen 回调注入，
保证纯 python 侧不依赖外部工具也能跑通（无图则文本侧照常）。
"""
import json
import os
import re
import subprocess

from . import pipeline_ops as ops
from . import pdf_builder

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================== 1. 自动分段大纲
_OUTLINE_SYS = """你是一门硬核交易/金融教学视频的「课程架构师」。
给定视频的中文转写（带 [起-止] 秒 时间戳），请把它划分成结构化的讲课大纲，输出严格 JSON。

划分原则：
- 整体按内容密度分成若干「期」（如「第1期」「第2期」），每期一个明确主题。
- 每期下分若干「段」，每段：标题、起始秒、结束秒、本段要点摘要、该段最该插入画面的时刻frame_at_sec。
- 对需要「自制概念图」的节点，标记 needs_diagram=true 并写 diagram_desc。
  判定标准（宁缺毋滥）：只有当该段**原视频画面里没有现成图表**、且**一张自制图能明显讲清楚这个概念**时才标。
  原视频已经有清晰图表/讲义页的段落一律不标；同一期内最多标 1 张。
- ★ diagram_desc 的格式**必须**是这样（制图器按行解析，每行一个节点）：
  第一行：图类型+标题（≤20字，如「五步流程图」）
  第二行起：每行一个节点（≤30字），按讲解顺序
  例：
    五步流程图
    遇阻：出现阴线回调
    二次/三次测试极点
    突破趋势线
    突破主要高点或低点
    高点上移确认新趋势
- 段要切得足够细（一个核心概念一段），便于后续精准抽帧与讲稿对齐。

只输出 JSON，不要任何解释：
{
  "title": "课件总标题",
  "parts": [
    {"title": "第1期 · 主题", "segments": [
      {"title": "段标题", "start_sec": 12, "end_sec": 48, "summary": "要点",
       "frame_at_sec": 30, "needs_diagram": false, "diagram_desc": ""}
    ]}
  ]
}"""


def segment_outline(cfg, transcript, duration_sec, log=None):
    user = (f"视频时长约 {duration_sec/60:.1f} 分钟。以下是带时间戳的中文转写：\n\n"
            f"{transcript}\n\n请输出结构化大纲 JSON。")
    raw = ops.call_llm(cfg, _OUTLINE_SYS, user, log=log)
    if not raw:
        raise RuntimeError("分段大纲 LLM 调用失败（双通道均不可用）")
    outline = _extract_json(raw)
    if not outline or "parts" not in outline:
        raise RuntimeError("分段大纲 JSON 解析失败")
    return outline


def _extract_json(text):
    """从 LLM 回复里抠出第一个 JSON 对象或数组（容忍 ```json 包裹、尾部多余逗号）。

    关键修复：LLM 在 _FACT_SYS 里被要求「输出 JSON 列表」，常返回 ```json\\n[...]```，
    旧实现的正则只认 {…} 对象，遇到 […] 数组直接解析失败 → fact_check 静默返回 0 条。
    这里同时支持对象与数组。
    """
    if not text:
        return None
    s = text.strip()
    # 去掉 ```json ... ``` / ``` ... ``` 围栏
    if "```" in s:
        m = re.search(r"```(?:json)?\s*([\{\[].*?[\}\]])\s*```", s, re.S)
        if m:
            s = m.group(1)
    # 找最外层 { 或 [（选起始最靠前的那个，通常就是顶层结构）
    cands = []
    for lo, hi in (("{", "}"), ("[", "]")):
        a, b = s.find(lo), s.rfind(hi)
        if a != -1 and b != -1 and b > a:
            cands.append((a, b))
    if not cands:
        return None
    a, b = min(cands, key=lambda x: x[0])
    cand = s[a:b + 1]
    try:
        return json.loads(cand)
    except Exception:
        try:
            return json.loads(cand.replace(",}", "}").replace(",]", "]"))
        except Exception:
            return None


# ============================================================== 2. 分段精准抽帧
def _ink_score(path):
    """标注墨迹量评分：画面下半部暗像素越多（图表/文字密集）分越高。"""
    from PIL import Image
    import numpy as np
    try:
        im = np.asarray(Image.open(path).convert("L"))
    except Exception:
        return 0
    h, w = im.shape
    rm = im.mean(axis=1)
    return int((rm[int(h * 0.45):] < 90).sum())


def _frame_signature(path, size=32):
    """视觉指纹：32x32 灰度缩略 + 16 桶色相直方图。

    同一屏画面的不同瞬间（光标移动、标注渐次出现）指纹几乎不变；
    换一张图表/讲义页，指纹差异显著。用来判「是不是同一屏」。
    """
    from PIL import Image
    import numpy as np
    try:
        im = Image.open(path).convert("RGB").resize((size, size), Image.BILINEAR)
    except Exception:
        return None
    g = np.asarray(im.convert("L"), dtype=np.float32) / 255.0
    hsv = np.asarray(im.convert("HSV"), dtype=np.float32)
    hist = np.histogram(hsv[..., 0], bins=16, range=(0, 256))[0].astype(np.float32)
    hist = hist / max(1.0, hist.sum())
    return g, hist


def _visual_distance(a, b):
    """两帧视觉距离：0.7*灰度 MAD + 0.3*色相直方图 L1 距离。"""
    import numpy as np
    if a is None or b is None:
        return 1.0
    g1, h1 = a
    g2, h2 = b
    mad = float(np.abs(g1 - g2).mean())
    l1 = float(np.abs(h1 - h2).sum()) / 2.0
    return 0.7 * mad + 0.3 * l1


def dedupe_frames_by_visual(manifest, base_dir, th=0.03, min_per_part=1, log=None):
    """画面张数**按内容自适应**：同一屏只留一张 + 每期保底 1 张，不设固定张数。

    1) 视觉去重：与已保留帧距离 < th 判为同屏 → 只保留墨迹量最高的那张
       （墨迹多 = 标注画全 = 信息量大）。
    2) 期级保底：某期若一张都没留下，补回该期墨迹量最高的一张（允许与别期同屏）。
    3) 重编号 V001..Vnnn 并删除落选文件，交付目录不留废图。
    返回 (新 manifest, 统计字典)
    """
    import shutil
    if not manifest:
        return manifest, {"before": 0, "after": 0, "same_screen_merged": 0,
                          "per_part_added": 0}

    items = sorted(manifest, key=lambda m: m.get("time_sec", 0))
    sigs = {}
    missing = []
    for m in items:
        p = os.path.join(base_dir, m["rel_path"])
        if not os.path.exists(p):
            missing.append(m["rel_path"])
        sigs[m["key"]] = _frame_signature(p)
    if missing:
        # 不静默：文件缺失会让课件引用坏路径，必须显式报出
        raise RuntimeError(
            "抽帧文件缺失，无法生成课件（检查 base_dir/rel_path 是否匹配）："
            + ", ".join(missing[:8])
            + (f" ...共 {len(missing)} 个" if len(missing) > 8 else ""))

    kept = []          # [(key, sig)]
    dropped = []
    merged = 0
    for m in items:
        s = sigs.get(m["key"])
        if s is None:
            kept.append((m["key"], s))
            continue
        best_i, best_d = -1, None
        for i, (k, ks) in enumerate(kept):
            d = _visual_distance(s, ks)
            if best_d is None or d < best_d:
                best_i, best_d = i, d
        if best_d is not None and best_d < th:
            # 同屏：留墨迹量更高的那张
            merged += 1
            cur_ink = m.get("ink", 0)
            old = next(x for x in items if x["key"] == kept[best_i][0])
            if cur_ink > old.get("ink", 0):
                kept[best_i] = (m["key"], s)
                dropped.append(old)
            else:
                dropped.append(m)
        else:
            kept.append((m["key"], s))

    kept_keys = [k for k, _ in kept]
    by_key = {m["key"]: m for m in items}

    # 期级保底
    part_of = {m["key"]: m.get("part_idx") for m in items}
    added = 0
    for pidx in sorted({p for p in part_of.values() if p is not None}):
        if any(part_of.get(k) == pidx for k in kept_keys):
            continue
        cands = [m for m in items if m.get("part_idx") == pidx
                 and m["key"] not in kept_keys]
        if not cands:
            continue
        best = max(cands, key=lambda m: m.get("ink", 0))
        kept.append((best["key"], sigs.get(best["key"])))
        kept_keys.append(best["key"])
        added += 1
        dropped = [d for d in dropped if d["key"] != best["key"]]

    # 落选的先删掉，避免与新编号撞名
    kept.sort(key=lambda ks: by_key[ks[0]].get("time_sec", 0))
    for d in dropped:
        p = os.path.join(base_dir, d["rel_path"])
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            pass

    # 重编号 V001..Vnnn —— 两段式改名，避免 V002→V001 覆盖掉还没搬走的 V001.jpg
    tmp_paths = {}
    for key, _sig in kept:
        old = os.path.join(base_dir, by_key[key]["rel_path"])
        tmpn = os.path.join(base_dir, "frames_clean", f"_tmp_{key}.jpg")
        try:
            if os.path.exists(old):
                os.replace(old, tmpn)
                tmp_paths[key] = tmpn
            else:
                tmp_paths[key] = old
        except OSError:
            tmp_paths[key] = old
    new_manifest = []
    for i, (key, _sig) in enumerate(kept, 1):
        m = dict(by_key[key])
        new_rel = f"frames_clean/V{i:03d}.jpg"
        dst = os.path.join(base_dir, new_rel)
        try:
            os.replace(tmp_paths[key], dst)
        except OSError:
            try:
                shutil.copy2(tmp_paths[key], dst)
            except Exception:
                pass
        m["rel_path"] = new_rel
        m["key"] = f"V{i:03d}"
        new_manifest.append(m)

    stats = {"before": len(items), "after": len(new_manifest),
             "same_screen_merged": merged, "per_part_added": added}
    if log:
        log(f"  自适应去重：{stats['before']} → {stats['after']} 张"
            f"（合并同屏 {merged}，期级保底补 {added}）")
    return new_manifest, stats


def extract_segment_frames(cfg, video, outline, task_dir, log=None):
    """按大纲每段时间窗抽若干候选帧，墨迹量评分挑最佳，存 frames_clean/Vxxx.jpg。
    返回 frame_manifest: [{key, rel_path, time_sec, note}]。

    坑（已踩）：候选帧临时目录**必须放 work/ 暂存区**，不能放 outputs/ 交付目录——
    交付目录里单次 rmtree >50 个文件会被 safe-delete 钩子拦截（后台进程拿不到确认 → 永久卡死）。
    这里进一步只用 2 个固定临时文件（cand/best），从根上不产生批量删除。
    """
    import shutil
    FFMPEG = cfg["ffmpeg"]
    clean_dir = os.path.join(task_dir, "frames_clean")
    os.makedirs(clean_dir, exist_ok=True)
    manifest = []
    idx = 0
    scratch = cfg.get("work_dir") or task_dir
    tmp = os.path.join(scratch, f"_frm_tmp_{os.getpid()}")
    os.makedirs(tmp, exist_ok=True)
    cand_path = os.path.join(tmp, "cand.jpg")
    best_path = os.path.join(tmp, "best.jpg")
    for pi, part in enumerate(outline.get("parts", []), 1):
        for si, seg in enumerate(part.get("segments", []), 1):
            a = float(seg.get("start_sec", 0))
            b = float(seg.get("end_sec", a + 5))
            if b <= a:
                b = a + 5
            fat = float(seg.get("frame_at_sec", (a + b) / 2))
            # 在 frame_at 附近均匀取 4 个候选
            cands = [max(0.0, fat - 4), max(0.0, fat - 1.5),
                     min(b, fat + 1.5), min(b, fat + 4)]
            cands = sorted(set(round(c, 1) for c in cands))
            best_s = -1
            for t in cands:
                try:
                    subprocess.run([FFMPEG, "-y", "-hide_banner", "-v", "error",
                                    "-ss", f"{t}", "-i", video, "-frames:v", "1",
                                    "-q:v", "3", cand_path],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   timeout=30, check=True)
                except Exception:
                    continue
                if not os.path.exists(cand_path):
                    continue
                sc = _ink_score(cand_path)
                if sc > best_s:
                    best_s = sc
                    shutil.copy2(cand_path, best_path)
            if best_s < 0:
                continue
            idx += 1
            dst = os.path.join(clean_dir, f"V{idx:03d}.jpg")
            # 清理画面（抹白板工具栏/调色板/尺寸标尺）—— 不清理会带录屏 UI 进课件
            ops.clean_course_frame(best_path, dst)
            manifest.append({
                "key": f"V{idx:03d}",
                "rel_path": f"frames_clean/V{idx:03d}.jpg",
                "time_sec": round(fat, 1),
                "note": seg.get("summary", "")[:40],
                "part": part.get("title", ""),
                "part_idx": pi,
                "seg": seg.get("title", ""),
                "ink": best_s,
            })
            if log:
                log(f"  段帧 [{part.get('title','')}/{seg.get('title','')}] {dst} ink={best_s}")
    # 收尾：只删 2 个固定临时文件（逐个删，避开批量删除钩子）
    for p in (cand_path, best_path):
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            pass
    # 删空的临时目录。注意：本环境 os.rmdir 对空目录也报 EISDIR（被沙箱拦截），
    # 只能用 rmtree；且**必须先确认目录已空**，保证永远不会发生批量删除。
    try:
        if os.path.isdir(tmp) and not os.listdir(tmp):
            shutil.rmtree(tmp, ignore_errors=True)
    except Exception:
        pass
    return manifest


def _fallback_frames(cfg, video, outline, task_dir, log=None):
    """段帧全失败时：按大纲期数在视频里均匀取若干张，保证课件有原画面。"""
    FFMPEG = cfg["ffmpeg"]
    clean_dir = os.path.join(task_dir, "frames_clean")
    os.makedirs(clean_dir, exist_ok=True)
    try:
        dur = ops.ffprobe_duration(cfg, video)
    except Exception:
        dur = 0.0
    if dur <= 0:
        return []
    n = max(3, len(outline.get("parts", [])))
    manifest = []
    for i in range(n):
        t = round(dur * (i + 0.5) / n, 1)
        out = os.path.join(clean_dir, f"V{i+1:03d}.jpg")
        try:
            subprocess.run([FFMPEG, "-y", "-hide_banner", "-v", "error",
                            "-ss", f"{t}", "-i", video, "-frames:v", "1",
                            "-q:v", "3", out],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=30, check=True)
        except Exception:
            continue
        if os.path.exists(out):
            manifest.append({"key": f"V{i+1:03d}",
                             "rel_path": f"frames_clean/V{i+1:03d}.jpg",
                             "time_sec": t,
                             "note": f"原视频 {int(t//60):02d}:{int(t%60):02d} 处画面",
                             "part": "", "part_idx": i + 1, "seg": "",
                             "ink": _ink_score(out)})
            log and log(f"  兜底帧 {out} t={t}")
    return manifest


# ============================================================== 3. 展开式逐字稿
_SCRIPT_SYS = """你是一位硬核交易/金融教学视频的「讲课稿写手」，擅长产出忠实结构化、展开式的讲课逐字稿。
基于「原始转写」+「分段大纲」+「可用画面素材」，产出一篇完整的讲课逐字稿（markdown）。

铁律：
1. 忠实于原视频：核心知识点不得删减，不得编造原视频没有的事实性数据/人物/案例。
   允许在原视频基础上合理延展讲解（补「为什么」、加贴合原意的类比与行动指令、加检验提问）。
2. 篇幅要展开：目标口播字数 ≥ 原转写字数的 1.2 倍（这是教学稿，不是压缩稿）。语速约 240 字/分。
3. 表演脚本格式（直接写进正文）：
   - 【】内是动作/语气提示，不念（如【停顿】、【语气加重】、【指图】、【放慢】）。
   - 用 —— 表示停顿 1 秒。
   - 每段口播结束标注本段时长（如「（约 1.8 分钟）」）与语速。
4. 严格按大纲的「期 / 段」组织：每期用 # 标题，每段用 ## 标题。
5. 讲到对应画面时，在正文合适位置插入 ![alt](frames_clean/xxx.jpg) 或 ![alt](diagrams/xxx.jpg)，
   不要堆在末尾，不要编造不存在的文件名。
6. 通篇要有：开场钩子（先给反常识结论）、每个核心概念配一个**贯穿全段**的类比、
   至少 3 处「检验提问」（停 1 秒让用户品）。
   ★ 结尾**必须**有一节，标题固定为 `## 【课后可执行动作清单】`，**至少 3 条**，
     每条写成「对象 + 动作 + 完成判定」，例如：
       - 【动作】打开 TradingView，把今天的例2 双顶标出来，看高点有没有被跌破
       - 【动作】用今天的案例，在自己最近的持仓上标出「高点上移/低点下移」
     结尾**不许**只写「谢谢观看」就结束。
7. ★ 类比必须**用明确的类比标记词**引出（就像 / 好比 / 相当于 / 打个比方 / 你可以理解为 /
   你可以这样理解 / 就跟我…一样），否则自检工具识别不到。每个核心概念至少 1 个贯穿类比。
8. ★ 出现专业术语（趋势线、极点、深度回调、交易区间、假突破、高点下移、前高、前低、
   通道、极值…）时，必须**当场用一句话解释**（写清「就是…」「意思是…」「指的是…」
   「你可以说它就是…」），不要只喊名词。

只输出 markdown 正文，不要任何额外说明。"""


def _style_block(style_name, intensity=3):
    """把 styles.PRESETS 里的讲法风格拼成 prompt 片段（指定风格入口）。

    标杆默认 = "faithful"（忠实结构化展开）；也可选 zhangxuefeng（张雪峰式）等。
    """
    from . import styles
    try:
        p = styles.get_preset(style_name)
    except KeyError:
        p = styles.get_preset("faithful")
    lines = "\n".join(f"- {x}" for x in p.get("style_lines", []))
    inten = p.get("intensity", {}).get(intensity, "")
    return (f"【讲法风格】{p['name']}（{p['desc']}）\n{lines}\n"
            f"- 当前强度 {intensity}：{inten}")


def looks_truncated(text):
    """判断 LLM 输出是否被 max_tokens 截断（讲稿最常见的隐性失败）。

    实测：成稿用 8000 token 时，稿子在"——好，"处断掉，尾部收尾段/课后作业/动作清单
    全部没生成，而流水线照样出稿、不报错 —— 必须能识别。
    """
    t = (text or "").rstrip()
    if not t:
        return True
    lines = [l for l in t.split("\n") if l.strip()]
    if not lines:
        return True
    last = lines[-1].strip()
    # 末行若不以成句标点/结构符结尾 → 疑似断在半句
    if not re.search(r"[。！？!?】\)）\」』…—\*\|：:]$", last):
        return True
    if t.count("```") % 2 == 1:        # 代码围栏没闭合
        return True
    return False


def generate_script(cfg, transcript, outline, frame_manifest, diagram_manifest, log=None,
                    style_name="faithful", intensity=3, source_kind="video",
                    extra_notes="", max_tokens=None, retry_tokens=None):
    # 画面素材清单
    fl = []
    for m in frame_manifest:
        t = m["time_sec"]
        fl.append(f"- {m['rel_path']}（{int(t//60):02d}:{int(t%60):02d}）{m.get('note','')}")
    for d in diagram_manifest or []:
        fl.append(f"- {d['rel_path']}（自制图）{d.get('desc','')}")
    if fl:
        frame_block = ("\n【可用画面素材】（按时间顺序，讲到对应内容时直接插入对应图片，"
                       "路径用上面的相对路径，不要堆在末尾，不要编造文件名）：\n" + "\n".join(fl))
    elif source_kind == "document":
        frame_block = ("\n【重要】本篇来源是**文档**，没有原视频画面可插；"
                       "所有配图都来自自制概念图（见下方清单），正文里用 ![alt](diagrams/xxx.jpg) 引用。")
    else:
        frame_block = ""

    notes_block = f"\n【额外要求】\n{extra_notes}\n" if extra_notes else ""
    outline_txt = json.dumps(outline, ensure_ascii=False)
    user = (f"【分段大纲】\n{outline_txt}\n\n"
            f"【原始素材】\n{transcript}\n\n"
            f"{_style_block(style_name, intensity)}\n"
            f"{notes_block}\n{frame_block}\n\n请产出完整讲课逐字稿 markdown。")
    mt = int(max_tokens or cfg.get("script_max_tokens", 20000))
    raw = ops.call_llm(cfg, _SCRIPT_SYS, user, log=log, max_tokens=mt)
    if not raw:
        raise RuntimeError("逐字稿 LLM 调用失败（双通道均不可用）")
    md = ops.extract_markdown(raw)
    if looks_truncated(md):
        rt = int(retry_tokens or cfg.get("script_max_tokens_retry", 32000))
        if log:
            log(f"  成稿疑似被截断（末行：{md.rstrip().split(chr(10))[-1][-30:]!r}），"
                f"用 {rt} token 重试一次")
        raw2 = ops.call_llm(cfg, _SCRIPT_SYS, user, log=log, max_tokens=rt)
        if raw2:
            md2 = ops.extract_markdown(raw2)
            if not looks_truncated(md2) or len(md2) > len(md):
                md = md2
    return md


# ============================================================== 4. 事实核查纠错
_FACT_SYS = """你是严谨的内容核查员。对比「原始视频转写」与「生成的讲课稿」，
找出原视频或讲稿中存在的错误 / 不严谨处，输出严格 JSON 列表（最多 5 条最关键的）：
[{ "original": "原话或讲稿原句", "issue": "问题类型(自相矛盾/缺条件前缀/错别字/与公认规则冲突/事实错误)",
   "correction": "正确说法", "basis": "依据" }]
只输出 JSON。"""


def fact_check(cfg, transcript, script, log=None):
    user = f"【原始转写】\n{transcript}\n\n【生成的讲课稿】\n{script}\n\n请输出纠错 JSON 列表。"
    raw = ops.call_llm(cfg, _FACT_SYS, user, log=log)
    if not raw:
        return []
    data = _extract_json(raw)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "corrections" in data:
        return data["corrections"]
    return []


# ============================================================== 5. 自制概念图（原生渲染，自动化）
def _sc_cjk_font():
    """挑一个简体中文 FontProperties，避免 PingFang.ttc 默认取 face0(HK 繁体) 缺字。

    与 pdf_builder._extract_face 同口径：对 .ttc 显式抽 SC face，非 ttc 直接取 face0。
    """
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import font_manager as fm
    from fontTools.ttLib import TTCollection
    import tempfile

    cands = [
        ("/System/Library/Fonts/PingFang.ttc", "SC"),
        ("/System/Library/Fonts/Hiragino Sans GB.ttc", None),
        ("/System/Library/Fonts/STHeiti Light.ttc", "SC"),
        ("/System/Library/Fonts/STHeiti.ttc", "SC"),
    ]
    for path, want in cands:
        if not os.path.exists(path):
            continue
        try:
            if path.lower().endswith(".ttc") and want:
                col = TTCollection(path)
                picks = [(i, (f["name"].getDebugName(4) or ""))
                         for i, f in enumerate(col.fonts)
                         if want in (f["name"].getDebugName(4) or "")]
                if not picks:
                    continue
                reg = [(i, nm) for i, nm in picks if "Regular" in nm]
                target = reg[0][0] if reg else picks[0][0]
                tmp = os.path.join(tempfile.gettempdir(),
                                   f"cg_cjk_{os.path.basename(path)}_{target}.ttf")
                if not os.path.exists(tmp):
                    col.fonts[target].save(tmp)
                return fm.FontProperties(fname=tmp)
            return fm.FontProperties(fname=path)
        except Exception:
            continue
    return fm.FontProperties(family="DejaVu Sans")


def _parse_diagram_spec(desc):
    """把 LLM 给的制图描述解析成 (标题, [节点…])。

    坑（已踩）：LLM 常常把整张图写成**一行**长文本（如
    "五步流程图：从上到下五个节点「遇阻 → 二次测试 → 突破趋势线 → …」"）。
    旧实现按"首行=标题、其余行=节点"解析 → 标题变成一整条长带、节点全丢，
    渲染出来是一张废图。这里做多级兜底：
      1) 多行 → 首行标题，其余每行一个节点
      2) 单行 → 先切出标题，再按分隔符/编号切节点
    """
    import textwrap
    lines = [l.strip() for l in (desc or "").split("\n") if l.strip()]
    if not lines:
        return "概念图", ["（无内容）"]
    if len(lines) > 1:
        title = lines[0]
        nodes = lines[1:]
    else:
        s = lines[0]
        # 切标题：优先 "：" / "——" / " - " 之前
        title = s
        m = re.match(r"^(.{1,28}?)(?:：|:|——|--)\s*(.+)$", s)
        if m:
            title, rest = m.group(1).strip(), m.group(2).strip()
        else:
            rest = s
        # 切节点：先按显式箭头/破折号/分号切，再按编号切
        parts = re.split(r"\s*(?:→|——|--|；|;|｜)\s*", rest)
        if len(parts) < 2:
            parts = re.split(r"\s*(?=\d+[.、)）])\s*", rest)
        if len(parts) < 2:
            parts = re.split(r"[，,、]", rest)
        nodes = [p.strip(" 　「」《》()（）[]") for p in parts if p.strip(" 　「」《》()（）[]")]
        if not nodes:
            nodes = [rest[:60]]

    def cut(t, n):
        t = re.sub(r"\s+", " ", t).strip()
        return t if len(t) <= n else t[:n - 1] + "…"

    title = cut(title, 24)
    nodes = [cut(n, 42) for n in nodes][:9]
    if not nodes:
        nodes = ["（无内容）"]
    return title, nodes


def gen_diagram_native(desc, out_path):
    """用 matplotlib 把 LLM 给的中文描述渲染成一张干净的概念图（流程图/对比/时序通用）。
    返回 True/False。完全本地、无外部依赖，保证 Web 工具自动化闭环。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

    _fp = _sc_cjk_font()
    title, steps = _parse_diagram_spec(desc)

    n = len(steps)
    # 竖排流程：节点多时两列，避免过高
    two_col = n > 6
    rows = (n + 1) // 2 if two_col else n
    fig_w = 11.5 if two_col else 8.6
    fig_h = max(3.2, 1.05 * rows + 2.1)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    fig.subplots_adjust(left=0.03, right=0.97, top=0.86, bottom=0.05)
    ax.axis("off")
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 10)

    ax.text(5, 9.45, title, ha="center", va="center", fontsize=15,
            fontweight="bold", fontproperties=_fp,
            bbox=dict(boxstyle="round,pad=0.42", fc="#2F6FB0", ec="none"))
    top, bot = 8.1, 1.15
    ys = ([top - (top - bot) * i / max(1, rows - 1) for i in range(rows)]
          if rows > 1 else [(top + bot) / 2])
    placed = []
    for i, s in enumerate(steps):
        col = i // rows if two_col else 0
        row = i % rows if two_col else i
        xc = (2.5 if col == 0 else 7.5) if two_col else 5.0
        y = ys[row]
        ax.add_patch(FancyBboxPatch((xc - 2.25, y - 0.42), 4.5, 0.84,
                     boxstyle="round,pad=0.12", fc="#EAF1F8", ec="#2F6FB0", lw=1.3))
        ax.text(xc, y, f"{i+1}. {s}", ha="center", va="center", fontsize=10.5,
                fontproperties=_fp, wrap=True)
        placed.append((xc, y))
    # 箭头：同列向下串；两列时第二列从第一列底部接过去
    for i in range(len(placed) - 1):
        x1, y1 = placed[i]
        x2, y2 = placed[i + 1]
        if abs(x1 - x2) < 0.01:
            ax.add_patch(FancyArrowPatch((x1, y1 - 0.45), (x2, y2 + 0.45),
                         arrowstyle="-|>", mutation_scale=15, color="#2F6FB0", lw=1.7))
        else:
            ax.add_patch(FancyArrowPatch((x1 + 2.3, y1 - 0.2), (x2 - 2.3, y2 + 0.2),
                         arrowstyle="-|>", mutation_scale=15, color="#2F6FB0", lw=1.7,
                         connectionstyle="arc3,rad=-0.25"))
    fig.savefig(out_path, dpi=130)
    plt.close(fig)
    return os.path.exists(out_path)


def _default_image_gen(specs, out_dir, log=None):
    """默认自制图生成：原生 matplotlib 渲染（自动化、无外部依赖）。"""
    ddir = os.path.join(out_dir, "diagrams")
    os.makedirs(ddir, exist_ok=True)
    out = []
    for i, spec in enumerate(specs, 1):
        p = os.path.join(ddir, f"D{i:03d}.jpg")
        if gen_diagram_native(spec, p):
            out.append({"rel_path": f"diagrams/D{i:003d}.jpg",
                        "desc": spec.split("\n")[0][:30] if spec else f"概念图{i}"})
            if log:
                log(f"  自制图 D{i:03d} <- {spec[:40]}")
    return out


# ============================================================== 6. 组装 + PDF
def _self_check_for_appendix(cfg, md):
    try:
        return ops.self_check(cfg, md)
    except Exception:
        return {}


def _normalize_headings(script):
    """归一化 LLM 稿的标题层级，消除标题重复。

    坑（已踩）：LLM 不总遵守「期用 #、段用 ##」——有时写成 `## 第N期` + `### 段标题`，
    有时自己在正文开头又写一遍 `# 标题`。直接拼装会导致：
      1) 课件里标题出现两次
      2) 逐段对账引擎（按 ## 找段、# 找期）把期当成段 → 掌握度失真
    这里统一：剥掉正文开头的 H1；`# 第N期…` 保持一级；`##` 升为一级期或二级段；
    其余 `###+` 降为二级段。
    """
    lines = script.split("\n")
    # 找正文第一个标题：若是 H1 且**不是期标题** → 它是文档标题（assemble 已统一写）→ 丢弃
    first_i = None
    for i, ln in enumerate(lines):
        if re.match(r"^#{1,6}\s+", ln.strip()):
            first_i = i
            break
    drop_title = None
    if first_i is not None:
        m = re.match(r"^#\s+(.*)$", lines[first_i].strip())
        if m:
            t = m.group(1).strip()
            is_part = bool(re.match(r"^第\s*[0-9一二三四五六七八九十]+\s*期", t)
                           or t[:6].endswith("期"))
            if not is_part:
                drop_title = t
    out = []
    for i, ln in enumerate(lines):
        s = ln.strip()
        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if not m:
            out.append(ln)
            continue
        hashes, text = m.group(1), m.group(2).strip()
        if drop_title is not None and text == drop_title:
            continue                       # 文档标题（含重复的那次）统一由 assemble 写
        if len(hashes) == 1:
            out.append(f"# {text}")       # 其它 H1 都是期标题 → 保留
            continue
        if re.match(r"^第\s*[0-9一二三四五六七八九十]+\s*期", text) or "期" in text[:6]:
            out.append(f"# {text}")       # 期 → 一级
        else:
            out.append(f"## {text}")      # 段 → 二级
    return "\n".join(out)


def _norm_title(t):
    return re.sub(r"[^\w一-鿿]", "", t or "")


def _ensure_part_headings(script, outline):
    """按大纲补齐**缺失的「期」标题**。

    坑（已踩）：LLM 稿有时漏写某一期的 `# 第N期` 标题（实测只写了第2期），
    于是期数统计只剩 1 → 时长承诺（期数 × 单期）算错、逐段对账把该期的段挂到空期下。
    这里按大纲的「期→段」顺序做贪心对齐（标题包含优先，位置兜底），把缺的期标题插回去。
    """
    parts = outline.get("parts") or []
    flat = []
    for p in parts:
        pt = p.get("title", "")
        for s in p.get("segments", []):
            flat.append((pt, s.get("title", "")))
    if not flat:
        return script
    lines = script.split("\n")
    seg_pos = []
    for i, l in enumerate(lines):
        s = l.strip()
        if not s.startswith("## "):
            continue
        t = s[3:].strip()
        if t.startswith("附") or t.startswith("目录"):
            continue
        seg_pos.append(i)
    if not seg_pos:
        return script

    ptr = 0
    part_of = []
    for pos in seg_pos:
        st = _norm_title(re.sub(r"^##\s+", "", lines[pos].strip()))
        matched = None
        for oi in range(ptr, min(len(flat), ptr + 6)):
            ot = _norm_title(flat[oi][1])
            if not ot:
                continue
            if ot in st or st in ot or len(set(ot) & set(st)) >= max(3, len(ot) // 2):
                matched = oi
                break
        if matched is None:
            matched = min(ptr, len(flat) - 1)
        ptr = matched + 1
        part_of.append((pos, flat[matched][0]))

    inserts = {}
    emitted = set()
    # 脚本里**已有**的期标题（任何层级）→ 不要重复插入
    for l in lines:
        m = re.match(r"^#{1,6}\s+(.*)$", l.strip())
        if not m:
            continue
        t = m.group(1).strip()
        if re.match(r"^第\s*[0-9一二三四五六七八九十]+\s*期", t) or t[:6].endswith("期"):
            emitted.add(t)
    prev = None
    for pos, pt in part_of:
        if pt and pt != prev and pt not in emitted:
            inserts.setdefault(pos, []).append(pt)
            emitted.add(pt)
        prev = pt or prev
    if not inserts:
        return script
    out = []
    for i, l in enumerate(lines):
        if i in inserts:
            for pt in inserts[i]:
                out.append(f"# {pt}")
                out.append("")
        out.append(l)
    return "\n".join(out)


def assemble(cfg, outline, script, frame_manifest, diagram_manifest, corrections,
             meta, log=None):
    """组装单篇 markdown：正稿 + 附一画面清单 + 附二三处纠正 + 附三录制清单 + 附四回炉记录。"""
    script = _ensure_part_headings(script, outline)
    script = _normalize_headings(script)
    parts = []
    # 课件自身时长（按语速折算正文口播字）——不能只写源视频时长，
    # 否则"源视频12.3分钟 / 课件22.6分钟"会被误读成课件只有12分钟。
    import re as _re
    _body = []
    for _l in script.split("\n"):
        _s = _l.strip()
        if not _s or _s.startswith(("|", "#", "!", ">", "*", "-", "`", "---")):
            continue
        _body.append(_s)
    _cn = len(_re.findall(r"[一-鿿]", "\n".join(_body)))
    _own_min = _cn / max(60, cfg.get("script_speed_cpm", 240)) * 60 / 60
    parts.append(f"# {outline.get('title', meta.get('video','讲课稿'))}\n")
    parts.append("> 自动生成讲课课件 · 来源 B站 "
                 f"{meta.get('video','')}（源视频 {meta.get('duration_min','?')} 分钟）"
                 f" · **本课件口播约 {_own_min:.1f} 分钟**"
                 f" · 画面 {len(frame_manifest)+(len(diagram_manifest or []))} 张（"
                 f"{len(frame_manifest)} 张原讲义图 + {len(diagram_manifest or [])} 张自制图）\n")

    # 期次结构速览表
    parts.append("\n## 目录（期 / 段 / 关键画面）\n")
    parts.append("| 期次 | 主题 | 段 | 关键画面 |")
    parts.append("| --- | --- | --- | --- |")
    for pi, part in enumerate(outline.get("parts", []), 1):
        segs = part.get("segments", [])
        seg_titles = "；".join(s.get("title", "") for s in segs)
        parts.append(f"| 第{pi}期 | {part.get('title','')} | {seg_titles} | "
                     f"{len(segs)} 段 |")
    parts.append("")

    parts.append("\n---\n")
    parts.append(script)

    # 附一 画面清单
    parts.append("\n---\n\n## 附一 · 画面清单\n")
    parts.append("| 文件 | 时刻 | 用途 |")
    parts.append("| --- | --- | --- |")
    for m in frame_manifest:
        t = m["time_sec"]
        parts.append(f"| {m['rel_path']} | {int(t//60):02d}:{int(t%60):02d} | {m.get('note','')} |")
    for d in diagram_manifest or []:
        parts.append(f"| {d['rel_path']} | 自制 | {d.get('desc','')} |")

    # 附二 三处纠正
    parts.append("\n---\n\n## 附二 · 对原视频的核查纠正\n")
    if corrections:
        for i, c in enumerate(corrections, 1):
            parts.append(f"**{i}. {c.get('issue','问题')}**")
            parts.append(f"- 原表述：{c.get('original','')}")
            parts.append(f"- 纠正：{c.get('correction','')}")
            parts.append(f"- 依据：{c.get('basis','')}")
            parts.append("")
    else:
        parts.append("（未检出明显错误）")

    # 附三 录制执行清单（通用模板，对应标杆附一）
    parts.append("\n---\n\n## 附三 · 录制执行清单（给自己看，不念）\n")
    parts.append("| # | 检查 | 勾 |")
    parts.append("| --- | --- | --- |")
    for i, item in enumerate([
        "盲讲 3 分钟做过了吗（关稿子、只看标题+关键词）",
        "盲讲卡住的点列出来了吗",
        "画面满屏：编辑器侧栏已关，图片单独全屏预览",
        "每张图最小字号 ≥ 18pt",
        "有没有即兴发挥的高风险句（必然/永远/充分必要）",
        "出现的数字条件前缀写全了吗",
    ], 1):
        parts.append(f"| {i} | {item} | □ |")

    # 附四 回炉记录（来自自检）
    sc = _self_check_for_appendix(cfg, script)
    parts.append("\n---\n\n## 附四 · 成稿自检\n")
    if sc:
        for k, v in sc.items():
            parts.append(f"- {k}: {v}")
    else:
        parts.append("- （自检不可用）")

    return "\n".join(parts)


def quality_gate(cfg, script, truncated=False):
    """质量闸门：返回 (自检字典, 不合格项列表[])。不合格必须显式报出。

    硬指标（红线）：
      - 成稿完整（没被 max_tokens 截断）
      - 行动指令 ≥ 3
      - 检验提问 ≥ 3
      - 正文 ≥ 1500 字
      - **未当场解释的术语 = 0**（费曼法核心：讲不清就是没懂）
    """
    try:
        checks = ops.self_check(cfg, script)
    except Exception:
        checks = {}
    gate = []
    if truncated:
        gate.append("成稿疑似被截断（末尾句子不完整，尾部收尾段可能缺失）")
    if checks.get("action_instructions", 0) < 3:
        gate.append(f"行动指令仅 {checks.get('action_instructions', 0)} 处（要求 ≥3）")
    if checks.get("check_questions", 0) < 3:
        gate.append(f"检验提问仅 {checks.get('check_questions', 0)} 处（要求 ≥3）")
    if checks.get("body_cn_chars", 0) < 1500:
        gate.append(f"正文仅 {checks.get('body_cn_chars', 0)} 字（过短，疑似生成不全）")
    n_unexp = checks.get("unexplained_terms", 0)
    if n_unexp:
        terms = "、".join((checks.get("unexplained_detail") or [])[:5])
        gate.append(f"{n_unexp} 个术语未当场解释（要求 0）：{terms}")
    return checks, gate


_REPAIR_SYS = """你是讲课稿编辑。基于给定讲稿正文，**只补写缺失的那一节**，其余内容一个字都不要改。

补写「课后可执行动作清单」时：
- 标题写成 `## 【课后可执行动作清单】`
- 3~5 条，**每条写成正文里能念出口的一句话**（例："现在请你打开 TradingView，
  把今天的例2 双顶标出来，看高点有没有被跌破。"）
- ★ **不要用 "- " 开头的列表项** —— 列表项是舞台提示、不算口播内容，
  自检工具统计不到，等于白写
- 动作必须真的能执行（打开某个软件/画图/标注/复盘/数一下/对照标准），
  不许写「好好复习」这种空话
- 必须紧扣这一节讲的内容，用讲稿里出现过的具体案例/图表/术语

只输出这一节的 markdown，不要任何解释。"""


def repair_missing(cfg, script, checks, log=None):
    """质量闸门没过时的**外科式修补**：只补缺的那一节，不重写全稿。

    返回 (新脚本, 修补说明列表)。修补后仍不合格的项由上层继续报警。
    """
    need = []
    if checks.get("action_instructions", 0) < 3:
        need.append("课后可执行动作清单")
    if checks.get("check_questions", 0) < 3:
        need.append("检验提问")
    if not need:
        return script, []
    body = script[:12000]
    user = (f"需要补写的小节：{'、'.join(need)}\n\n"
            f"【讲稿正文】\n{body}\n\n"
            f"请只输出需要补写的那一节 markdown。")
    raw = ops.call_llm(cfg, _REPAIR_SYS, user, log=log)
    if not raw:
        return script, []
    patch = ops.extract_markdown(raw).strip()
    if not patch:
        return script, []
    # 插到附录之前；没有附录就追加到末尾
    idx = script.find("\n## 附")
    if idx == -1:
        new_script = script.rstrip() + "\n\n" + patch + "\n"
    else:
        new_script = script[:idx].rstrip() + "\n\n" + patch + "\n\n" + script[idx:].lstrip("\n")
    if log:
        log(f"  修补：补写「{'、'.join(need)}」{len(patch)} 字")
    return new_script, need


_TERM_FIX_SYS = """你是讲课稿编辑。给你若干个专业术语和它们在讲稿里首次出现的上下文，
请为**每一个**术语写一句**当场就能念出来的大白话解释**。

要求：
- 每个术语一句话，不超过 40 字
- 必须用「就是…」「意思是…」「指的是…」「你可以理解为…」这种说法开头
  （自检工具靠这些标记判断"当场解释过"）
- 用外行听得懂的话，不要用另一个术语解释术语
- 严格按给定的术语顺序输出，不要增删术语

只输出 JSON，格式：{"terms": ["术语1", "术语2"], "explains": ["解释1", "解释2"]}"""


def repair_unexplained_terms(cfg, script, checks, log=None):
    """术语未当场解释 → 在**首次出现处**插入一句通俗解释（外科式，不重写全稿）。

    返回 (新脚本, 修补说明)。插入位置：该术语所在句子的句末。
    """
    terms = checks.get("unexplained_detail") or []
    if not terms:
        return script, []
    # 定位：必须用**正文口径**（body_of 跳过表格），否则会命中目录表里的同名词 →
    # 解释插进目录，检查时正文里那个位置仍判未解释（插了等于没插）。
    _q = ops.load_quality_module()
    _body, _kept = _q.body_with_map(script)
    items = []
    for t in terms:
        off = _q.term_first_occurrence_in_body(script, t)
        if off is None:
            continue
        s = script.rfind("\n", 0, off) + 1
        e = script.find("\n", off)
        e = e if e > 0 else len(script)
        items.append({"term": t, "ctx": script[s:e][:160], "off": off})
    if not items:
        return script, []
    user = ("请为下列术语各写一句大白话解释：\n"
            + "\n".join(f"{k+1}. {it['term']}（上下文：{it['ctx']}）"
                        for k, it in enumerate(items)))
    raw = ops.call_llm(cfg, _TERM_FIX_SYS, user, log=log)
    if not raw:
        return script, []
    try:
        import json as _json
        data = _json.loads(ops.extract_markdown(raw).strip()
                           .replace("```json", "").replace("```", "").strip())
        explains = data.get("explains") or []
    except Exception as e:
        if log:
            log(f"  术语修补解析失败（{e}），跳过")
        return script, []
    if len(explains) != len(items):
        if log:
            log(f"  术语修补数量不匹配（{len(explains)}/{len(items)}），跳过")
        return script, []
    # 插到每个术语首次出现所在句子的句末（从后往前插，避免位移）
    out = script
    for it, exp in sorted(zip(items, explains),
                           key=lambda x: x[0].get("off", 0), reverse=True):
        i = out.find(it["term"], it.get("off", 0))
        if i < 0:
            continue
        # 插入点：优先**句末标点**（在同一行内）；没有标点就插到该行行末。
        # ★ 不能按换行插到下一行 —— 那会把解释落到下一段/标题后面，
        #   而闸门按「术语所在段」判定作用域 → 解释在别的段 = 判未解释（实测漏修）。
        line_end = out.find("\n", i)
        line_end = line_end if line_end > 0 else len(out)
        m = re.search(r"[。！？]", out[i:line_end])
        if m:
            pos = i + m.end()
        else:
            pos = line_end
        ins = f"（{exp.strip()}）"
        out = out[:pos] + ins + out[pos:]
    if log:
        log(f"  修补：{len(items)} 个术语补上当场解释")
    return out, [it["term"] for it in items]


def build_courseware(cfg, video, transcript, duration_sec, task_dir, out_dir,
                     log=None, image_gen=None, stage_cb=None,
                     style_name="faithful", intensity=3, extra_notes="",
                     source_kind="video"):
    """端到端：大纲→抽帧→成稿→纠错→组装→PDF。

    image_gen(specs) -> [(desc, rel_path)] ：agent 侧通过 ImageGen 生成自制图后回填。
    不传则走内置原生 matplotlib 渲染（自动化、无外部依赖）。
    stage_cb(stage, label, frac) ：可选，用于把内部阶段进度上报给上层（如 Web 任务引擎）。
    style_name/intensity ：讲法风格（styles.PRESETS），默认 faithful=标杆·忠实结构化。
    返回 dict 含 script_path / pdf_path / frame_manifest / diagram_manifest / corrections / outline / checks。
    """
    def _st(s, l, f=0.0):
        if stage_cb:
            try:
                stage_cb(s, l, f)
            except Exception:
                pass

    _st("segment_outline", "自动分段大纲", 0.0)
    outline = segment_outline(cfg, transcript, duration_sec, log=log)
    if log:
        log(f"  大纲：{len(outline.get('parts', []))} 期 / "
            f"{sum(len(p.get('segments', [])) for p in outline.get('parts', []))} 段")
    _st("segment_outline", "自动分段大纲", 1.0)

    _st("extract_frames", "抽关键画面", 0.0)
    frame_manifest = extract_segment_frames(cfg, video, outline, task_dir, log=log)
    if not frame_manifest:
        # 兜底：按大纲期数均匀抽几张，保证课件一定有原视频画面
        frame_manifest = _fallback_frames(cfg, video, outline, task_dir, log=log)
    # 画面张数按内容自适应：同一屏只留一张 + 每期保底 1 张（不设固定张数）
    frame_manifest, frame_stats = dedupe_frames_by_visual(
        frame_manifest, task_dir,
        th=float(cfg.get("frame_dedupe_th", 0.03)),
        min_per_part=int(cfg.get("frame_min_per_part", 1)),
        log=log)
    if log:
        log(f"  抽帧：{len(frame_manifest)} 张原讲义图（自适应去重后）")
    _st("extract_frames", "抽关键画面", 1.0)

    # 自制图：由 LLM 按内容判断该做几张（代码里的 max_diagrams 只是安全护栏，不是目标值）
    diagram_specs = []
    for pi, part in enumerate(outline.get("parts", []), 1):
        for si, seg in enumerate(part.get("segments", []), 1):
            if seg.get("needs_diagram") and seg.get("diagram_desc"):
                diagram_specs.append(seg["diagram_desc"])
    max_diagrams = int(cfg.get("max_diagrams", 8))
    if len(diagram_specs) > max_diagrams:
        log and log(f"  自制图 {len(diagram_specs)}→ 安全上限 {max_diagrams} 张（LLM 过度标记）")
        diagram_specs = diagram_specs[:max_diagrams]
    diagram_manifest = []
    if diagram_specs:
        _st("gen_diagram", "生成自制图", 0.0)
        gen = image_gen or _default_image_gen
        diagram_manifest = gen(diagram_specs, out_dir, log=log) or []
        _st("gen_diagram", "生成自制图", 1.0)

    _st("build_script", "生成讲课稿", 0.0)
    script = generate_script(cfg, transcript, outline, frame_manifest, diagram_manifest,
                              log=log, style_name=style_name, intensity=intensity,
                              source_kind=source_kind, extra_notes=extra_notes)
    truncated = looks_truncated(script)
    if truncated:
        log("  ⚠ 成稿末尾不完整，疑似被 max_tokens 截断（收尾段/动作清单可能缺失）")
    _st("build_script", "生成讲课稿", 1.0)

    _st("fact_check", "核查纠错", 0.0)
    corrections = fact_check(cfg, transcript, script, log=log)
    if log:
        log(f"  纠错：{len(corrections)} 条")
    _st("fact_check", "核查纠错", 1.0)

    meta = {"video": os.path.basename(video),
            "duration_min": f"{duration_sec/60:.1f}"}

    # ── 自检 + 质量闸门（必须在写盘/出PDF**之前**，否则横幅进不了文件）
    _st("self_check", "稿子自检", 0.0)
    checks, gate = quality_gate(cfg, script, truncated)
    repaired = []
    if gate:
        # 外科式修补 A：术语未当场解释 → 在首次出现处插一句大白话
        if checks.get("unexplained_terms", 0):
            s3, r3 = repair_unexplained_terms(cfg, script, checks, log=log)
            if s3 != script:
                c3, g3 = quality_gate(cfg, s3, truncated)
                script, checks, gate = s3, c3, g3
                repaired += [f"术语解释×{len(r3)}"]
        # 外科式修补 B：只补缺的那一节（行动指令/检验提问），不重写全稿
        if gate:
            script2, repaired2 = repair_missing(cfg, script, checks, log=log)
            if script2 != script:
                checks2, gate2 = quality_gate(cfg, script2, truncated)
                if len(gate2) < len(gate):
                    script, checks, gate = script2, checks2, gate2
                repaired += repaired2
    _st("self_check", "稿子自检", 1.0)

    _st("assemble", "组装与PDF", 0.0)
    md = assemble(cfg, outline, script, frame_manifest, diagram_manifest, corrections, meta, log=log)
    if gate:
        log("  ⚠ 质量闸门未通过：" + "；".join(gate))
        # ★ 横幅里不要用 ⚠ 符号：PingFang SC 没有该字形，PDF 里会渲染成方块
        banner = ("> **【质量提醒】本稿未通过质量闸门**：" + "；".join(gate)
                  + "\n> 请先补齐上述项再录制（详见文末「附四 · 成稿自检」）。\n")
        lines = md.split("\n")
        md = "\n".join(lines[:2] + [banner] + lines[2:])
    else:
        log("  ✓ 质量闸门通过（行动指令/检验提问/完整性均达标）")

    styled_out = os.path.join(out_dir, "讲课稿.md")
    os.makedirs(out_dir, exist_ok=True)
    with open(styled_out, "w", encoding="utf-8") as fh:
        fh.write(md)

    pdf_out = os.path.join(out_dir, "讲课稿.pdf")
    try:
        pdf_builder.markdown_to_pdf(styled_out, pdf_out)
        if log:
            log(f"  PDF 已生成 {pdf_out}")
    except Exception as e:
        if log:
            log(f"  PDF 生成失败（不影响 md）：{e}")
        pdf_out = None
    _st("assemble", "组装与PDF", 1.0)

    return {
        "outline": outline,
        "frame_manifest": frame_manifest,
        "diagram_manifest": diagram_manifest,
        "corrections": corrections,
        "script": script,
        "script_path": styled_out,
        "pdf_path": pdf_out,
        "script_chars": len(script),
        "total_images": len(frame_manifest) + len(diagram_manifest),
        "quality_gate": gate,
        "checks": checks,
    }
