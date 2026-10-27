#!/usr/bin/env python3
"""集成验证：Link A 的「张雪峰逐字稿 + 内嵌图片」是否真正生效。

不下载 B 站视频，直接用本地 30s 测试视频跑真实生产函数链：
  extract_audio -> run_asr -> build_visual_assets -> build_prompt(frame_manifest)
  -> call_llm_stream(真实小米) -> extract_markdown -> ensure_frames_in_script
最后断言：课件 md 含 frames_clean/ 图片、且不含 承诺指标/三处技术说明/画面切换时刻表。
日志固定写到项目内 scripts/_logs/，不进 /tmp。
"""
import os
import sys
import json

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


sys.path.insert(0, ROOT)
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

from webapp.core import config, pipeline_ops as ops, styles, runner

LOGDIR = os.path.join(ROOT, "scripts", "_logs")
os.makedirs(LOGDIR, exist_ok=True)
LOG = os.path.join(LOGDIR, "test_linka_images.log")

def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(m + "\n")

cfg = config.load()
video = os.path.join(ROOT, "work", "short_test.mp4")
assert os.path.exists(video), video

wd = os.path.join(ROOT, "work", "_linka_img_test")
os.makedirs(wd, exist_ok=True)
wav = os.path.join(wd, "audio.wav")
asr_txt = os.path.join(wd, "asr.txt")

log("== 1 extract_audio ==")
ops.extract_audio(cfg, video, wav, log=log)
dur = ops.ffprobe_duration(cfg, video)
log(f"dur={dur:.1f}s")

log("== 2 ASR ==")
ops.run_asr(cfg, wav, asr_txt, log=log, workdir=wd)
segs = ops.parse_asr_file(asr_txt)
log(f"segs={len(segs)}")

log("== 3 visual assets ==")
assets = ops.build_visual_assets(cfg, video, wd, log=log)
# 把帧落进 frames_clean 子目录并给 rel_path（与生产一致）
fc_dir = os.path.join(wd, "frames_clean")
os.makedirs(fc_dir, exist_ok=True)
for a in assets:
    dst = os.path.join(fc_dir, os.path.basename(a["file"]))
    if not os.path.exists(dst):
        import shutil
        shutil.copy2(a["file"], dst)
    a["rel_path"] = f"frames_clean/{os.path.basename(a['file'])}"
log(f"assets={len(assets)}")

log("== 4 build_prompt(frame_manifest) ==")
source_text = ops.asr_to_text(segs)
frame_manifest = [{"key": a["key"], "rel_path": a["rel_path"],
                   "time_sec": a["time_sec"], "note": a.get("note", "")}
                  for a in assets if a.get("rel_path")]
system, user, _ = styles.build_prompt(
    "zhangxuefeng", 3, source_text, "",
    cfg["script_target_min"], cfg["script_target_max"], cfg["script_speed_cpm"],
    frame_manifest=frame_manifest)

log("== 5 call_llm_stream (真实小米) ==")
styled = ops.call_llm_stream(cfg, system, user, log=log)
assert styled is not None, "小米 LLM 返回 None（双通道均失败）"
styled = runner.extract_markdown(styled)

log("== 6 ensure_frames_in_script ==")
styled = runner.ensure_frames_in_script(styled, frame_manifest)

out_md = os.path.join(wd, "讲课稿.md")
with open(out_md, "w", encoding="utf-8") as f:
    f.write(styled)

# ── 断言
imgs = styled.count("frames_clean/")
bad = [k for k in ("承诺指标", "三处技术说明", "画面切换时刻表", "元信息")]
found_bad = [k for k in bad if k in styled]
log(f"imgs_embedded={imgs}  forbidden_tables_found={found_bad}")
log("PREVIEW:\n" + styled[:800])

print(json.dumps({
    "out_md": out_md,
    "images_embedded": imgs,
    "total_assets": len(assets),
    "forbidden_tables_found": found_bad,
}, ensure_ascii=False))
assert imgs >= 1, f"课件没有嵌入任何图片！embedded={imgs}"
assert not found_bad, f"课件仍含冗余表：{found_bad}"
print("INTEGRATION_OK")
