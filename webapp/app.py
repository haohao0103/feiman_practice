#!/usr/bin/env python3
"""FastAPI 应用 —— 讲课稿工作台。

启动：
  /Users/mac/.workbuddy/binaries/python/versions/3.14.3/bin/python3 \
      -m uvicorn webapp.app:app --host 127.0.0.1 --port 8848
"""
import json
import os
import shutil
import sys
import time

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


sys.path.insert(0, ROOT)

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from webapp.core import config as cfgmod
from webapp.core import jobs
from webapp.core import styles

WEB = os.path.join(ROOT, "webapp")
cfg = cfgmod.load()

app = FastAPI(title="讲课稿工作台", version="1.0")
app.mount("/static", StaticFiles(directory=os.path.join(WEB, "static")), name="static")


class ScriptReq(BaseModel):
    url: str
    style: str = "zhangxuefeng"
    intensity: int = 3
    notes: str = ""


class ReviewReq(BaseModel):
    style: str = "zhangxuefeng"
    intensity: int = 3
    notes: str = ""
    baseline_script: str = ""


@app.get("/", response_class=HTMLResponse)
def index():
    p = os.path.join(WEB, "templates", "index.html")
    with open(p, encoding="utf-8") as fh:
        return HTMLResponse(fh.read())


@app.get("/api/config")
def api_config():
    return {
        "styles": [{"key": k, "name": v["name"], "desc": v["desc"]}
                    for k, v in styles.PRESETS.items()],
        "llm_ready": cfgmod.llm_ready(cfg),
        "llm_model": cfg.get("llm_model"),
        "asr_model": cfg.get("asr_model"),
        "target_min": cfg["script_target_min"],
        "target_max": cfg["script_target_max"],
    }


# ---------------------------------------------------------------- 链路 A
@app.post("/api/video-to-script")
def api_video_to_script(req: ScriptReq):
    if req.style not in styles.PRESETS:
        raise HTTPException(400, f"未知风格: {req.style}")
    t = jobs.create_task(cfg, "video_to_script", req.model_dump())
    return {"task_id": t["id"]}


# ---------------------------------------------------------------- 链路 B
@app.post("/api/video-review")
async def api_video_review(
    file: UploadFile = File(...),
    style: str = Form("zhangxuefeng"),
    intensity: int = Form(3),
    notes: str = Form(""),
    baseline_script: str = Form(""),
):
    if not file.filename:
        raise HTTPException(400, "未收到文件")
    ext = os.path.splitext(file.filename)[1] or ".mp4"
    if ext.lower() not in (".mp4", ".mov", ".mkv", ".m4v", ".flv", ".webm", ".avi"):
        raise HTTPException(400, f"不支持的文件类型: {ext}")
    size_mb = 0
    tid = f"review_{time.strftime('%Y%m%d_%H%M%S')}_{os.getpid()}"
    up_dir = os.path.join(cfg["uploads_dir"], tid)
    os.makedirs(up_dir, exist_ok=True)
    dst = os.path.join(up_dir, f"upload{ext}")
    # 流式落盘：1GB 文件不进内存
    with open(dst, "wb") as fh:
        while True:
            chunk = await file.read(8 * 1024 * 1024)
            if not chunk:
                break
            size_mb += len(chunk) / 1e6
            if size_mb > cfg["max_upload_mb"]:
                fh.close()
                os.remove(dst)
                raise HTTPException(413, f"文件超过 {cfg['max_upload_mb']}MB 上限")
            fh.write(chunk)
    if size_mb < 0.01:
        os.remove(dst)
        raise HTTPException(400, "文件为空")
    t = jobs.create_task(cfg, "video_review", {
        "video_path": dst, "style": style, "intensity": int(intensity),
        "notes": notes, "baseline_script": baseline_script,
        "filename": file.filename, "size_mb": round(size_mb, 1)})
    return {"task_id": t["id"]}


# ---------------------------------------------------------------- 链路 C
@app.post("/api/doc-to-courseware")
async def api_doc_to_courseware(
    file: UploadFile = File(...),
    style: str = Form("faithful"),
    intensity: int = Form(3),
    notes: str = Form(""),
):
    """上传文档 → 标杆级讲课逐字稿课件（指定风格）。"""
    if not file.filename:
        raise HTTPException(400, "未收到文件")
    ext = os.path.splitext(file.filename)[1].lower()
    allowed = {".md", ".markdown", ".txt", ".text", ".docx", ".doc",
               ".rtf", ".html", ".htm", ".pdf"}
    if ext not in allowed:
        raise HTTPException(400, f"不支持的文档类型: {ext}（支持 md/txt/docx/doc/rtf/html/pdf）")
    size_mb = 0
    tid = f"doc_{time.strftime('%Y%m%d_%H%M%S')}_{os.getpid()}"
    up_dir = os.path.join(cfg["uploads_dir"], tid)
    os.makedirs(up_dir, exist_ok=True)
    dst = os.path.join(up_dir, f"doc{ext}")
    with open(dst, "wb") as fh:
        while True:
            chunk = await file.read(8 * 1024 * 1024)
            if not chunk:
                break
            size_mb += len(chunk) / 1e6
            if size_mb > cfg["max_upload_mb"]:
                fh.close()
                os.remove(dst)
                raise HTTPException(413, f"文件超过 {cfg['max_upload_mb']}MB 上限")
            fh.write(chunk)
    if size_mb < 0.005:
        os.remove(dst)
        raise HTTPException(400, "文件为空")
    t = jobs.create_task(cfg, "doc_to_courseware", {
        "doc_path": dst, "style": style, "intensity": int(intensity),
        "notes": notes, "filename": file.filename, "size_mb": round(size_mb, 2)})
    return {"task_id": t["id"]}


# ---------------------------------------------------------------- 练习追踪
@app.get("/api/practice")
def api_practice_list():
    """所有练习项目（按更新时间倒序）。"""
    from webapp.core import practice_log as pl
    return {"items": pl.list_practices(cfg)}


@app.get("/api/practice/detail")
def api_practice_detail(key: str):
    """单个练习项目：各轮明细 + 掌握度趋势 + 报告/趋势图路径。"""
    from webapp.core import practice_log as pl

    data = pl.load_practice(cfg, key)
    if not data.get("rounds"):
        raise HTTPException(404, "该练习项目还没有记录")
    pr = pl.render_practice(cfg, key)
    trend = pl.compute_trend(data.get("rounds", []))
    weak = pl.recurring_weak_segments(data.get("rounds", []))
    return {"data": data, "trend": trend, "weak": weak,
            "report_path": pr.get("report_path"), "chart_path": pr.get("chart_path")}


# ---------------------------------------------------------------- 通用
@app.get("/api/task/{tid}")
def api_task(tid: str):
    t = jobs.get_task(cfg, tid)
    if not t:
        raise HTTPException(404, "任务不存在")
    return JSONResponse(jobs.sanitize(t))


@app.get("/api/tasks")
def api_tasks():
    return JSONResponse(jobs.sanitize([
        {"id": t["id"], "kind": t["kind"], "status": t["status"],
         "stage_label": t.get("stage_label"), "progress": t.get("progress"),
         "created_at": t.get("created_at"), "error": t.get("error")}
        for t in jobs.list_tasks(cfg)[:50]]))


@app.get("/api/file")
def api_file(path: str):
    """下载产物。限制在 outputs / projects 目录内。"""
    ap = os.path.abspath(path)
    allowed = [os.path.abspath(cfg["outputs_dir"]),
               os.path.abspath(cfg["projects_dir"])]
    if not any(ap.startswith(a) for a in allowed):
        raise HTTPException(403, "路径不在允许范围")
    if not os.path.exists(ap):
        raise HTTPException(404, "文件不存在")
    if os.path.isdir(ap):
        raise HTTPException(400, "是目录")
    return FileResponse(ap, filename=os.path.basename(ap))


@app.get("/api/preview")
def api_preview(path: str):
    ap = os.path.abspath(path)
    allowed = [os.path.abspath(cfg["outputs_dir"]),
               os.path.abspath(cfg["projects_dir"])]
    if not any(ap.startswith(a) for a in allowed):
        raise HTTPException(403, "路径不在允许范围")
    if not os.path.exists(ap):
        raise HTTPException(404, "文件不存在")
    if ap.lower().endswith((".jpg", ".jpeg", ".png")):
        return FileResponse(ap)
    with open(ap, encoding="utf-8", errors="replace") as fh:
        return JSONResponse({"text": fh.read()[:20000]})


@app.get("/api/health")
def health():
    return {"ok": True, "llm": cfgmod.llm_ready(cfg),
            "ffmpeg": os.path.exists(cfg["ffmpeg"]),
            "yt_dlp": os.path.exists(cfg["yt_dlp"])}
