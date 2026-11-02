import os, re, sys, time
ROOT="/Users/mac/WorkBuddy/fei_man_price"; sys.path.insert(0,ROOT)
from webapp.core import config as cfgmod, courseware_gen as cg
cfg=cfgmod.load()
asr=open(os.path.join(ROOT,"work/projects/video_to_script_20261103_002912_1810f9/asr.txt"),encoding="utf-8").read()
md=open(os.path.join(ROOT,"outputs/课件_video_to_script_20261103_005319_c4c05e/讲课稿.md"),encoding="utf-8").read()
m=re.search(r"^##\s*附一", md, re.M); script=md[:m.start()] if m else md
segs=cg._split_script_segments(script)
batch=segs[5:10]      # 用第 2 批（之前失败的那批）
body="\n\n".join(s[3] for s in batch)
user=(f"【原始转写】\n{asr}\n\n【本批要核查的段落】\n{body}\n\n只核查本批这几段的问题，输出 JSON 列表。")
for name, think in (("开思考", None), ("关思考", {"type":"disabled"})):
    t0=time.time()
    raw=cg.ops.call_llm(cfg, cg._FACT_SYS, user, log=lambda m: None,
                        max_tokens=24000, timeout=600, thinking=think)
    print(f"  {name}: {time.time()-t0:5.1f}s | 返回 {len(raw) if raw else 0} 字符 | "
          f"内容 {str(raw)[:70] if raw else '(空)'}")
