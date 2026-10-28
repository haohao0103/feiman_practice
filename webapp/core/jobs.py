#!/usr/bin/env python3
"""任务引擎 —— 后台线程执行 + 状态持久化 + 实时进度。

设计要点：
  1. 每个任务一个独立工作目录，产物互不干扰
  2. 状态写磁盘 JSON（进程重启也能看到历史）
  3. 进度通过轮询 status 接口读取（前端每 1.5s 拉一次）
  4. 阶段失败显式记录，不静默吞掉
"""
import json
import os
import shutil
import threading
import time
import traceback
import uuid
from datetime import datetime

from . import config

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


_LOCK = threading.Lock()
_TASKS = {}          # task_id -> task dict（内存镜像）
_STAGE_ORDER = {
    "video_to_script": [
        ("download", "下载视频"),
        ("extract_audio", "抽取音轨"),
        ("asr", "语音转写"),
        ("segment_outline", "自动分段大纲"),
        ("extract_frames", "抽取关键画面"),
        ("gen_diagram", "生成自制图"),
        ("build_script", "生成讲课稿"),
        ("fact_check", "核查纠错"),
        ("assemble", "组装与PDF"),
        ("self_check", "稿子自检"),
    ],
    "video_review": [
        ("upload", "接收视频"),
        ("extract_audio", "抽取音轨"),
        ("asr", "语音转写"),
        ("analyze", "演讲指标分析"),
        ("visual", "画面构成分析"),
        ("baseline", "与课件逐段对账"),
        ("improve", "生成改进稿"),
        ("self_check", "稿子自检"),
    ],
    "doc_to_courseware": [
        ("upload", "接收文档"),
        ("doc_read", "读取文档"),
        ("segment_outline", "文档分段大纲"),
        ("gen_diagram", "生成自制图"),
        ("build_script", "生成讲课稿"),
        ("fact_check", "核查纠错"),
        ("assemble", "组装与PDF"),
        ("self_check", "稿子自检"),
    ],
}

# 阶段耗时权重：LLM 改写(style_rewrite/improve)与 ASR 是耗时大头，
# 给更大权重 → 进度条在这两个阶段内移动范围更大，不会"卡在 75% 不动"。
_STAGE_WEIGHTS = {
    "video_to_script": {"download": 3, "extract_audio": 1, "asr": 4, "segment_outline": 2,
                        "extract_frames": 1, "gen_diagram": 3, "build_script": 4,
                        "fact_check": 3, "assemble": 2, "self_check": 1},
    "video_review": {"upload": 1, "extract_audio": 1, "asr": 4, "analyze": 1, "visual": 1,
                     "baseline": 3, "improve": 5, "self_check": 1},
    "doc_to_courseware": {"upload": 1, "doc_read": 1, "segment_outline": 3,
                           "gen_diagram": 2, "build_script": 4, "fact_check": 3,
                           "assemble": 2, "self_check": 1},
}


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _task_dir(cfg, task_id):
    d = os.path.join(cfg["projects_dir"], task_id)
    os.makedirs(d, exist_ok=True)
    return d


def _write_state(cfg, task):
    p = os.path.join(cfg["projects_dir"], f"{task['id']}.json")
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(task, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def sanitize(obj):
    """清洗控制字符。

    ASR 转写里可能含 \\x0b、\\x00 等裸控制字符，直接 json.dumps 会生成
    非法 JSON，浏览器 JSON.parse 会失败。这里全局剔除。
    """
    if isinstance(obj, str):
        return "".join(c for c in obj
                       if c in "\n\r\t" or (ord(c) >= 32 and ord(c) != 127))
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize(v) for v in obj]
    return obj


def _load_states(cfg):
    out = []
    if not os.path.isdir(cfg["projects_dir"]):
        return out
    for f in os.listdir(cfg["projects_dir"]):
        if not f.endswith(".json"):
            continue
        try:
            with open(os.path.join(cfg["projects_dir"], f), encoding="utf-8") as fh:
                out.append(json.load(fh))
        except Exception:
            continue
    out.sort(key=lambda t: t.get("created_at", ""), reverse=True)
    return out


def create_task(cfg, kind, params):
    """建任务并起后台线程。"""
    with _LOCK:
        tid = f"{kind}_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}"
        task = {
            "id": tid,
            "kind": kind,
            "params": params,
            "status": "pending",
            "stage": None,
            "stage_label": "排队中",
            "progress": 0.0,
            "created_at": _now(),
            "finished_at": None,
            "dir": _task_dir(cfg, tid),
            "logs": [],
            "result": {},
            "error": None,
        }
        _TASKS[tid] = task
        _write_state(cfg, task)

    th = threading.Thread(target=_run, args=(cfg, tid), daemon=True)
    th.start()
    return task


def get_task(cfg, tid):
    with _LOCK:
        if tid in _TASKS:
            return _TASKS[tid]
    p = os.path.join(cfg["projects_dir"], f"{tid}.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    return None


def list_tasks(cfg):
    with _LOCK:
        live = list(_TASKS.values())
    disk = _load_states(cfg)
    seen = set()
    out = []
    for t in live + disk:
        if t["id"] in seen:
            continue
        seen.add(t["id"])
        out.append(t)
    out.sort(key=lambda t: t.get("created_at", ""), reverse=True)
    return out


def _set(cfg, tid, **kw):
    with _LOCK:
        t = _TASKS.get(tid)
        if t is None:
            return
        t.update(kw)
        t = sanitize(t)
        _TASKS[tid] = t
        _write_state(cfg, t)


def _log(cfg, tid, msg):
    with _LOCK:
        t = _TASKS.get(tid)
        if t is None:
            return
        line = f"[{_now()}] {msg}"
        t["logs"].append(line)
        if len(t["logs"]) > 400:
            t["logs"] = t["logs"][-400:]
        t = sanitize(t)
        _TASKS[tid] = t
        _write_state(cfg, t)


def _run(cfg, tid):
    from . import runner
    try:
        _set(cfg, tid, status="running", stage_label="启动中")

        # runner 里的 log 是单参 log(msg)；
        # 这里的 _log 需要 (cfg, tid, msg)，所以做一次适配。
        def log(msg, _cfg=cfg, _tid=tid):
            _log(_cfg, _tid, msg)

        def setp(**kw):
            _set(cfg, tid, **kw)

        _RUNNERS = {
            "video_to_script": "run_video_to_script",
            "video_review": "run_video_review",
            "doc_to_courseware": "run_doc_to_courseware",
        }
        fn_name = _RUNNERS.get(_TASKS[tid]["kind"], "run_video_review")
        runner_fn = getattr(runner, fn_name)
        result = runner_fn(cfg, tid, setp, log)
        _set(cfg, tid, status="done", stage=None, stage_label="完成",
             progress=1.0, result=result or {}, finished_at=_now())
        _log(cfg, tid, "任务完成")
        # 任务收尾时顺手清理 work/ 暂存区（低频触发，见 webapp/core/cleanup.py）。
        # 放在这里而不是各 runner 里 —— 这是所有任务唯一的收尾单点。
        try:
            from . import cleanup as cl
            cl.protect([task_dir])
            try:
                st = cl.maybe_run(cfg, log=lambda m: _log(cfg, tid, m))
            finally:
                cl.unprotect([task_dir])   # 成对解除，否则这个目录永远清不掉
            if st:
                _log(cfg, tid, f"暂存区清理：释放 "
                               f"{st['freed_bytes']/1048576:.0f}MB，"
                               f"work/ 现 {st['total_bytes_after']/1073741824:.2f}GB")
        except Exception as e:
            _log(cfg, tid, f"暂存区清理跳过（不影响任务结果）：{e}")
    except Exception as e:
        tb = traceback.format_exc()
        _set(cfg, tid, status="failed", stage_label="失败", error=str(e),
             finished_at=_now())
        _log(cfg, tid, f"任务失败: {e}\n{tb[-1500:]}")


def stage_progress(cfg, tid, stage, label=None, frac=None):
    """更新阶段进度。frac: 0~1 表示该阶段内部进度。

    整体进度按阶段耗时权重分配（见 _STAGE_WEIGHTS），而非均分——
    LLM 改写与 ASR 是耗时大头，给更大权重，进度条在这两个阶段内才有足够
    的移动范围，不会"卡在某个百分比不动"。
    """
    kind = _TASKS.get(tid, {}).get("kind", "")
    order = _STAGE_ORDER.get(kind, [])
    weights = _STAGE_WEIGHTS.get(kind, {})
    with _LOCK:
        t = _TASKS.get(tid)
        if t is None:
            return
        try:
            idx = [s[0] for s in order].index(stage)
        except ValueError:
            idx = 0
        total_w = sum(weights.get(s[0], 1) for s in order) or 1
        done_w = sum(weights.get(order[i][0], 1) for i in range(idx))
        cur_w = weights.get(stage, 1)
        prog = (done_w + cur_w * (frac or 0.0)) / total_w
        t["stage"] = stage
        t["stage_label"] = label or stage
        t["progress"] = round(min(1.0, prog), 4)
        t = sanitize(t)
        _TASKS[tid] = t
        _write_state(cfg, t)
