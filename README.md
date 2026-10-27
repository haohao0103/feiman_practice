# 费曼学习法 · 讲课稿工作台

把「一段课程视频 / 一篇讲义」变成**能照着念的讲课逐字稿课件**，再让你**照着课件试讲**，
由 AI **逐段对账**你的试讲效果，给出改进意见与下一版稿，**循环迭代直到掌握**。

一句话：**这是"教 - 学 - 练 - 评 - 改"闭环的自动化流水线。**

---

## 完整闭环

```
① 视频 URL / 文档
        │
        ▼
② AI 生成指定风格的逐字稿课件      ← 结构化分期 · 展开不压缩 · 表演脚本格式
        │                            （讲义全部来自原视频/原文画面，不自造事实）
        ▼
③ 你照着课件试讲（录音 / 录屏）
        │
        ▼
④ AI 逐段对账课件 ↔ 试讲            ← 覆盖 / 漏讲 / 说错 / 检验提问是否问了
        │                            行动指令是否给了 / 该指的画面是否指了
        ▼
⑤ 掌握度评分 + 逐段对账报告 + 改进稿
        │
        ▼
⑥ 归档进练习项目 → 掌握度趋势曲线 + 跨轮薄弱段 + 下一步建议
        │
        └──► 回到 ③，循环直到达标（默认 85 分）
```

Web 界面四个 tab 就是这条链路：

| tab | 功能 |
|---|---|
| ① 视频 → 风格化讲课稿 | 贴 B 站视频地址 → 下载 → 离线转写 → 抽原视频画面 → 生成课件 |
| ② 文档 → 讲课课件 | 上传 md/txt/docx/doc/rtf/html/pdf → 按文档标题结构重排成讲课大纲 → 课件 |
| ③ 录屏 → 对账 → 改进稿 | 上传试讲录屏 + 对照课件 → 逐段对账 → 掌握度 + 对账报告 + 改进稿 |
| ④ 练习追踪 | 每轮自动归档 → 掌握度趋势曲线 + 反复薄弱段 + 下一步该练哪几段 |

---

## 快速开始

```bash
# 1) 依赖
pip install -r requirements.txt

# 2) 配置（真实 key 只放本地，不入库）
cp webapp/config.example.json webapp/config.json
$EDITOR webapp/config.json        # 填 llm_api_key / llm2_api_key（主/备用通道）

# 3) 启动
python3 -m uvicorn webapp.app:app --host 127.0.0.1 --port 8848
```

打开 http://127.0.0.1:8848

> ASR 用本地离线模型（faster-whisper），视频不会上传到第三方；LLM 走 OpenAI 兼容协议
> （主通道挂了自动切备用通道）。

### 不起服务，直接跑流水线

```bash
# 视频 → 标杆级课件（无头，按 bvid 分目录，缓存复用 video/asr）
python3 scripts/test_bench_pipeline.py "https://www.bilibili.com/video/BV1XXXXXXXX/"

# 文档 → 标杆级课件
python3 scripts/test_doc_courseware.py <文档路径> [风格] [强度]

# 课件 ↔ 试讲 逐段对账（掌握度 + 对账报告 + 改进稿）
python3 scripts/test_rehearsal_alignment.py [课件md] [试讲asr.txt]
```

---

## 目录结构

```
webapp/                  # Web 应用（FastAPI）
  app.py                 #   路由：/api/video-to-script /api/doc-to-courseware /api/video-review /api/practice
  config.json            #   本地配置（含真实 key，**不入库**）
  config.example.json    #   配置样例（入库）
  core/
    courseware_gen.py    #   课件生成主引擎（大纲→抽帧→成稿→纠错→组装→PDF）
    doc_courseware.py    #   文档 → 课件（md/docx/pdf/… 解析 + 标题骨架重排）
    rehearsal_review.py  #   课件 ↔ 试讲 逐段对账引擎 + 掌握度评分
    practice_log.py      #   练习迭代追踪（趋势曲线 + 跨轮薄弱段 + 下一步建议）
    pipeline_ops.py      #   ASR / LLM 双通道调用 / 画面加工 / 稿件组装
    runner.py            #   三条链路的任务编排
    jobs.py  styles.py  pdf_builder.py  config.py
  templates/index.html   #   单页前端（四个 tab）

scripts/                 # 流水线脚本 + 质检脚本 + 测试脚本（见 scripts/README.md）
courseware_v2/           # 人工录前自查表（费曼四关）
examples/                # 标杆课件样例（产出长什么样）
docs/                    # 设计决策与踩坑记录
```

---

## 核心设计决策

每一条都是踩过坑之后定死的，改前先看 `docs/设计决策与红线.md`。

| 决策 | 为什么 |
|---|---|
| **画面张数按内容自适应**，不设固定张数 | 图表密集的课 20+ 张、纯口播 3 张，固定值必然一头错。视觉去重阈值 0.03 由实测断层标定（噪声地板 0.022 / 下一档 0.035），不拍脑袋 |
| **课件要"展开"而非"压缩"** | 教学稿要讲清"为什么"，目标口播 ≥ 原文 1.2 倍；压缩稿只能照着念，理解不了 |
| **术语必须当场解释** | 费曼法核心：讲得清才算懂。自检把"未当场解释的术语数 = 0"列为硬指标 |
| **时长承诺按「单期」口径** | 单期 5~8 分钟（= 期数 × 单期 = 整篇）。拿单期目标去判整篇会误判超标 2 倍 |
| **质量闸门在写盘前跑** | 不达标（截断/行动指令不足/提问不足）→ 顶层横幅 + 日志报警 + Web 红条，不许静默出稿 |
| **逐段对账要看原句** | 「你说的」必须带转写原句依据，禁止泛泛而评 |
| **只补缺的那一节，不重写全稿** | 外科式修补（`repair_missing`）：重写会毁掉其它已达标项 |
| **临时文件一律放 `work/`，且用少量固定文件** | 交付目录里单次批量删除 >50 文件会被安全钩子拦截 → 后台进程永久卡死（详见 docs） |

---

## 质检与测试

```bash
# 课件质量复审（复用 check_script_quality.py 的词表，同一口径）
python3 scripts/audit_courseware.py <课件md>

# 稿子自检（产出承诺值，供录后对账）
python3 scripts/check_script_quality.py

# 端到端
python3 scripts/test_web_e2e.py        # 视频全链路（真实 HTTP）
python3 scripts/test_doc_web_e2e.py     # 文档全链路（真实 multipart 上传）
python3 scripts/test_rehearsal_alignment.py
python3 scripts/test_practice_loop.py   # 多轮归档 → 趋势 → 薄弱段

# 专项
python3 scripts/test_frame_dedupe.py            # 画面自适应去重
python3 scripts/test_frame_extract_cleanup.py    # 抽帧清理 + 临时目录不留残留
python3 scripts/calibrate_delete_hook.py         # safe-delete 钩子标定
python3 scripts/analyze_frame_diversity.py       # 画面相似度分布（阈值标定依据）
```

---

## 已知限制

- **视频源需可下载**：仅支持单个视频（非合集、非分P）；部分需要登录态的源不可用。
- **ASR 首次运行要下模型**，之后走本地缓存。
- **LLM 判定有波动**：同一份课件两次对账，个别段的覆盖判定可能不同（正因如此才做"趋势"而不是"绝对分"）。
- **`work/` 有 6.5G**：全是上传的录屏 mp4 + 抽帧中间图，不入库；删 `work/` 不影响任何代码。

---

## 数据与隐私

- `webapp/config.json`（含真实 API Key）**不入库**，仓库里只有 `config.example.json`。
- `work/`（上传的录屏、转写、抽帧中间产物）与 `outputs/`（生成的课件）**不入库**，均可由流水线重新生成。
- 仓库里唯一的媒体素材是 `examples/`（一份标杆课件样例，含取自公开教学视频的抽帧图）。

---

## 许可

内部项目，未指定公开许可。
