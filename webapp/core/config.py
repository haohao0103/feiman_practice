#!/usr/bin/env python3
"""系统配置 —— 所有可调参数集中在此。

优先级：环境变量 > webapp/config.json > 本文件默认值
"""
import json
import os
import shutil
import sys

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


WEB = os.path.join(ROOT, "webapp")
CONFIG_JSON = os.path.join(WEB, "config.json")


# 解释器能力校验缓存：{(interpreter, module): bool}
_MODULE_CACHE = {}


def _tool(env_name, *candidates, prefer="path", require_module=None):
    """外部工具定位。

    prefer="path"   → 环境变量 > PATH > 已知安装位置（ffmpeg / yt-dlp 这类独立工具）
    prefer="known"  → 环境变量 > 已知安装位置 > PATH（**解释器必须这样**）

    ★ 为什么解释器不能 PATH 优先：PATH 里的 python3 未必装了项目需要的包。
      实测踩过：PATH 的 python3 没有 faster_whisper，而 ASR 用它跑 → 直接 rc=1 失败。
      所以解释器一律"已知安装位置优先"，并且用 require_module 做能力校验，
      选一个真能 import 到目标模块的。

    require_module：给定则只接受能 import 该模块的解释器。
    """
    v = os.environ.get(env_name)
    if v and (not require_module or _has_module(v, require_module)):
        return v
    cands = list(candidates)
    if prefer == "path":
        found = [shutil.which(c) for c in cands]
        cands = [p for p in found if p] + [c for c in cands if not os.path.isabs(c)]
    for c in cands:
        if not c:
            continue
        p = c if os.path.isabs(c) else (shutil.which(c) or c)
        if not os.path.exists(p):
            continue
        if require_module and not _has_module(p, require_module):
            continue
        return p
    # 都没命中：退回第一个可用候选（报错时提示用）
    for c in cands:
        if c and os.path.exists(c):
            return c
    return cands[0] if cands else ""


def _has_module(interpreter, module):
    """解释器能否 import 某个模块（带缓存，避免每次都起进程）。"""
    if not interpreter or not os.path.exists(interpreter):
        return False
    key = (interpreter, module)
    if key in _MODULE_CACHE:
        return _MODULE_CACHE[key]
    try:
        import subprocess
        r = subprocess.run([interpreter, "-c", f"import {module}"],
                           capture_output=True, timeout=60)
        ok = (r.returncode == 0)
    except Exception:
        ok = False
    _MODULE_CACHE[key] = ok
    return ok


DEFAULTS = {
    # 路径
    "work_dir": os.path.join(ROOT, "work"),
    "uploads_dir": os.path.join(ROOT, "work", "uploads"),
    "projects_dir": os.path.join(ROOT, "work", "projects"),
    "outputs_dir": os.path.join(ROOT, "outputs"),
    "logs_dir": os.path.join(ROOT, "logs"),
    "models_dir": os.path.join(ROOT, "work", "models"),

    # 工具链（可被环境变量覆盖：FEIMAN_FFMPEG / FEIMAN_YTDLP / FEIMAN_PYTHON_ASR / FEIMAN_PYTHON）
    "ffmpeg": _tool("FEIMAN_FFMPEG", "ffmpeg",
                    "/Users/mac/.workbuddy/binaries/python/versions/3.14.3/lib/"
                    "python3.14/site-packages/imageio_ffmpeg/binaries/ffmpeg-macos-x86_64-v7.1"),
    "yt_dlp": _tool("FEIMAN_YTDLP", "yt-dlp",
                    "/Users/mac/.workbuddy/binaries/python/envs/default/bin/yt-dlp"),
    # ★ 解释器：已知安装位置优先，且必须真的能 import faster_whisper
    #   （PATH 的 python3 通常没装这个包，用它跑 ASR 会直接 rc=1）
    "python_asr": _tool("FEIMAN_PYTHON_ASR",
                        "/Users/mac/.workbuddy/binaries/python/envs/default/bin/python3",
                        "python3", prefer="known", require_module="faster_whisper"),
    "python_main": _tool("FEIMAN_PYTHON",
                         "/Users/mac/.workbuddy/binaries/python/envs/default/bin/python3",
                         sys.executable, prefer="known", require_module="numpy"),

    # ASR（本地离线）
    "asr_model": "small",          # tiny/base/small/medium/large-v3
    "asr_device": "cpu",
    "asr_compute_type": "int8",
    "asr_beam_size": 1,
    "hf_endpoint": "https://hf-mirror.com",

    # work/ 暂存区清理（见 webapp/core/cleanup.py）
    # 上传的录屏（~760M）+ 抽帧（~1100 张/任务）会持续堆积，实测三天到 6.5G。
    "cleanup": {
        "enabled": True,
        "keep_days": 7,         # 任务目录里的大件保留天数（超龄才删）
        "max_work_gb": 8,       # work/ 总量水位线（超了从最老任务整目录删）
        "interval_hours": 6,    # 自动触发间隔（服务启动 + 任务收尾时检查）
    },

    # LLM（OpenAI 兼容协议；小米 MiMo / DeepSeek / OpenAI 等通用）
    # 唯一规则：有小米（双通道 ch1/ch2 任一可达）→ 用小米；无小米 → 直接报错，绝不静默降级。
    "llm_base_url": os.environ.get("LLM_BASE_URL", ""),
    "llm_api_key": os.environ.get("LLM_API_KEY", ""),
    "llm_model": os.environ.get("LLM_MODEL", "mimo-v2.6-pro"),
    # 备用通道（例如主通道是 Token Plan、备用是按量付费）
    "llm2_base_url": os.environ.get("LLM2_BASE_URL", ""),
    "llm2_api_key": os.environ.get("LLM2_API_KEY", ""),
    "llm2_model": os.environ.get("LLM2_MODEL", "mimo-v2.6-pro"),
    "llm_timeout": 180,
    "llm_max_tokens": 8000,
    # 讲课稿一次要写完整篇（≈5600~10000 汉字 ≈ 8000~14000 token），
    # 用通用 8000 会被**截断**（实测：稿子在"——好，"处断掉，尾部收尾段/动作清单全丢，
    # 而流水线照样出稿、不报错）。所以成稿调用单独给大预算。
    "script_max_tokens": 20000,
    "script_max_tokens_retry": 32000,
    "llm_temperature": 0.7,

    # 离线引擎（ch3 本地确定性引擎）。默认 False = 关闭。
    # 关闭时：无小米 → 直接报错（唯一规则）。
    # 显式设为 True 时：无小米才退化为 ch3 本地引擎（离线、无需 key）。
    # 注意：这不是默认行为，默认就是"无小米就报错"。
    "offline_engine": False,

    # 讲课稿规格
    # ★ 时长口径 = **单期**：单期口播 5~8 分钟。
    #   2026-10-24 用户定调是"单期 8-10 分钟"，但那是愿景值——原始第一期稿实测
    #   1296 字 = 5.4 分钟/期，从未达到 8 分钟。2026-10-27 对齐到实际产出（5~8 分钟/期），
    #   与原始标杆同一量级；整篇课件时长 = 期数 × 单期时长。
    "script_target_min": 5,         # 单期目标下限（分钟）
    "script_target_max": 8,         # 单期目标上限（分钟）
    "script_speed_cpm": 240,         # 语速基准 字/分
    "style_preset": "zhangxuefeng",  # 风格预设
    "style_intensity": 3,            # 1 轻度 / 2 中度 / 3 重度

    # 抽帧自适应 —— 画面张数由内容涌现，**不设固定张数**。
    # 规则：①同一屏画面只留一张（视觉距离 < 阈值即判同屏，保留墨迹量最高的那张）
    #      ②每期至少 1 张（期级覆盖，防课件稀疏）
    #      ③不设总数上限 —— 上限天然等于段数
    "frame_dedupe_th": 0.03,         # 同屏判定阈值。由 scripts/analyze_frame_diversity.py 实测标定：
                                     # 噪声地板 0.022（光标/标注造成的微差）之上、下一档 0.035 之下
    "frame_min_per_part": 1,        # 每期保底张数
    "max_diagrams": 8,               # 自制图安全上限（仅防 LLM 过度标记，非目标值）
    "max_diagrams_doc": 15,          # 文档来源的安全上限（文档无原画面，图全靠自制）
    "doc_formats": [".md", ".markdown", ".txt", ".text", ".docx", ".doc",
                    ".rtf", ".html", ".htm", ".pdf"],

    # 画面
    "frame_interval_sec": 10,
    "frame_width": 1400,
    "max_video_seconds": 3600,       # 上限 1 小时
    "max_upload_mb": 2048,            # 上限 2GB（录屏文件普遍 1GB+）
}


def load():
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG_JSON):
        try:
            with open(CONFIG_JSON, encoding="utf-8") as fh:
                cfg.update(json.load(fh))
        except Exception:
            pass
    # 环境变量覆盖
    for k in ("llm_base_url", "llm_api_key", "llm_model",
              "llm2_base_url", "llm2_api_key", "llm2_model", "asr_model",
              "style_preset", "style_intensity", "script_target_min",
              "script_target_max", "script_speed_cpm"):
        env_key = f"FEM_{k.upper()}"
        if os.environ.get(env_key):
            v = os.environ[env_key]
            cfg[k] = int(v) if k in ("style_intensity", "script_target_min",
                                     "script_target_max", "script_speed_cpm") else v
    # 嵌套段（cleanup）做浅合并，避免 config.json 只写一个键就把默认全冲掉
    for _sec in ("cleanup",):
        merged = dict(DEFAULTS.get(_sec, {}))
        merged.update(cfg.get(_sec) or {})
        cfg[_sec] = merged
    for d in (cfg["work_dir"], cfg["uploads_dir"], cfg["projects_dir"],
              cfg["outputs_dir"], cfg["logs_dir"], cfg["models_dir"]):
        os.makedirs(d, exist_ok=True)
    return cfg


def llm_ready(cfg):
    """任一通道可用即视为已配置 LLM。"""
    ch1 = bool(cfg.get("llm_api_key") and cfg.get("llm_base_url"))
    ch2 = bool(cfg.get("llm2_api_key") and cfg.get("llm2_base_url"))
    return ch1 or ch2


def llm_channels(cfg):
    """返回已配置的通道路列表 [(base_url, api_key, model, label), ...]。"""
    chs = []
    if cfg.get("llm_api_key") and cfg.get("llm_base_url"):
        chs.append((cfg["llm_base_url"].rstrip("/"),
                    cfg["llm_api_key"], cfg.get("llm_model", "mimo-v2.6-pro"), "ch1"))
    if cfg.get("llm2_api_key") and cfg.get("llm2_base_url"):
        chs.append((cfg["llm2_base_url"].rstrip("/"),
                    cfg["llm2_api_key"], cfg.get("llm2_model", "mimo-v2.6-pro"), "ch2"))
    return chs
