#!/usr/bin/env python3
"""两条业务链路的执行实现。"""
import json
import os
import re
import shutil
import time
import hashlib

from . import config as cfgmod
from . import pipeline_ops as ops
from . import styles

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


CN = re.compile(r"[\u4e00-\u9fff]")


# ================================================================ 链路 A
def _download_hint(url, err):
    """把 yt-dlp 的原始报错翻译成可执行的下一步。

    ★ 实测踩过：当前运行环境的出口网络只通国内站（youtube / github / google
    全部超时），yt-dlp 报的是很难懂的 "Unable to connect to proxy ... 502"。
    这时要直接告诉用户「换个来源或在本机下载后指路径」，而不是甩原始报错。
    """
    s = str(err)
    low = s.lower()
    host = re.sub(r"^https?://([^/]+).*$", r"\1", url or "")
    overseas = any(h in host for h in ("youtube.com", "youtu.be", "vimeo.com",
                                       "twitter.com", "x.com", "tiktok.com",
                                       "instagram.com", "facebook.com"))
    net_hint = ("proxy" in low or "502" in s or "timed out" in low
                or "unable to connect" in low or "connection" in low)
    if net_hint and overseas:
        return (f"{s}\n\n"
                f"【网络出口限制】当前环境访问不了 {host}（境外站点在沙箱出口被拦）。\n"
                f"两个绕行办法：\n"
                f"  ① 在本机终端（沙箱外）用 yt-dlp 下载，再把文件路径填进同一个输入框\n"
                f"     yt-dlp -f 'bv*[height<=1080][vcodec^=avc1]+ba/b' -o video.mp4 '{url}'\n"
                f"  ② 或改用该内容在 B 站的版本（本环境能直连 B 站）")
    if net_hint:
        return (f"{s}\n\n"
                f"【网络出口限制】当前环境访问不了 {host or '该站点'}。\n"
                f"可以改用能直连的站点，或在本机下载后把文件路径填进输入框。")
    return s


def normalize_video_source(raw):
    """校验并规范化输入源，返回 (kind, value, error)。

    kind = "url"    → value 是规范化后的 http(s) 地址（yt-dlp 支持的站点都行）
    kind = "local" → value 是本地视频文件的绝对路径
    error 非空表示不合法。

    ★ 为什么不再只认 B 站：yt-dlp 本身支持 YouTube / Vimeo / Twitter 等几十个站点，
      把校验写死成 bilibili.com 等于把工具锁死在一个平台上。
      同时支持本地文件路径 —— 有些源在当前网络出口取不到（见 download_video 的
      网络受限提示），这时在本机下载好再指路径最省事。
    """
    if not raw or not raw.strip():
        return None, None, "请填写视频地址，或本地视频文件的路径"
    s = raw.strip()
    # 本地文件
    if s.startswith("file://"):
        s = s[7:]
    if s.startswith("/") or s.startswith("~"):
        p = os.path.abspath(os.path.expanduser(s))
        if not os.path.exists(p):
            return None, None, f"本地文件不存在：{p}"
        if not os.path.isfile(p):
            return None, None, f"不是文件（要视频文件，不要目录）：{p}"
        if os.path.getsize(p) < 1024:
            return None, None, f"文件太小，可能不是视频：{p}"
        return "local", p, None
    # 省略协议头的 B 站短链（BVxxxx）
    if re.match(r"^BV[0-9A-Za-z]{8,}$", s):
        s = "https://www.bilibili.com/video/" + s
    if not re.match(r"^https?://", s):
        s = "https://" + s
    # 主机名必须像域名（含点 + 顶级域），否则多半是乱输入
    if not re.match(r"^https?://[A-Za-z0-9][A-Za-z0-9.\-]*\.[A-Za-z]{2,}([:/?#]|$)", s):
        return None, None, ("地址格式不对（应形如 "
                            "https://www.youtube.com/watch?v=... 或 https://www.bilibili.com/video/BVxxxx）")
    return "url", s, None


def normalize_bilibili_url(raw):
    """兼容旧接口：只接受 B 站地址（已由 normalize_video_source 泛化）。"""
    kind, value, err = normalize_video_source(raw)
    if err:
        return None, err
    if kind != "url":
        return None, "请填写 B 站视频页地址"
    if not re.match(r"^https?://(www\.|m\.)?bilibili\.com/video/", value):
        return None, ("请填写合法的 B 站视频页地址"
                      "（形如 https://www.bilibili.com/video/BVxxxx，可省略 https:// 前缀）")
    return value, None


def run_video_to_script(cfg, tid, setp, log):
    """B 站 URL → 标杆级讲课课件（自动化、通用化，等价项目强流水线）。

    全程无硬编码段落 / 无预制图：LLM 自动分段大纲 → 按段精准抽原视频画面
    → 展开式结构化逐字稿（表演脚本格式）→ 事实核查纠错 → 自制概念图 → 组装 + PDF。
    画面/自制图/MD/PDF 全部落在 outputs/课件_{tid}/ 同目录，相对路径可整体搬走。
    """
    from . import jobs
    from . import courseware_gen as cg

    params = jobs.get_task(cfg, tid)["params"]
    task_dir = jobs.get_task(cfg, tid)["dir"]
    out_dir = os.path.join(cfg["outputs_dir"], f"课件_{tid}")
    os.makedirs(out_dir, exist_ok=True)

    kind, url, err = normalize_video_source(params.get("url", ""))
    if err:
        raise ValueError(err)

    # ── 1 下载（视频流 avc1 + 音轨 分别下，再合并；本机 ffmpeg 无 HEVC 解码）
    jobs.stage_progress(cfg, tid, "download", "下载视频", 0.0)
    dl_dir = os.path.join(task_dir, "video")
    os.makedirs(dl_dir, exist_ok=True)
    t0 = time.time()
    if kind == "local":
        # 本地视频文件：直接用，不下载也不复制（复制会占双份空间，
        # 且清理逻辑会把它当成中间产物删掉 —— 原文件必须在 work/ 之外）
        video = url
        log(f"  使用本地视频 {video} "
            f"{os.path.getsize(video)/1e6:.1f}MB（不下载）")
    else:
        try:
            ops.download_video(cfg, url, os.path.join(dl_dir, "v.%(ext)s"),
                              "bv*[height<=1080][vcodec^=avc1]", log=log, timeout=1800,
                              progress_cb=lambda f: jobs.stage_progress(
                                  cfg, tid, "download", "下载视频", f))
        except Exception as e:
            raise RuntimeError(f"视频下载失败（yt-dlp）：{_download_hint(url, e)}")
        vids = [os.path.join(dl_dir, f) for f in os.listdir(dl_dir)
                if f.startswith("v.") and f.endswith((".mp4", ".mkv", ".webm", ".flv"))]
        if not vids:
            raise RuntimeError("视频下载完成但未找到视频流文件")
        vstream = max(vids, key=os.path.getsize)

        # 音轨单独下（与视频流合并，确保有声音供 ASR）
        try:
            ops.download_video(cfg, url, os.path.join(dl_dir, "a.%(ext)s"),
                              "ba/b", log=log, timeout=900)
        except Exception as e:
            log(f"  音轨单独下载失败（{e}），尝试用视频内嵌音轨")
        auds = [os.path.join(dl_dir, f) for f in os.listdir(dl_dir)
                if f.startswith("a.") and f.endswith((".m4a", ".mp3", ".aac", ".m4s"))]
        video = vstream
        if auds:
            audio = max(auds, key=os.path.getsize)
            merged = os.path.join(dl_dir, "merged.mp4")
            try:
                ops.run_cmd(cfg, [cfg["ffmpeg"], "-y", "-hide_banner", "-v", "error",
                                  "-i", vstream, "-i", audio,
                                  "-c:v", "copy", "-c:a", "aac", "-shortest", merged],
                            log=log)
                if os.path.exists(merged) and os.path.getsize(merged) > 0:
                    video = merged
                    log("  音视频已合并")
            except Exception as e:
                log(f"  合并失败（{e}），使用视频内嵌音轨")
        log(f"  视频就绪 {os.path.basename(video)} "
            f"{os.path.getsize(video)/1e6:.1f}MB {time.time()-t0:.1f}s")
    setp(progress_note=f"视频 {os.path.getsize(video)/1e6:.0f}MB")

    dur = ops.ffprobe_duration(cfg, video)
    if dur > cfg["max_video_seconds"]:
        raise RuntimeError(f"视频 {dur/60:.1f} 分钟超过上限 "
                           f"{cfg['max_video_seconds']/60:.0f} 分钟")
    log(f"  时长 {dur:.1f}s")

    # ── 2 音轨 + ASR
    jobs.stage_progress(cfg, tid, "extract_audio", "抽取音轨", 0.1)
    wav = os.path.join(task_dir, "audio.wav")
    ops.extract_audio(cfg, video, wav, log=log)
    jobs.stage_progress(cfg, tid, "asr", "语音转写（本地离线）", 0.1)
    asr_txt = os.path.join(task_dir, "asr.txt")
    ops.run_asr(cfg, wav, asr_txt, log=log, workdir=task_dir,
                progress_cb=lambda f: jobs.stage_progress(
                    cfg, tid, "asr", "语音转写（本地离线）", f))
    segs = ops.parse_asr_file(asr_txt)
    log(f"  ASR {len(segs)} 段")
    setp(asr_segments=len(segs))
    if not segs:
        raise RuntimeError("ASR 未产出任何内容")
    transcript = ops.asr_to_text(segs)

    # ── 3 强流水线：大纲→抽帧→成稿→纠错→组装→PDF
    # task_dir 与 out_dir 同传 out_dir，保证 frames_clean/diagrams/MD/PDF 同目录、相对路径可搬走。
    def _stage(s, l, f=0.0):
        try:
            jobs.stage_progress(cfg, tid, s, l, f)
        except Exception:
            pass

    res = cg.build_courseware(
        cfg, video, transcript, dur, out_dir, out_dir,
        log=log, stage_cb=lambda s, l, f: _stage(s, l, f))

    # 组装产物清单（前端要 file/time_sec/key 用于缩略图）
    assets = []
    for m in res["frame_manifest"]:
        ap = os.path.join(out_dir, m["rel_path"])
        assets.append({"key": m["key"], "file": ap, "note": m.get("note", ""),
                       "time_sec": m["time_sec"], "rel_path": m["rel_path"]})
    for d in res["diagram_manifest"]:
        ap = os.path.join(out_dir, d["rel_path"])
        assets.append({"key": os.path.basename(d["rel_path"]), "file": ap,
                       "note": d.get("desc", ""), "time_sec": 0,
                       "rel_path": d["rel_path"]})

    try:
        with open(res["script_path"], encoding="utf-8") as fh:
            preview = fh.read()[:4000]
    except Exception:
        preview = (res.get("script") or "")[:4000]

    return {
        "used_llm": True,
        "llm_note": ("已用小米 LLM 生成标杆级讲课课件：结构化分期 · 展开不压缩 · "
                     "表演脚本格式 · 抽全原视频画面 · 自制概念图 · 事实核查纠错 · 附录齐全"),
        "video": os.path.basename(video),
        "duration_sec": round(dur, 1),
        "asr_segments": len(segs),
        "images": res["total_images"],
        "script_chars": res["script_chars"],
        "checks": res.get("checks", {}),
        "quality_gate": res.get("quality_gate", []),
        "corrections": res["corrections"],
        "script_path": res["script_path"],
        "pdf_path": res["pdf_path"],
        "assets": assets,
        "preview": preview,
    }


def build_faithful_script(cfg, segs, assets, url, dur):
    """忠实逐字稿：口播 = 原 ASR，画面 = 原视频抽帧，不改写。"""
    assets_by_key = {a["key"]: a for a in assets}
    # 画面按时间落位：每个 ASR 段归到它发生时正在显示的那张画面
    asset_times = [a["time_sec"] for a in assets]
    segments = []
    cur = None
    idx = 0
    for s, e, tx in segs:
        # 找到当前时刻对应的画面（最后一个 time_sec <= s 的）
        while idx + 1 < len(assets) and asset_times[idx + 1] <= s:
            idx += 1
        k = assets[idx]["key"] if idx < len(assets) else None
        # 每 ~90 秒或换画面时切一段
        if cur is None:
            cur = {"title": f"段 1 · 原视频 {int(s//60):02d}:{int(s%60):02d} 起",
                   "text": "", "images": [], "budget": "",
                   "start": s}
        cur["text"] += tx + "\n"
        if k and k not in cur["images"]:
            cur["images"].append(k)
        # 段落长度控制：> 90 秒 或 正文已 > 400 字则切
        if e - cur["start"] > 90 or len(cur["text"]) > 400:
            segments.append(cur)
            cur = {"title": f"段 {len(segments)+1} · 原视频 {int(s//60):02d}:{int(s%60):02d} 起",
                   "text": "", "images": [], "budget": "", "start": s}
    if cur and cur["text"].strip():
        segments.append(cur)

    meta = (f"**来源**：{url} ｜ 时长 {dur/60:.1f} 分钟 ｜ "
            f"口播 = 原视频 ASR（{len(segs)} 段带时间戳）｜ "
            f"画面 = 原视频抽帧（{len(assets)} 张）\n> **本稿为忠实转写**，未改写观点；"
            f"画面全部来自原视频。")
    return ops.assemble_script_md("原视频忠实逐字稿", meta, segments, assets_by_key)




def extract_markdown(text):
    """从 LLM 返回里抽出 ```markdown ... ``` 块，没有就原样返回。"""
    m = re.search(r"```markdown\s*(.*?)```", text, re.S)
    if m:
        return m.group(1).strip()
    m = re.search(r"```\s*(.*?)```", text, re.S)
    if m:
        return m.group(1).strip()
    # 没有围栏：去掉可能的解释性前后缀
    lines = text.strip().split("\n")
    for i, l in enumerate(lines):
        if l.startswith("# "):
            return "\n".join(lines[i:]).strip()
    return text.strip()


def ensure_frames_in_script(md, frame_manifest):
    """兜底：保证课件里有图。LLM 漏嵌的画面按段落边界散布补回。

    frame_manifest 每项含 rel_path（frames_clean/Vxxx.jpg）。已存在的图片保留。
    """
    if not frame_manifest:
        return md
    present = set(re.findall(r"frames_clean/([A-Za-z0-9_.-]+\.jpg)", md))
    missing = [fm for fm in frame_manifest
               if os.path.basename(fm["rel_path"]) not in present]
    if not missing:
        return md
    paras = md.split("\n\n")
    n = len(paras)
    if n <= 1:
        tail = "\n\n".join(
            f"![{fm.get('note', '画面')}]({fm['rel_path']})" for fm in missing)
        return md + "\n\n" + tail
    step = max(1, n // (len(missing) + 1))
    inserts = {}
    pos = 0
    for fm in missing:
        pos = min(pos + step, n - 1)
        inserts.setdefault(pos, []).append(
            f"![{fm.get('note', '画面')}]({fm['rel_path']})")
        pos += 1
    out = []
    for i, p in enumerate(paras):
        out.append(p)
        for img in inserts.get(i, []):
            out.append(img)
    return "\n\n".join(out)


# ================================================================ 链路 C
def run_doc_to_courseware(cfg, tid, setp, log):
    """上传文档 → 标杆级讲课逐字稿课件（指定风格）。

    与链路A 同产物结构（MD + 内嵌图 PDF），区别是输入是文档而不是视频：
    文档原有标题结构作为大纲骨架，图全部来自自制概念图（+ 文档自带图）。
    """
    from . import jobs
    from . import doc_courseware as dc

    params = jobs.get_task(cfg, tid)["params"]
    task_dir = jobs.get_task(cfg, tid)["dir"]
    out_dir = os.path.join(cfg["outputs_dir"], f"课件_{tid}")
    os.makedirs(out_dir, exist_ok=True)
    doc_path = params.get("doc_path")
    if not doc_path or not os.path.exists(doc_path):
        raise ValueError("文档文件不存在")
    style_name = params.get("style", "faithful")
    intensity = int(params.get("intensity", 3))
    extra_notes = params.get("notes", "")

    def _stage(s, l, f=0.0):
        try:
            jobs.stage_progress(cfg, tid, s, l, f)
        except Exception:
            pass

    res = dc.build_doc_courseware(
        cfg, doc_path, out_dir, style_name=style_name, intensity=intensity,
        log=log, stage_cb=lambda s, l, f: _stage(s, l, f),
        extra_notes=extra_notes, workdir=os.path.join(task_dir, "docwork"))

    assets = []
    for m in res["frame_manifest"]:
        ap = os.path.join(out_dir, m["rel_path"])
        assets.append({"key": m["key"], "file": ap, "note": m.get("note", ""),
                       "time_sec": 0, "rel_path": m["rel_path"]})
    for d in res["diagram_manifest"]:
        ap = os.path.join(out_dir, d["rel_path"])
        assets.append({"key": os.path.basename(d["rel_path"]), "file": ap,
                       "note": d.get("desc", ""), "time_sec": 0,
                       "rel_path": d["rel_path"]})

    try:
        with open(res["script_path"], encoding="utf-8") as fh:
            preview = fh.read()[:4000]
    except Exception:
        preview = (res.get("script") or "")[:4000]

    from . import styles as _styles
    try:
        style_name_cn = _styles.get_preset(style_name)["name"]
    except Exception:
        style_name_cn = style_name

    return {
        "used_llm": True,
        "llm_note": (f"已用小米 LLM 把文档转成「{style_name_cn}」讲课课件："
                     "结构化分期 · 展开不压缩 · 表演脚本格式 · 自制概念图 · 事实核查纠错 · 附录齐全"),
        "video": os.path.basename(doc_path),
        "duration_sec": round(res["est_duration_min"] * 60, 1),
        "asr_segments": 0,
        "images": res["total_images"],
        "script_chars": res["script_chars"],
        "checks": res.get("checks", {}),
        "quality_gate": res.get("quality_gate", []),
        "corrections": res["corrections"],
        "script_path": res["script_path"],
        "pdf_path": res["pdf_path"],
        "assets": assets,
        "preview": preview,
    }


# ================================================================ 链路 B
def run_video_review(cfg, tid, setp, log):
    """上传本地视频 → 评估 → 改进稿。"""
    from . import jobs
    params = jobs.get_task(cfg, tid)["params"]
    task_dir = jobs.get_task(cfg, tid)["dir"]
    video = params.get("video_path")
    if not video or not os.path.exists(video):
        raise ValueError("视频文件不存在")
    style_name = params.get("style", "zhangxuefeng")
    intensity = int(params.get("intensity", 3))
    extra_notes = params.get("notes", "")
    baseline_script = params.get("baseline_script", "")

    # ── 1 音轨
    jobs.stage_progress(cfg, tid, "extract_audio", "抽取音轨", 0.5)
    wav = os.path.join(task_dir, "audio.wav")
    ops.extract_audio(cfg, video, wav, log=log)
    dur = ops.ffprobe_duration(cfg, video)

    # ── 2 ASR
    jobs.stage_progress(cfg, tid, "asr", "语音转写（本地离线）", 0.1)
    asr_txt = os.path.join(task_dir, "asr.txt")
    ops.run_asr(cfg, wav, asr_txt, log=log, workdir=task_dir,
                progress_cb=lambda f: jobs.stage_progress(
                    cfg, tid, "asr", "语音转写（本地离线）", f))
    segs = ops.parse_asr_file(asr_txt)
    log(f"  ASR {len(segs)} 段 / {dur:.0f}s")
    if not segs:
        raise RuntimeError("ASR 未产出任何内容")

    full = "".join(t for _, _, t in segs)
    cn = len(CN.findall(full))

    # ── 3 指标分析
    jobs.stage_progress(cfg, tid, "analyze", "演讲指标分析", 0.3)
    metrics = analyze_speech(segs, dur, cn, cfg)
    log(f"  指标: 时长 {dur:.0f}s 字数 {cn} 语速 {metrics['cpm']}")

    # ── 4 画面分析
    jobs.stage_progress(cfg, tid, "visual", "画面构成分析", 0.2)
    vis = analyze_visual(cfg, video, task_dir, log=log)
    log(f"  画面: 切图 {vis['scene_changes']} 次 可见率 {vis['diagram_ratio']*100:.0f}%")

    # ── 5 对账
    jobs.stage_progress(cfg, tid, "baseline", "与课件逐段对账", 0.5)
    gap = build_gap(cfg, metrics, vis, baseline_script)
    log(f"  通用指标对账: 不达标 {gap['n_fail']} 项")

    report = build_review_report(cfg, metrics, vis, gap, segs, video, dur)
    report_path = os.path.join(cfg["outputs_dir"], f"录屏评估报告_{tid}.md")
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write(report)
    log(f"  评估报告 {report_path}")

    # ── 5b 课件↔试讲 逐段对账（有课件基线才做）→ 掌握度 + 对账报告
    mastery = None
    align_report_path = None
    sections = []
    if baseline_script and baseline_script.strip():
        try:
            from . import rehearsal_review as rr
            sections = rr.parse_courseware_sections(baseline_script)
            if sections:
                jobs.stage_progress(cfg, tid, "baseline", "与课件逐段对账", 0.7)
                transcript = ops.asr_to_text(segs)
                verdicts = rr.align_rehearsal(cfg, sections, transcript, log=log)
                planned = sum((s["duration_min"] or 0) for s in sections)
                mastery = rr.compute_mastery(
                    verdicts, sections, actual_sec=dur, planned_sec=planned * 60 or None)
                log(f"  逐段对账: 掌握度 {mastery['score']} 分 · "
                    f"覆盖 {mastery['coverage']*100:.0f}% · 说错 {mastery['wrong_count']} 处")
                meta = {"title": "试讲对账", "courseware": "（上传的课件）",
                        "trial": os.path.basename(video), "actual_min": dur / 60,
                        "time": time.strftime("%Y-%m-%d %H:%M")}
                align_report_path = os.path.join(cfg["outputs_dir"], f"试讲对账报告_{tid}.md")
                with open(align_report_path, "w", encoding="utf-8") as fh:
                    fh.write(rr.build_alignment_report(mastery, sections, meta))
                log(f"  对账报告 {align_report_path}")
            else:
                log("  基线课件没有可识别的期/段结构，跳过逐段对账")
        except Exception as e:
            # 逐段对账失败不能拖垮整条评估，显式报出后降级为通用指标
            log(f"  逐段对账失败（降级为通用指标评估）：{e}")

    # ── 6 改进稿
    jobs.stage_progress(cfg, tid, "improve", "生成改进稿", 0.0)
    if mastery and sections:
        # 有逐段对账 → 改进稿按「对账指出的问题」改写（比通用指标精准）
        from . import rehearsal_review as rr
        improved_text = rr.build_next_draft(cfg, baseline_script, mastery,
                                             style_name=style_name,
                                             intensity=intensity, log=log)
        improved = {"text": improved_text, "used_llm": True}
        jobs.stage_progress(cfg, tid, "improve", "生成改进稿", 1.0)
    else:
        improved = build_improved_script(
            cfg, style_name, intensity, segs, gap, metrics, vis, extra_notes,
            baseline_script,
            progress_cb=lambda f: jobs.stage_progress(
                cfg, tid, "improve", "生成改进稿", f))
    used_llm = improved.get("used_llm", False)
    log(f"  改进稿 {len(improved['text'])} 字符 (LLM={used_llm})")

    jobs.stage_progress(cfg, tid, "self_check", "稿子自检", 0.4)
    checks = ops.self_check(cfg, improved["text"])
    log(f"  自检: {checks}")

    imp_path = os.path.join(cfg["outputs_dir"], f"改进稿_{tid}.md")
    with open(imp_path, "w", encoding="utf-8") as fh:
        fh.write(improved["text"])
    shutil.copy(imp_path, os.path.join(task_dir, "改进稿.md"))

    # ── 7 归档到练习项目（掌握度趋势 / 跨轮薄弱段）
    practice_key = None
    practice_report_path = None
    if mastery:
        try:
            from . import practice_log as pl
            practice_key = pl.practice_key_from_courseware(baseline_script,
                                                           os.path.basename(video))
            detail = mastery.get("detail", [])
            pl.archive_round(cfg, practice_key, {
                "practice_title": practice_key,
                "courseware_path": (params.get("baseline_path") or ""),
                "courseware_sig": hashlib.md5(
                    baseline_script.encode("utf-8")).hexdigest()[:8],
                "trial": os.path.basename(video),
                "trial_path": video,
                "score": mastery["score"],
                "dims": {k: mastery.get(k) for k in
                         ("coverage", "fidelity", "check_q_rate", "action_rate",
                          "picture_rate", "duration_fit")},
                "segments": [{"idx": d.get("idx"), "title": d.get("title"),
                              "part": d.get("part"), "covered": d.get("covered"),
                              "missed": len(d.get("missed") or []),
                              "wrong": len(d.get("wrong") or []),
                              "comment": d.get("comment", "")} for d in detail],
                "improve_path": imp_path,
                "align_report_path": align_report_path,
                "threshold": pl.DEFAULT_THRESHOLD,
            })
            pr = pl.render_practice(cfg, practice_key)
            practice_report_path = pr.get("report_path")
            log(f"  练习归档: 轮次 {len(pr['data'].get('rounds', []))} · "
                f"项目「{practice_key}」 · 报告 {practice_report_path}")
        except Exception as e:
            log(f"  练习归档失败（不影响评估结果）：{e}")

    return {
        "used_llm": used_llm,
        "duration_sec": round(dur, 1),
        "cn_chars": cn,
        "metrics": metrics,
        "visual": vis,
        "gap": gap,
        "checks": checks,
        "report_path": report_path,
        "align_report_path": align_report_path,
        "mastery": mastery,
        "practice_key": practice_key,
        "practice_report_path": practice_report_path,
        "script_path": imp_path,
        "preview": improved["text"][:4000],
        "report_preview": report[:3000],
    }


# ================================================================ 分析
ANALOGY_MARKS = ["就像", "好比", "相当于", "好像", "打个比方", "你可以理解为",
                 "比如说", "想象", "比喻", "类比", "我用一个", "特别土",
                 "门轴", "门缝", "推开门", "门后面", "手碰到门", "手滑"]
CHECK_Q = ["大家能不能", "能明白吗", "大家看", "听懂了吗", "有没有问题",
           "觉得对吗", "是这样吗", "对不对", "你品品", "你有没有", "你猜",
           "那什么时候", "什么才叫", "你要是", "自己试", "对吧", "是不是"]
ACTION = ["拿你自己", "复盘", "标一下", "去练", "试试", "做一遍", "作业",
          "对照着", "打开图", "截图", "我们下期", "回去看", "自己画",
          "打开你自己", "你标", "去标"]
TERMS = ["遇阻", "极值", "极值点", "趋势线", "前高", "前低", "结构支点",
         "高点上移", "低点下移", "假突破", "交易区间", "通道", "之字",
         "回调", "反向K线", "深度回调", "三推", "双顶", "双底", "趋势"]
FILLERS = ["呃", "那个", "这个", "对吧", "是不是", "然后呢", "就是说",
           "其实", "我们说", "嗯", "OOK", "OK"]


def analyze_speech(segs, dur, cn, cfg):
    full = "".join(t for _, _, t in segs)
    # 重复检测：相邻 90s 窗口的高重合
    win = 90
    texts = []
    for s, e, t in segs:
        texts.append((s, e, re.sub(r"[^一-鿿]", "", t)))

    def grams(t, n=4):
        return set(t[i:i + n] for i in range(max(1, len(t) - n + 1)))

    windows = []
    for t0 in range(0, int(dur), win):
        chunk = "".join(t for s, e, t in texts if s >= t0 and s < t0 + win)
        if len(chunk) >= 20:
            windows.append((t0, chunk))
    pairs = []
    for i in range(len(windows)):
        for j in range(i + 1, len(windows)):
            gi, gj = grams(windows[i][1]), grams(windows[j][1])
            if gi and gj:
                inter = len(gi & gj)
                if inter:
                    jac = inter / min(len(gi), len(gj))
                    if jac >= 0.35:
                        pairs.append((windows[i][0], windows[j][0], round(jac, 3)))
    rep_chars = 0
    for a, b, _ in pairs:
        rep_chars += len(windows[b][1])

    return {
        "duration_sec": round(dur, 1),
        "cn_chars": cn,
        "cpm": round(cn / max(dur, 1) * 60, 1),
        "action_instructions": sum(full.count(k) for k in ACTION),
        "check_questions": sum(full.count(k) for k in CHECK_Q),
        "analogy_marks": sum(1 for k in ANALOGY_MARKS if k in full),
        "filler_total": sum(full.count(k) for k in FILLERS),
        "filler_per_1000cn": round(sum(full.count(k) for k in FILLERS) / max(cn, 1) * 1000, 1),
        "term_total": sum(full.count(k) for k in TERMS),
        "term_per_1000cn": round(sum(full.count(k) for k in TERMS) / max(cn, 1) * 1000, 1),
        "repeat_pairs": len(pairs),
        "repeat_ratio": round(rep_chars / max(cn, 1), 3) if cn else 0,
        "segments": len(segs),
    }


def analyze_visual(cfg, video, task_dir, log=None):
    """画面构成：切图次数 + 静态占比 + 流程图可见率（近似）。

    性能关键：**-skip_frame nokey** —— 只解关键帧，跳过 P/B 帧。
    实测 20 分钟 3584x2240@60fps 录屏：117s → 31s（快 3.8 倍）。
    """
    import numpy as np
    from PIL import Image
    FFMPEG = cfg["ffmpeg"]
    probe = os.path.join(task_dir, "_vprobe")
    os.makedirs(probe, exist_ok=True)
    ops.run_cmd(cfg, [FFMPEG, "-y", "-hide_banner", "-v", "error",
                      "-skip_frame", "nokey", "-i", video,
                      "-vf", "fps=1,scale=320:-2", os.path.join(probe, "p_%05d.jpg")])
    files = sorted(f for f in os.listdir(probe) if f.endswith(".jpg"))
    arrs = [np.asarray(Image.open(os.path.join(probe, f)).convert("L"),
                        dtype=np.float32) / 255.0 for f in files]
    changes = 0
    static = 0
    diagram = 0
    for i in range(1, len(arrs)):
        d = float(np.abs(arrs[i] - arrs[i - 1]).mean())
        if d > 0.06:
            changes += 1
        elif d < 0.006:
            static += 1
        if arrs[i].mean() < 90:
            diagram += 1
    n = max(1, len(arrs) - 1)
    # 注意：不在运行时做批量删除。环境的删除安全钩子会在单次 rmtree 删除 >50 个文件时
    # 要求人工确认，而后台 uvicorn 进程拿不到确认会永久卡死。改为重命名保留目录
    # （任务目录本就是临时工作区，留着无害），绕开该钩子。
    try:
        os.rename(probe, probe + "_done")
    except OSError:
        pass
    return {
        "frames": len(arrs),
        "scene_changes": changes,
        "static_pairs": static,
        "static_ratio": round(static / n, 3),
        "diagram_ratio": round(diagram / max(1, len(arrs)), 3),
    }


def count_courseware_parts(baseline_script):
    """课件期数（用于按「期数 × 单期承诺」算总时长）。

    时长承诺是**单期 8~10 分钟**（用户 2026-10-24 定调），整篇 = 期数 × 单期。
    拿单期目标去比整篇录屏会误判"超时 2 倍"。
    """
    if not baseline_script or not baseline_script.strip():
        return 1
    parts = set()
    for ln in baseline_script.split("\n"):
        m = re.match(r"^#{1,6}\s+(.*)$", ln.strip())
        if not m:
            continue
        t = m.group(1).strip()
        if re.match(r"^第\s*[0-9一二三四五六七八九十]+\s*期", t) or t[:6].endswith("期"):
            parts.add(t)
    return max(1, len(parts))


def build_gap(cfg, metrics, vis, baseline_script):
    """与稿子对账。"""
    lo = cfg["script_target_min"]
    hi = cfg["script_target_max"]
    dur = metrics["duration_sec"]
    n_parts = count_courseware_parts(baseline_script)
    t_lo, t_hi = lo * n_parts, hi * n_parts
    rows = [
        {"metric": "时长", "promised": f"{t_lo}~{t_hi} 分钟（{n_parts}期 × 单期{lo}~{hi}）",
         "actual": f"{dur/60:.1f} 分钟",
         "ok": t_lo * 60 <= dur <= t_hi * 60,
         "fix": "删重复讲解段、合并同类案例"},
        {"metric": "行动指令", "promised": "≥3",
         "actual": metrics["action_instructions"],
         "ok": metrics["action_instructions"] >= 3,
         "fix": "结尾加「翻 20 个图表逐个标」这类可执行动作"},
        {"metric": "检验性提问", "promised": "≥3",
         "actual": metrics["check_questions"],
         "ok": metrics["check_questions"] >= 3,
         "fix": "关键处设问并停 1 秒，给观众回答时间"},
        {"metric": "类比", "promised": "≥8",
         "actual": metrics["analogy_marks"],
         "ok": metrics["analogy_marks"] >= 8,
         "fix": "每个核心概念配一个贯穿的类比"},
        {"metric": "重复内容占比", "promised": "≤10%",
         "actual": f"{metrics['repeat_ratio']*100:.1f}%",
         "ok": metrics["repeat_ratio"] <= 0.10,
         "fix": "同一批内容只讲一遍；讲乱了不要从头重来"},
        {"metric": "流程图可见率", "promised": "≥95%",
         "actual": f"{vis['diagram_ratio']*100:.0f}%",
         "ok": vis["diagram_ratio"] >= 0.95,
         "fix": "把图常驻画面，别靠滚动"},
    ]
    return {"rows": rows, "n_fail": sum(1 for r in rows if not r["ok"])}


def build_review_report(cfg, metrics, vis, gap, segs, video, dur):
    L = []
    A = L.append
    A("# 录屏评估报告")
    A("")
    A(f"> 视频：`{os.path.basename(video)}` ｜ 时长 {dur/60:.1f} 分钟 ｜ "
      f"生成时间 {time.strftime('%Y-%m-%d %H:%M')}")
    A("")
    A("## 一、结论")
    A("")
    n = gap["n_fail"]
    A(f"**{'全部达标' if n == 0 else f'{n} 项不达标'}**"
      + ("" if n == 0 else "。下面是逐项差距和改法。"))
    A("")
    A("## 二、逐项对账")
    A("")
    A("| 指标 | 目标 | 实测 | 状态 | 改法 |")
    A("|---|---|---|---|---|")
    for r in gap["rows"]:
        A(f"| {r['metric']} | {r['promised']} | {r['actual']} | "
          f"{'✅' if r['ok'] else '❌'} | {r['fix']} |")
    A("")
    A("## 三、演讲指标明细")
    A("")
    A("| 指标 | 值 |")
    A("|---|---|")
    for k, v in metrics.items():
        if k in ("segments",):
            continue
        A(f"| {k} | {v} |")
    A("")
    A("## 四、画面构成")
    A("")
    A("| 指标 | 值 |")
    A("|---|---|")
    for k, v in vis.items():
        A(f"| {k} | {v} |")
    A("")
    A("## 五、完整转写（带时间戳）")
    A("")
    A("```text")
    A(ops.asr_to_text(segs))
    A("```")
    A("")
    A("---")
    A("")
    A("*由 `webapp` 自动生成，数字来自实测。*")
    return "\n".join(L)


def build_improved_script(cfg, style_name, intensity, segs, gap, metrics,
                          vis, extra_notes, baseline_script,
                          progress_cb=None, log=None):
    """基于评估差距，生成改进稿。"""
    source_text = ops.asr_to_text(segs)
    fix_list = "\n".join(f"- {r['metric']}：实测 {r['actual']}（目标 {r['promised']}）→ {r['fix']}"
                         for r in gap["rows"] if not r["ok"]) or "- （无明显差距，保持原有讲法）"

    system, user, _ = styles.build_prompt(
        style_name, intensity, source_text, extra_notes,
        cfg["script_target_min"], cfg["script_target_max"], cfg["script_speed_cpm"])
    user = ("【本次录制的评估结果与必须修掉的问题】\n" + fix_list + "\n\n" +
            "请在改写时**逐条落实**上述改法。\n\n" + user)

    styled = ops.call_llm_stream(
        cfg, system, user, log=log, progress_cb=progress_cb)
    if styled:
        if progress_cb:
            progress_cb(1.0)
        return {"text": ops.extract_markdown(styled), "used_llm": True}

    # 无小米（双通道均失败）：默认直接报错，绝不静默降级。
    # 仅当用户在 config 显式开启 offline_engine 时，才退化到 ch3 本地引擎。
    if not cfg.get("offline_engine"):
        raise RuntimeError(
            "小米 LLM 不可用（双通道 ch1/ch2 均失败）—— 无法生成改进稿。"
            "请检查 config.json 的 llm_api_key / llm_base_url 与网络连通性。"
        )
    if not segs:
        raise RuntimeError("无可用转写，离线引擎也无法生成改进稿")
    dur = segs[-1][1] if segs else 0
    faithful = _segs_to_faithful_md(segs, dur)
    gaps = [f"{r['metric']}：实测 {r['actual']}（目标 {r['promised']}）→ {r['fix']}"
            for r in gap["rows"] if not r["ok"]] or ["（无明显差距，保持原有讲法）"]
    text = styles.local_rewrite(faithful, style_name, intensity, extra_notes, gaps=gaps)
    if progress_cb:
        progress_cb(1.0)
    return {"text": text, "used_llm": False}


def _segs_to_faithful_md(segs, dur):
    """把录音转写切成带 '## 段 N' 标记的忠实稿，供 ch3 离线引擎注入脚手架。"""
    if not segs:
        return None
    chunks = []
    cur, cur_start, cur_chars = [], segs[0][0], 0
    for s, e, t in segs:
        cur.append(t)
        cur_chars += len(t)
        if e - cur_start > 90 or cur_chars > 400:
            chunks.append((cur_start, "\n".join(cur)))
            cur, cur_start, cur_chars = [], e, 0
    if cur:
        chunks.append((cur_start, "\n".join(cur)))
    L = ["# 改进稿", "",
         f"> 来源：本次录制转写 ｜ 时长 {dur/60:.1f} 分钟 ｜ 本地确定性引擎生成，非 LLM", ""]
    for i, (st, txt) in enumerate(chunks, 1):
        L.append(f"## 段 {i} · 原录制 {int(st//60):02d}:{int(st%60):02d} 起")
        L.append("")
        L.append(txt)
        L.append("")
    return "\n".join(L)
