#!/usr/bin/env python3
"""真实 Web 全链路测试 · 文档 → 课件（HTTP 往返，复刻浏览器行为）。

POST /api/doc-to-courseware（multipart 上传）→ 轮询 /api/task/{tid} → 硬校验 PASS/FAIL。
"""
import json
import os
import time
import urllib.request
import uuid

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


BASE = "http://127.0.0.1:8848"
DOC = os.path.join(ROOT, "outputs/价格行为学_反转的五个步骤_我的讲课稿.md")
LOG = os.path.join(ROOT, "work/doc_web_e2e.log")


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%H:%M:%S')}] {m}\n")
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def post_file(path, fields, file_path, file_field="file"):
    """multipart/form-data 上传（不依赖第三方库）。"""
    boundary = "----wb" + uuid.uuid4().hex
    body = b""
    for k, v in fields.items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n"
                 f"{v}\r\n").encode("utf-8")
    fname = os.path.basename(file_path)
    ctype = {"md": "text/markdown", "pdf": "application/pdf"}.get(
        fname.rsplit(".", 1)[-1], "application/octet-stream")
    with open(file_path, "rb") as f:
        data = f.read()
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; "
             f"filename=\"{fname}\"\r\nContent-Type: {ctype}\r\n\r\n").encode("utf-8")
    body += data + b"\r\n"
    body += f"--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(
        BASE + path, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    open(LOG, "w", encoding="utf-8").close()
    log("=== 真实 Web 全链路测试 · 文档 → 课件 ===")
    log(f"上传: {DOC}")
    j = post_file("/api/doc-to-courseware",
                  {"style": "faithful", "intensity": "3", "notes": ""}, DOC)
    tid = j.get("task_id")
    if not tid:
        log(f"FAIL: 未返回 task_id -> {j}")
        return
    log(f"task_id={tid}")
    last = ""
    for _ in range(240):
        t = get(f"/api/task/{tid}")
        line = f"status={t.get('status')} progress={t.get('progress')} stage={t.get('stage_label')}"
        if line != last:
            log(line)
            last = line
        if t.get("status") == "done":
            break
        if t.get("status") == "failed":
            log(f"FAIL: {t.get('error')}")
            return
        time.sleep(5)
    else:
        log("FAIL: 超时")
        return

    res = t.get("result", {})
    problems = []
    for key in ("script_path", "pdf_path"):
        p = res.get(key)
        if not p or not os.path.exists(p):
            problems.append(f"产物缺失 {key}: {p}")
    assets = res.get("assets") or []
    for a in assets:
        if not os.path.exists(a.get("file", "")):
            problems.append(f"资源不存在: {a.get('file')}")
    pp = res.get("pdf_path")
    if pp and os.path.exists(pp):
        raw = open(pp, "rb").read()
        if b"%PDF" not in raw[:8] or b"%%EOF" not in raw[-1024:]:
            problems.append("PDF 头尾非法")
        log(f"PDF 内嵌图 XObject: {raw.count(b'/Subtype /Image')}")
    log(f"成稿字数: {res.get('script_chars')}  画面: {res.get('images')}"
        f"  纠错: {len(res.get('corrections') or [])}")
    log(f"自检: {res.get('checks')}")
    if problems:
        log("=== FAIL ===")
        for p in problems:
            log("  - " + p)
    else:
        log("=== PASS：文档 → 课件 全链路通过 ===")
        log(f"MD: {res.get('script_path')}")
        log(f"PDF: {res.get('pdf_path')}")


if __name__ == "__main__":
    main()
