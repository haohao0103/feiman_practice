#!/usr/bin/env python3
"""真实 Web 全链路端到端测试：通过 HTTP API 跑完整条 Link A（下载→合并→ASR→强流水线→PDF）。

不依赖任何内部函数，只走 /api/video-to-script + /api/task/{tid}，
完全复刻浏览器前端的调用方式。最后对产物做硬校验并给出 PASS/FAIL。
"""
import json
import os
import sys
import time
import urllib.request

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


BASE = "http://127.0.0.1:8848"
URL = "https://www.bilibili.com/video/BV1rXXfBiECa/"
LOG = os.path.join(ROOT, "work/web_e2e.log")


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%H:%M:%S')}] {m}\n")
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def post(path, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    open(LOG, "w", encoding="utf-8").close()
    log("=== 真实 Web 全链路测试启动 ===")
    log(f"推送 /api/video-to-script  url={URL}")
    j = post("/api/video-to-script",
             {"url": URL, "style": "zhangxuefeng", "intensity": 3, "notes": ""})
    tid = j.get("task_id")
    if not tid:
        log(f"FAIL: 未返回 task_id -> {j}")
        return
    log(f"task_id={tid}")

    last = ""
    for _ in range(240):  # 最多 ~20 分钟
        t = get(f"/api/task/{tid}")
        st = t.get("status")
        prog = t.get("progress")
        label = t.get("stage_label")
        line = f"status={st} progress={prog} stage={label}"
        if line != last:
            log(line)
            last = line
        if st == "done":
            log("=== 任务完成，开始硬校验 ===")
            break
        if st == "failed":
            log(f"FAIL: 任务失败 -> {t.get('error')}")
            return
        time.sleep(5)
    else:
        log("FAIL: 超过 20 分钟仍未完成")
        return

    res = t.get("result", {})
    # 硬校验
    problems = []
    sp = res.get("script_path")
    pp = res.get("pdf_path")
    if not sp or not os.path.exists(sp):
        problems.append(f"讲课稿MD缺失: {sp}")
    if not pp or not os.path.exists(pp):
        problems.append(f"课件PDF缺失: {pp}")
    assets = res.get("assets") or []
    frames = [a for a in assets if "frames_clean" in (a.get("rel_path") or "")]
    diags = [a for a in assets if "diagrams" in (a.get("rel_path") or "")]
    # 校验每个资源文件真实存在
    for a in assets:
        if not os.path.exists(a.get("file", "")):
            problems.append(f"资源文件不存在: {a.get('file')}")
    # PDF 硬校验
    if pp and os.path.exists(pp):
        raw = open(pp, "rb").read()
        if b"%PDF" not in raw[:8] or b"%%EOF" not in raw[-1024:]:
            problems.append("PDF 头/尾非法")
        img = raw.count(b"/Subtype /Image")
        log(f"PDF 内嵌图片 XObject 数: {img}")
        if img < 1:
            problems.append("PDF 无内嵌图片")
    log(f"成稿字数: {res.get('script_chars')}")
    log(f"原讲义图: {len(frames)}  自制图: {len(diags)}  总画面: {res.get('images')}")
    log(f"纠错条数: {len(res.get('corrections') or [])}")
    log(f"自检: {res.get('checks')}")
    log(f"llm_note: {res.get('llm_note')}")

    if problems:
        log("=== FAIL ===")
        for p in problems:
            log("  - " + p)
    else:
        log("=== PASS：真实 Web 全链路产出标杆级课件，所有硬校验通过 ===")
        log(f"产物 MD: {sp}")
        log(f"产物 PDF: {pp}")


if __name__ == "__main__":
    main()
