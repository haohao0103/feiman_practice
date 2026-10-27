#!/usr/bin/env python3
"""验证抽帧收尾：临时目录必须留在 work/ 暂存区、且跑完自动消失（不残留）。

背景：safe-delete 钩子会拦截「交付目录内单次批量删除 >50 文件」→ 后台进程挂死。
本测试确认 extract_segment_frames：
  1) 候选帧临时目录建在 cfg["work_dir"]（暂存区），**不在** 交付目录
  2) 跑完后临时目录被清掉，不留残留
  3) 成品帧在 frames_clean/，且文件真实存在
"""
import os
import sys

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


sys.path.insert(0, ROOT)
from webapp.core import config as cfgmod  # noqa: E402
from webapp.core import courseware_gen as cg  # noqa: E402

cfg = cfgmod.load()
VIDEO = os.path.join(ROOT, "work/bench_src_BV1rXXfBiECa/video.mp4")
OUT = os.path.join(ROOT, "work/_extract_cleanup_test")

if not os.path.exists(VIDEO):
    print("跳过：没有可用的缓存视频", VIDEO)
    sys.exit(0)

os.makedirs(OUT, exist_ok=True)
outline = {"title": "抽帧清理测试", "parts": [
    {"title": "第1期", "segments": [
        {"title": "段1", "start_sec": 30, "end_sec": 40, "frame_at_sec": 35,
         "summary": "测试段1"},
        {"title": "段2", "start_sec": 120, "end_sec": 130, "frame_at_sec": 125,
         "summary": "测试段2"},
    ]},
]}

before = {d for d in os.listdir(cfg["work_dir"]) if d.startswith("_frm_tmp_")}
man = cg.extract_segment_frames(cfg, VIDEO, outline, OUT, log=print)
after = {d for d in os.listdir(cfg["work_dir"]) if d.startswith("_frm_tmp_")}
new_tmp = sorted(after - before)

print("\n--- 校验")
print(f"  抽到帧: {len(man)} 张")
ok_files = all(os.path.exists(os.path.join(OUT, m["rel_path"])) for m in man)
print(f"  成品帧文件都存在: {ok_files}")
print(f"  暂存区新增的 _frm_tmp_*: {new_tmp}  (跑完应为空列表=已自动清理)")
print(f"  交付目录内有无 _frm_tmp: "
      f"{[d for d in os.listdir(OUT) if d.startswith('_frm_tmp')]}  (应为空)")
print(f"  交付目录内容: {sorted(os.listdir(OUT))}")
print("\n结论: " + ("通过" if (ok_files and not new_tmp) else "不通过"))

import shutil

shutil.rmtree(OUT, ignore_errors=True)
