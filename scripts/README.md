# scripts/ 脚本地图

三类脚本：**当前主链路** / **质检与测试** / **历史固化路径**（保留可复现，不删）。

---

## 一、当前主链路（配合 `webapp/` 使用）

| 脚本 | 作用 |
|---|---|
| `test_bench_pipeline.py` | **无头跑完整课件流水线**：`python3 scripts/test_bench_pipeline.py <B站URL>` → 按 bvid 分目录产出 md+pdf，缓存复用 video/asr |
| `test_doc_courseware.py` | 文档 → 标杆级课件（无头）：`python3 scripts/test_doc_courseware.py <文档> [风格] [强度]` |
| `test_rehearsal_alignment.py` | 课件 ↔ 试讲 逐段对账 → 掌握度 + 对账报告 + 改进稿 |
| `test_practice_loop.py` | 多轮对账归档 → 掌握度趋势 → 跨轮薄弱段 → 趋势图 |
| `cleanup_work.py` | **work/ 暂存区清理**（默认试运行）：`--apply` 真删 / `--status` 看占用 / `--keep-days` `--max-gb` 调参 |
| `test_cleanup.py` | 清理逻辑安全属性测试（沙盒内跑，不碰真实 work/） |

## 二、质检与测试

| 脚本 | 作用 |
|---|---|
| `audit_courseware.py` | **课件质量复审**。复用 `check_script_quality.py` 的词表与 `body_of()`（同一口径，不另立标准），额外查结构/表演格式/图片内嵌位置/附录/术语当场解释/单期时长 |
| `check_script_quality.py` | **稿子自检（核心口径源）**：时长（单期 5~8 分钟）、行动指令 ≥3、检验提问 ≥3、类比 ≥8、未解释术语 = 0；产出 `work/script_baseline.json` 作为录后对账的承诺值 |
| `lexicon.json` | **词表与评分锚点**（口头禅/术语/类比标记/检验提问/行动指令）。口径要改就改这里，别改代码 |
| `check_against_baseline.py` | 承诺值 vs 实测值逐项对账 |
| `check_script_coverage.py` | 逐字稿 vs 原视频 覆盖度校验（保真度 Dice） |
| `feynman_score.py` | 费曼四维量化（F1 概念边界 / F2 外行语言 / F3 卡壳处理 / F4 教学闭环）+ 画面执行 |
| `analyze_frame_diversity.py` | 画面相似度分布 —— **去重阈值 0.03 的标定依据**（噪声地板 0.022 / 下一档 0.035 的断层） |
| `test_frame_dedupe.py` | 视觉去重：同屏合并、期级保底 |
| `test_frame_extract_cleanup.py` | 抽帧清理（抹工具栏/标尺/裁内容区）+ 临时目录不留残留 |
| `calibrate_delete_hook.py` | **safe-delete 钩子标定**：rmtree vs 逐个删除 × work/ vs outputs/（决定临时文件放哪、怎么删） |
| `test_web_e2e.py` / `test_doc_web_e2e.py` | 真实 HTTP 全链路（后者走真实 multipart 上传） |
| `test_error_policy.py` / `test_llm_stream.py` / `test_asr_progress.py` / `test_xiaomi_smoke.py` / `test_linka_images.py` | 分项测试：失败策略 / 流式 LLM / ASR 进度 / 通道连通 / 图片链路 |

## 三、历史固化路径（保留可复现，别当现行入口）

| 脚本 | 说明 |
|---|---|
| `build_courseware.py` | **原始**「原课程视频 → 忠实逐字稿」一条命令流水线（固定路径、手工校对过）。里面有分区裁切 + TradingView 工具栏抹除的原始实现，现行实现已抽到 `webapp/core/pipeline_ops.py:clean_course_frame` |
| `build_script_from_video.py` | 上一代视频 → 讲稿脚本 |
| `extract_course_frames.py` / `clean_course_frames.py` / `make_figures.py` | 上一代抽帧 / 画面清洗 / 出图 |
| `pipeline.py` | 上一代评审流水线（费曼四维报告） |
| `parse_asr.py` / `asr_chunk.py` / `bench_asr.py` | ASR 解析、分块、基准 |
| `analyze_metrics.py` / `repetition.py` / `audio_quality.py` / `volume_check.py` / `probe_media.py` / `scene_timeline.py` / `visual_structure.py` / `contact_sheet.py` / `render_report.py` / `orig_page_map.py` / `orig_scene_split.py` | 媒体与指标分析小工具 |

## 四、口径改动位置（改这里，别改代码）

| 想改什么 | 改哪 |
|---|---|
| 术语/口头禅/类比/提问/行动指令的识别词 | `lexicon.json` |
| 时长承诺、行动指令条数、提问处数、类比数、术语解释红线 | `check_script_quality.py` 的 `TARGETS` |
| 讲法风格（张雪峰式 / 标杆·忠实结构化 / 极简理性 / 故事化） | `webapp/core/styles.py` 的 `PRESETS` |
| 清理策略（保留天数 / 水位线 / 触发间隔） | `webapp/core/config.py` 的 `cleanup` 段（可用 `webapp/config.json` 覆盖） |
| 画面去重阈值 | `webapp/core/config.py` 的 `frame_dedupe_th`（标定依据见 `analyze_frame_diversity.py`） |
| 单期目标时长 | `webapp/core/config.py` 的 `script_target_min/max` + `check_script_quality.py` 的 `TARGETS`（两处要同步） |
