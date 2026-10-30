#!/usr/bin/env python3
"""核心业务：两条链路的实际执行。

链路 A：B 站 URL → 风格化讲课稿
    download → extract_audio → ASR → 场景切分 → 抽帧 → 画面加工
    → 忠实逐字稿 → LLM 风格化 → 自检

链路 B：上传本地视频 → 评估 → 改进稿
    upload → extract_audio → ASR → 指标/画面分析 → 与稿子对账
    → LLM 生成改进稿 → 自检

所有外部调用都写日志；阶段失败显式抛出，不静默吞。
"""
import json
import os
import re
import shutil
import subprocess
import time

from . import config as cfgmod
from . import styles

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def extract_markdown(text):
    """从 LLM 返回里抽出 ```markdown ... ``` 块，没有就原样返回。"""
    if not text:
        return ""
    m = re.search(r"```markdown\s*(.*?)```", text, re.S)
    if m:
        return m.group(1).strip()
    m = re.search(r"```\s*(.*?)```", text, re.S)
    if m:
        return m.group(1).strip()
    lines = text.strip().split("\n")
    for i, l in enumerate(lines):
        if l.startswith("# "):
            return "\n".join(lines[i:]).strip()
    return text.strip()

CN = re.compile(r"[\u4e00-\u9fff]")


# ================================================================ 工具函数
def run_cmd(cfg, cmd, cwd=None, log=None, timeout=3600):
    """跑外部命令。失败显式抛异常。

    log 可以是单参函数(msg) 或双参函数(tid, msg)；这里统一按单参调用。
    """
    t0 = time.time()

    def emit(msg):
        if log is None:
            return
        try:
            log(msg)          # 单参：log(msg)
        except TypeError:
            pass

    emit(f"$ {' '.join(str(c) for c in cmd[:6])}")
    r = subprocess.run([str(c) for c in cmd], cwd=cwd, capture_output=True,
                       text=True, errors="replace", timeout=timeout)
    tail = (r.stdout or "").strip().splitlines()[-3:]
    emit(f"  rc={r.returncode} {time.time()-t0:.1f}s :: {' | '.join(tail)[:300]}")
    if r.returncode != 0:
        raise RuntimeError(f"命令失败 rc={r.returncode}: {' '.join(str(c) for c in cmd[:4])}\n"
                           f"{(r.stderr or '')[-1200:]}")
    return r.stdout


def ffprobe_duration(cfg, path):
    """取视频时长。

    注意：ffmpeg 只读元信息时必然返回 rc=1（"At least one output file must be
    specified"），这不是错误。所以优先用 ffprobe；没有 ffprobe 时用 ffmpeg
    并接受 rc=1。
    """
    import shutil
    probe = shutil.which("ffprobe")
    if not probe:
        # 常见位置：与 ffmpeg 同目录
        cand = os.path.join(os.path.dirname(cfg["ffmpeg"]), "ffprobe")
        probe = cand if os.path.exists(cand) else None
    if probe:
        r = subprocess.run([probe, "-v", "error", "-show_entries",
                            "format=duration", "-of", "csv=p=0", path],
                           capture_output=True, text=True, errors="replace")
        v = (r.stdout or "").strip().split("\n")[0]
        try:
            return float(v)
        except (ValueError, IndexError):
            pass
    # 退回 ffmpeg：接受 rc=1（元信息已打到 stderr）
    r = subprocess.run([cfg["ffmpeg"], "-hide_banner", "-i", path],
                       capture_output=True, text=True, errors="replace")
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", r.stderr or "")
    if not m:
        raise RuntimeError("无法解析视频时长")
    h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return h * 3600 + mi * 60 + s


def extract_audio(cfg, src, dst, log=None):
    """抽音轨到 16k 单声道 wav。

    若源文件没有音轨，尝试从同名 m4a 补；都没有则报错（明确失败，不静默）。
    """
    try:
        run_cmd(cfg, [cfg["ffmpeg"], "-y", "-hide_banner", "-v", "error",
                      "-i", src, "-vn", "-ac", "1", "-ar", "16000",
                      "-c:a", "pcm_s16le", dst], log=log)
    except Exception as e:
        # 源无音轨 → 找同目录的独立音轨合并
        d = os.path.dirname(src)
        base = os.path.splitext(os.path.basename(src))[0]
        cands = [os.path.join(d, f) for f in os.listdir(d)
                 if f.startswith(("audio.", base + ".")) and
                 f.endswith((".m4a", ".mp3", ".aac", ".wav", ".m4s"))]
        if not cands:
            raise RuntimeError(f"音轨抽取失败且找不到独立音轨：{e}")
        a = max(cands, key=os.path.getsize)
        run_cmd(cfg, [cfg["ffmpeg"], "-y", "-hide_banner", "-v", "error",
                      "-i", a, "-ac", "1", "-ar", "16000",
                      "-c:a", "pcm_s16le", dst], log=log)
    if not os.path.exists(dst) or os.path.getsize(dst) == 0:
        raise RuntimeError("音轨抽取失败：输出为空")
    return dst


# ================================================================ ASR
# 注意：模板用 {name} 占位 + str.replace 注入，
# 不用 % 格式化 —— 脚本正文里有大量 %02d、%(...) 等 printf 记号，
# 用 % 格式化会与之冲突（not enough arguments for format string）。
_ASR_SCRIPT = r'''
import os, sys, json, time
os.environ.setdefault("HF_ENDPOINT", "@ENDPOINT@")
from faster_whisper import WhisperModel

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

wav, out = sys.argv[1], sys.argv[2]
t0 = time.time()
model = WhisperModel("@MODEL@", device="@DEVICE@", compute_type="@CTYPE@")
print("MODEL_LOADED %.1f" % (time.time()-t0), flush=True)
t1 = time.time()
segs, info = model.transcribe(wav, language="zh", beam_size=@BEAM@,
                              vad_filter=True, condition_on_previous_text=False)
n = 0
last_p = -1.0
total = getattr(info, "duration", None) or 0
with open(out, "w", encoding="utf-8") as fh:
    fh.write("# ASR 转写\n\n")
    for s in segs:
        st = int(s.start // 60); ss = s.start - st * 60
        en = int(s.end // 60); es = s.end - en * 60
        fh.write("[%02d:%06.3f-%02d:%06.3f] %s\n" % (st, ss, en, es, s.text.strip()))
        n += 1
        if total and s.end:
            p = min(1.0, s.end / total)
            if p - last_p >= 0.05:
                print("PROGRESS %.3f" % p, flush=True)
                last_p = p
print("PROGRESS 1.000", flush=True)
print("TRANSCRIBE_DONE %d %.1f" % (n, time.time()-t1), flush=True)
'''


def run_asr(cfg, wav, out_txt, log=None, workdir=None, progress_cb=None):
    """本地离线 ASR（faster-whisper）。progress_cb(frac 0~1) 用于上报转写过程进度。"""
    if not workdir:
        workdir = os.path.dirname(os.path.abspath(wav)) or cfg["work_dir"]
    os.makedirs(workdir, exist_ok=True)
    script = os.path.join(workdir, "_asr_run.py")
    body = (_ASR_SCRIPT
            .replace("@ENDPOINT@", cfg["hf_endpoint"])
            .replace("@MODEL@", cfg["asr_model"])
            .replace("@DEVICE@", cfg["asr_device"])
            .replace("@CTYPE@", cfg["asr_compute_type"])
            .replace("@BEAM@", str(cfg["asr_beam_size"])))
    with open(script, "w", encoding="utf-8") as fh:
        fh.write(body)
    env = dict(os.environ)
    env["HF_ENDPOINT"] = cfg["hf_endpoint"]
    t0 = time.time()
    last_p = -1.0
    proc = subprocess.Popen([cfg["python_asr"], script, wav, out_txt],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, errors="replace", env=env, bufsize=1)
    with proc.stdout:
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("PROGRESS "):
                try:
                    p = float(line.split()[1])
                    if progress_cb and abs(p - last_p) >= 0.0:
                        progress_cb(p)
                        last_p = p
                except (ValueError, IndexError):
                    pass
            elif line.startswith("MODEL_LOADED") or line.startswith("TRANSCRIBE_DONE"):
                if log:
                    log(f"  ASR {line}")
    rc = proc.wait(timeout=7200)
    if log:
        log(f"  ASR rc={rc} {time.time()-t0:.1f}s")
    if rc != 0 or not os.path.exists(out_txt):
        raise RuntimeError(f"ASR 失败 rc={rc}")
    return out_txt


def download_video(cfg, url, out_tmpl, fmt, log=None, timeout=1800, progress_cb=None):
    """下载视频并上报进度（解析 yt-dlp 的 [download] X% 行）。

    ★ 失败时把 yt-dlp 的原始输出尾部带进异常 message —— 否则上层只拿到
      "rc=1"，既看不出是网络受限还是格式不支持，也没法给出可执行的建议。
    """
    cmd = [cfg["yt_dlp"], "--newline", "-f", fmt, "-o", out_tmpl, url]
    t0 = time.time()
    tail = []
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, errors="replace", bufsize=1)
    last_p = -1.0
    with proc.stdout:
        for line in proc.stdout:
            line = line.strip()
            m = re.search(r"\[download\]\s+([\d.]+)%", line)
            if m and progress_cb:
                try:
                    p = float(m.group(1)) / 100.0
                    if p - last_p >= 0.01:
                        progress_cb(p)
                        last_p = p
                except ValueError:
                    pass
            elif line:
                tail.append(line)
                if len(tail) > 40:
                    tail.pop(0)
                if log and ("Destination" in line or "ERROR" in line):
                    log(f"  yt-dlp: {line}")
    rc = proc.wait(timeout=timeout)
    if log:
        log(f"  下载完成 rc={rc} {time.time()-t0:.1f}s")
    if rc != 0:
        detail = "\n".join(tail[-12:]) if tail else "(无输出)"
        raise RuntimeError(f"视频下载失败（yt-dlp rc={rc}）\n{detail}")
    return rc


def parse_asr_file(path):
    """解析 ASR 文件 → [(start, end, text)]"""
    out = []
    pat = re.compile(r"\[(\d+):(\d+\.\d+)-(\d+):(\d+\.\d+)\]\s*(.*)")
    for line in open(path, encoding="utf-8"):
        m = pat.match(line.strip())
        if not m:
            continue
        s = int(m.group(1)) * 60 + float(m.group(2))
        e = int(m.group(3)) * 60 + float(m.group(4))
        out.append((round(s, 2), round(e, 2), m.group(5).strip()))
    return out


def asr_to_text(segs):
    return "\n".join(f"[{int(s//60):02d}:{s%60:06.3f}-{int(e//60):02d}:{e%60:06.3f}] {t}"
                      for s, e, t in segs)


# ================================================================ LLM
def call_llm(cfg, system, user, log=None, max_tokens=None):
    """OpenAI 兼容协议调用，双通道容错。

    依次尝试所有已配置通道（主通道 llm_* → 备用通道 llm2_*）。
    任一通道成功即返回文本；全部失败或无任何通道配置时返回 None（调用方降级）。
    兼容小米 MiMo：同时带 api-key 与 Authorization: Bearer 两个头。
    max_tokens：可选，覆盖 cfg["llm_max_tokens"]（长输出任务用，避免被截断）。
    """
    import urllib.request
    channels = cfgmod.llm_channels(cfg)
    if not channels:
        return None
    payload = {
        "model": None,  # 每通道单独覆盖
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": cfg["llm_temperature"],
        "max_tokens": int(max_tokens or cfg["llm_max_tokens"]),
    }
    last_err = None
    for base, key, model, label in channels:
        payload["model"] = model
        url = base + "/chat/completions"
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {key}",
                     "api-key": key})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=cfg["llm_timeout"]) as r:
                data = json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last_err = e
            if log:
                log(f"  LLM[{label}] 调用失败: {e}")
            continue
        if log:
            log(f"  LLM[{label}] 完成 {time.time()-t0:.1f}s :: {model}")
        try:
            return data["choices"][0]["message"]["content"]
        except Exception:
            last_err = "响应解析失败"
            continue
    if log:
        log(f"  LLM 全部通道失败: {last_err}")
    return None


def call_llm_stream(cfg, system, user, log=None, progress_cb=None, token_cb=None):
    """流式调用（SSE），逐块上报进度。全部通道失败返回 None。

    progress_cb(frac 0~1)：基于"已接收字符数 / 预估输出长度"估算（预估取自脚本目标字数）。
    token_cb(text)：每收到一块增量文本回调（可选）。
    某通道流式失败会自动退回该通道的非流式 call_llm 兜底。
    """
    import urllib.request
    channels = cfgmod.llm_channels(cfg)
    if not channels:
        return None
    # 动态预估输出长度：以输入(user)篇幅为基准——改写稿长度通常与转写同量级。
    # 短录屏输入短 → expected 小 → 进度条能正常爬到 100%，不会卡在低位缓慢爬。
    # 留 1.2 余量避免模型略超输入时过早触顶；下限 600 保证极短输入也有可感知进度。
    src_chars = len(user or "")
    expected = max(600, int(src_chars * 1.2))
    payload = {
        "model": None,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": cfg["llm_temperature"],
        "max_tokens": cfg["llm_max_tokens"],
        "stream": True,
    }
    last_err = None
    for base, key, model, label in channels:
        payload["model"] = model
        url = base + "/chat/completions"
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {key}", "api-key": key})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=cfg["llm_timeout"]) as r:
                full, received, last_rep = [], 0, -1.0
                for raw in r:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        j = json.loads(data)
                    except Exception:
                        continue
                    chs = j.get("choices")
                    if not chs:
                        continue
                    delta = chs[0].get("delta", {}).get("content", "")
                    if not delta:
                        continue
                    full.append(delta)
                    received += len(delta)
                    if token_cb:
                        token_cb(delta)
                    if progress_cb:
                        frac = min(0.98, received / expected)
                        if frac - last_rep >= 0.02:
                            progress_cb(frac)
                            last_rep = frac
                text = "".join(full)
                if progress_cb:
                    progress_cb(1.0)
                if log:
                    log(f"  LLM[{label}] 流式完成 {time.time()-t0:.1f}s :: {model} 字符={len(text)}")
                if text:
                    return text
                last_err = "空响应"
        except Exception as e:
            last_err = e
            if log:
                log(f"  LLM[{label}] 流式调用失败: {e}")
            # 该通道流式失败 → 退回非流式兜底（仍给满进度）
            try:
                fb = call_llm(cfg, system, user, log=log)
                if fb:
                    if progress_cb:
                        progress_cb(1.0)
                    return fb
            except Exception:
                pass
            continue
    if log:
        log(f"  LLM 全部通道流式失败: {last_err}")
    return None


# ================================================================ 画面处理
def build_visual_assets(cfg, video, workdir, log=None, shots=None,
                        frame_interval=None, style_note=""):
    """抽帧 + 画面加工，返回 [{key,file,note,time_sec}]。

    复用已验证的逻辑（关键帧评分 = 标注量 × 图表可见高度）。
    """
    import numpy as np
    from PIL import Image

    FFMPEG = cfg["ffmpeg"]
    interval = frame_interval or cfg["frame_interval_sec"]
    raw_dir = os.path.join(workdir, "frames_raw")
    clean_dir = os.path.join(workdir, "frames_clean")
    os.makedirs(raw_dir, exist_ok=True)
    os.makedirs(clean_dir, exist_ok=True)

    # 1) 一次性 1fps 采样（场景切分 + 抽帧共用这批帧，只解一次视频）
    # 性能：-skip_frame nokey 只解关键帧（实测 20 分钟 60fps 录屏 117s→31s）
    probe_dir = os.path.join(workdir, "_probe")
    os.makedirs(probe_dir, exist_ok=True)
    run_cmd(cfg, [FFMPEG, "-y", "-hide_banner", "-v", "error",
                  "-skip_frame", "nokey", "-i", video,
                  "-vf", "fps=1,scale=320:-2", os.path.join(probe_dir, "p_%05d.jpg")],
            log=log)
    pfiles = sorted(f for f in os.listdir(probe_dir) if f.endswith(".jpg"))
    arrs = []
    for f in pfiles:
        a = np.asarray(Image.open(os.path.join(probe_dir, f)).convert("L"),
                       dtype=np.float32) / 255.0
        arrs.append(a)
    bounds = [0]
    for i in range(1, len(arrs)):
        if float(np.abs(arrs[i] - arrs[i - 1]).mean()) > 0.06:
            bounds.append(i)
    bounds.append(len(arrs))
    scenes = [(a, b) for a, b in zip(bounds[:-1], bounds[1:]) if b - a >= 2]
    log(f"  场景切分：{len(scenes)} 段")

    # 2) 每个场景取代表帧（成品画质）
    # 性能：**一次 ffmpeg 出全部 1fps 采样帧**，而不是每个场景起一个进程 seek。
    # 50 个场景 = 50 次进程启动 + 50 次 seek，极慢。
    reps = []
    if scenes:
        samp_dir = os.path.join(raw_dir, "_samp")
        os.makedirs(samp_dir, exist_ok=True)
        cmd = [FFMPEG, "-y", "-hide_banner", "-v", "error",
               "-skip_frame", "nokey", "-i", video,
               "-vf", f"fps=1,scale={cfg['frame_width']}:-2",
               "-q:v", "3", os.path.join(samp_dir, "s_%05d.jpg")]
        run_cmd(cfg, cmd, log=log)
        samp = sorted(f for f in os.listdir(samp_dir) if f.endswith(".jpg"))
        log(f"  1fps 采样 {len(samp)} 帧（成品画质）")
        # 场景代表帧 = 该场景内采样帧的中位
        for i, (a, b) in enumerate(scenes, 1):
            lo, hi = int(a), int(b)
            cands = [f for k, f in enumerate(samp) if lo <= k < hi] or [samp[min(lo, len(samp)-1)]]
            pick = cands[len(cands) // 2]
            reps.append({"idx": i, "t": round((a + b) / 2, 2),
                         "path": os.path.join(samp_dir, pick)})
    log(f"  场景代表帧：{len(reps)} 张")

    # 3) 加工：分区裁切 + 工具栏抹除
    def chart_h(path):
        im = Image.open(path).convert("L")
        w, h = im.size
        rm = np.asarray(im).mean(axis=1)
        return int((rm[int(h * 0.45):] < 90).sum())

    def ink_score(path):
        im = Image.open(path).convert("RGB")
        w, h = im.size
        lo = np.asarray(im.crop((0, int(h * 0.45), w, h))).astype(np.int16)
        r, g, b = lo[..., 0], lo[..., 1], lo[..., 2]
        mx, mn = lo.max(axis=2), lo.min(axis=2)
        yel = (r > 140) & (g > 120) & (b < 110) & ((r - b) > 70) & ((g - b) > 50)
        wte = (mn > 190)
        ink = float(yel.sum()) * 3.0 + float(wte.sum()) * 0.3
        return ink * min(chart_h(path) / h * 2.2, 1.0)

    def find_toolbar(gray):
        h, w = gray.shape
        x0, y0 = int(w * 0.55), int(h * 0.78)
        reg = gray[y0:, x0:].astype(np.float32)
        if reg.size == 0:
            return None
        rv = np.abs(np.diff(reg, axis=0)).mean(axis=1)
        hr = np.where(rv > 6)[0]
        if len(hr) == 0:
            return None
        top, bot = y0 + int(hr.min()), y0 + int(hr.max()) + 2
        strip = gray[top:bot, x0:].astype(np.float32)
        cv = np.abs(np.diff(strip, axis=1)).mean(axis=0)
        hc = np.where(cv > 6)[0]
        if len(hc) == 0:
            return None
        left, right = x0 + int(hc.min()), x0 + int(hc.max()) + 2
        if (right - left) > w * 0.45 or (bot - top) > h * 0.25 or right < w * 0.80:
            return None
        return (left, top, min(w, right + 4), min(h, bot + 4))

    assets = []
    for r in reps:
        im = Image.open(r["path"]).convert("RGB")
        gray = np.asarray(im.convert("L"))
        a = np.asarray(im).astype(np.int16)
        tb = find_toolbar(gray)
        if tb:
            x0, y0, x1, y1 = tb
            reg = a[y0:y1, x0:x1]
            if reg.size:
                mx, mn = reg.max(axis=2), reg.min(axis=2)
                sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1), 0)
                if int((sat > 0.35).sum()) >= 40:
                    src_x = max(0, x0 - 80)
                    patch = a[y0:y1, src_x:x0]
                    if patch.shape[1] > 0:
                        a[y0:y1, x0:x1] = np.repeat(patch[:, -1:, :], x1 - x0, axis=1)
        # 尺寸标尺抹除
        if a[0:int(gray.shape[0] * 0.05), 0:int(gray.shape[1] * 0.28)].size and \
           (a[0:int(gray.shape[0] * 0.05), 0:int(gray.shape[1] * 0.28)].max(axis=2) > 225).mean() > 0.5:
            h0, h1 = int(gray.shape[0] * 0.05), int(gray.shape[0] * 0.095)
            w1 = int(gray.shape[1] * 0.26)
            a[0:int(gray.shape[0] * 0.045), 0:w1] = a[h0:h1, 0:w1]
        im2 = Image.fromarray(a.astype(np.uint8))
        g2 = np.asarray(im2.convert("L"))
        rm = g2.mean(axis=1)
        dark = np.where(rm < 90)[0]
        blocks = []
        st = None
        for i, d in enumerate(dark):
            if d and st is None:
                st = i
            elif not d and st is not None:
                if i - st > 40:
                    blocks.append((st, i))
                st = None
        if st is not None and len(dark) - st > 40:
            blocks.append((st, len(dark)))
        # 选主体：图表块优先，否则讲义页区
        src = None
        if blocks:
            big = max(blocks, key=lambda x: x[1] - x[0])
            if big[1] - big[0] >= 300:
                src = im2.crop((0, big[0], im2.width, big[1]))
            else:
                src = im2.crop((0, 0, im2.width, blocks[0][0]))
        else:
            src = im2
        if src.height < 120 or src.width < 120:
            src = im2
        key = f"V{r['idx']:03d}"
        out = os.path.join(clean_dir, f"{key}.jpg")
        src.save(out, quality=92)
        assets.append({"key": key, "file": out, "time_sec": r["t"],
                       "note": style_note or f"原视频 {int(r['t']//60):02d}:{int(r['t']%60):02d} 处画面",
                       "chart_h": chart_h(out)})
    log(f"  画面加工完成：{len(assets)} 张")
    return assets


# ================================================================ 画面加工（可复用）
def _find_toolbar(gray):
    """找白板/录屏工具栏（调色板/工具条）区域，返回 (x0,y0,x1,y1) 或 None。"""
    import numpy as np
    h, w = gray.shape
    x0, y0 = int(w * 0.55), int(h * 0.78)
    reg = gray[y0:, x0:].astype(np.float32)
    if reg.size == 0:
        return None
    rv = np.abs(np.diff(reg, axis=0)).mean(axis=1)
    hr = np.where(rv > 6)[0]
    if len(hr) == 0:
        return None
    top, bot = y0 + int(hr.min()), y0 + int(hr.max()) + 2
    strip = gray[top:bot, x0:].astype(np.float32)
    cv = np.abs(np.diff(strip, axis=1)).mean(axis=0)
    hc = np.where(cv > 6)[0]
    if len(hc) == 0:
        return None
    left, right = x0 + int(hc.min()), x0 + int(hc.max()) + 2
    if (right - left) > w * 0.45 or (bot - top) > h * 0.25 or right < w * 0.80:
        return None
    return (left, top, min(w, right + 4), min(w, bot + 4))


def clean_course_frame(src, dst):
    """清理一帧录屏画面，让它能直接进课件：

    1) 抹白板工具栏/调色板（右下）
    2) 抹左上角的录制软件标尺（如 "1440×900"）—— 用区域**中位色**填充，
       不用"复制下方像素"（那会把水印复制一份，出现两层重复水印）
    3) 裁掉右侧白边与右侧滚动条
    4) 裁掉底部下一张图的边缘（裁到主体内容块为止）
    失败时保留原图（不静默丢帧）。
    """
    import numpy as np
    from PIL import Image
    try:
        im = Image.open(src).convert("RGB")
        gray = np.asarray(im.convert("L"))
        a = np.asarray(im).astype(np.int16)
        h, w = gray.shape

        # 1) 工具栏/调色板
        tb = _find_toolbar(gray)
        if tb:
            x0, y0, x1, y1 = tb
            reg = a[y0:y1, x0:x1]
            if reg.size:
                mx, mn = reg.max(axis=2), reg.min(axis=2)
                sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1), 0)
                if int((sat > 0.35).sum()) >= 40:
                    src_x = max(0, x0 - 80)
                    patch = a[y0:y1, src_x:x0]
                    if patch.shape[1] > 0:
                        a[y0:y1, x0:x1] = np.repeat(patch[:, -1:, :], x1 - x0, axis=1)

        # 2) 左上角标尺 + 水印 → 中位色填充（不复制下方像素，避免水印重复）
        ry, rx = int(h * 0.05), int(w * 0.28)
        if ry > 4 and rx > 4:
            corner = a[0:ry, 0:rx]
            if corner.size and (corner.max(axis=2) > 225).mean() > 0.5:
                med = np.median(corner.reshape(-1, 3), axis=0).astype(np.int16)
                a[0:ry, 0:rx] = med

        # 3) 裁到**内容区**
        #    水平：按局部对比度(细节)找内容列 → 去掉左右均匀界面底色/黑边/滚动条
        #    垂直：先定主体暗块(深色画布的图表)，再向下吸收其后的文字行(标题/说明)，
        #          遇到下一块图表就停 → 去掉底部露出的下一张图
        from PIL import ImageFilter
        grayl = im.convert("L")
        blurred = np.asarray(grayl.filter(ImageFilter.BoxBlur(9)), dtype=np.int16)
        detail = np.abs(np.asarray(grayl, dtype=np.int16) - blurred)
        dmask = detail > 14
        col_d = dmask.sum(axis=0)
        cols_ok = np.where(col_d > h * 0.012)[0]
        if len(cols_ok) > 20:
            c0 = max(0, int(cols_ok[0]) - 6)
            c1 = min(w, int(cols_ok[-1]) + 7)
            if (c1 - c0) > w * 0.25:
                a = a[:, c0:c1]
        # 垂直：主体暗块
        cw = max(1, a.shape[1])
        dark_frac = (np.asarray(grayl, dtype=np.int16) < 90).sum(axis=1) / cw
        blocks, st = [], None
        for i, f in enumerate(dark_frac):
            if f > 0.35 and st is None:
                st = i
            elif f <= 0.35 and st is not None:
                blocks.append((st, i))
                st = None
        if st is not None:
            blocks.append((st, len(dark_frac)))
        blocks = [b for b in blocks if (b[1] - b[0]) > h * 0.15]
        if blocks:
            bs, be = max(blocks, key=lambda x: x[1] - x[0])
            # 向下吸收文字行（浅底 + 少量墨迹），最多 20% 高度
            limit = min(h, be + int(h * 0.20))
            y = be
            while y < limit:
                if dark_frac[y] > 0.35:
                    break
                y += 1
            if y > be and (y - be) > h * 0.02:
                be = y
            if be < a.shape[0] - 2 and be > h * 0.3:
                a = a[:be, :]

        if a.shape[0] < 120 or a.shape[1] < 120:
            raise ValueError("裁剪后画面过小，放弃清理")
        Image.fromarray(a.astype(np.uint8)).save(dst, quality=92)
        return True
    except Exception:
        try:
            import shutil
            shutil.copy2(src, dst)
        except Exception:
            pass
        return False


# ================================================================ 稿件组装
def assemble_script_md(title, meta_rows, segments, assets_by_key,
                       tech_notes=None, style_name="", source="", extra=""):
    """把分段文本 + 画面组装成讲课稿 Markdown。

    图片用**相对路径**（相对于稿件所在目录），保证整个目录拷走仍能正常显示。
    """
    L = []
    A = L.append
    A(f"# {title}")
    A("")
    A("> " + meta_rows)
    A("")
    if extra:
        A(extra)
        A("")
    for seg in segments:
        A(f"## {seg['title']}")
        A("")
        if seg.get("budget"):
            A(f"*{seg['budget']}*")
            A("")
        for k in seg.get("images", []):
            if k in assets_by_key:
                a = assets_by_key[k]
                A(f"![{k}]({a.get('rel_path') or a['file']})")
                A("")
        A(seg["text"].strip())
        A("")
    if tech_notes:
        A("---")
        A("")
        A("## 附 · 三处技术说明（录前扫一眼，讲的时候按稿里的说法）")
        A("")
        A("| # | 原视频的问题 | 本稿采用的说法 | 依据 |")
        A("|---|---|---|---|")
        for n in tech_notes:
            A(f"| {n['no']} | {n['problem']} | {n['fix']} | {n['basis']} |")
        A("")
    if assets_by_key:
        A("---")
        A("")
        A("## 附 · 画面切换时刻表")
        A("")
        A("| 讲到 | 画面文件 |")
        A("|---|---|")
        for seg in segments:
            for k in seg.get("images", []):
                if k in assets_by_key:
                    A(f"| {seg['title']} | `{os.path.basename(assets_by_key[k]['file'])}` |")
        A("")
    return "\n".join(L)


def load_quality_module():
    """加载 scripts/check_script_quality.py（口径单一来源）。"""
    import importlib.util
    qp = os.path.join(ROOT, "scripts", "check_script_quality.py")
    spec = importlib.util.spec_from_file_location("q", qp)
    q = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(q)
    return q


def self_check(cfg, md_text):
    """稿子自检（复用口径：时长/类比/提问/行动指令/未解释术语/图片）。"""
    import importlib.util

    qp = os.path.join(cfg["outputs_dir"], "..", "scripts", "check_script_quality.py")
    qp = os.path.normpath(qp)
    if not os.path.exists(qp):
        qp = os.path.join(ROOT, "scripts", "check_script_quality.py")
    spec = importlib.util.spec_from_file_location("q", qp)
    q = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(q)

    # 正文范围：排除附录（各种附录标题形态都要切掉）
    # ★ 自检/闸门/复审必须用**同一个**切法，否则"未解释术语"数会差 1
    import re as _re
    main_body = md_text
    m = _re.search(r"^##\s*附", main_body, _re.M)
    if m:
        main_body = main_body[:m.start()]
    body = q.body_of(main_body)
    cn = len(CN.findall(body))
    dur = cn / cfg["script_speed_cpm"] * 60
    counts = {
        "action_instructions": q.count_action_instructions(body),
        "check_questions": sum(body.count(k) for k in q.CHECK_Q),
        "analogy_marks": sum(1 for k in q.ANALOGY_SEG if k in body),
    }
    counts["body_cn_chars"] = cn
    counts["body_duration_sec"] = round(dur, 1)
    counts["body_duration_min"] = round(dur / 60, 1)
    # 未当场解释的术语（费曼核心红线，= 0）—— 与自检/复审同一口径（单一来源）
    try:
        unexp = q.find_unexplained_terms(body, q.split_sections(main_body))
        counts["unexplained_terms"] = len(unexp)
        counts["unexplained_detail"] = [u["term"] for u in unexp]
    except Exception:
        counts["unexplained_terms"] = 0
    return counts
