#!/usr/bin/env python3
"""实测：文档 → 标杆级讲课逐字稿课件（指定风格）。

用法: python3 scripts/test_doc_courseware.py <文档路径> [风格] [强度]
默认文档 = outputs/价格行为学_反转的五个步骤_我的讲课稿.md
日志: work/doc_courseware.log
"""
import os
import sys
import time

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


sys.path.insert(0, ROOT)
from webapp.core import config as cfgmod          # noqa: E402
from webapp.core import doc_courseware as dc      # noqa: E402

LOG = os.path.join(ROOT, "work/doc_courseware.log")
cfg = cfgmod.load()

DOC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    ROOT, "outputs/价格行为学_反转的五个步骤_我的讲课稿.md")
STYLE = sys.argv[2] if len(sys.argv) > 2 else "faithful"
INTENSITY = int(sys.argv[3]) if len(sys.argv) > 3 else 3


def log(m):
    line = f"[{time.strftime('%H:%M:%S')}] {m}"
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


def main():
    open(LOG, "w", encoding="utf-8").close()
    log(f"=== 文档→课件测试 ===")
    log(f"文档: {DOC}")
    log(f"风格: {STYLE}  强度: {INTENSITY}")
    if not os.path.exists(DOC):
        log("FAIL: 文档不存在")
        return
    bvid = "doc_" + os.path.splitext(os.path.basename(DOC))[0][:20]
    out = os.path.join(ROOT, "outputs", f"课件_{bvid}")
    os.makedirs(out, exist_ok=True)
    res = dc.build_doc_courseware(cfg, DOC, out, style_name=STYLE, intensity=INTENSITY,
                                  log=log, workdir=os.path.join(ROOT, "work/doc_work"))
    log("=== 结果 ===")
    log(f"风格: {STYLE}")
    log(f"文档字符: {res['doc_chars']}  估算时长: {res['est_duration_min']} 分钟")
    log(f"成稿字数: {res['script_chars']}")
    log(f"文档原图: {len(res['frame_manifest'])} 张  自制图: {len(res['diagram_manifest'])} 张"
        f"  总画面: {res['total_images']}")
    log(f"纠错条数: {len(res['corrections'])}")
    log(f"自检: {res['checks']}")
    log(f"MD: {res['script_path']}")
    log(f"PDF: {res['pdf_path']}")
    if res["corrections"]:
        for c in res["corrections"]:
            log(f"  纠错: [{c.get('issue')}] {c.get('original','')[:40]}")


if __name__ == "__main__":
    main()
