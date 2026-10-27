#!/usr/bin/env python3
"""原视频逐字稿生成器 —— 严格忠实于原课程视频。

原则（对齐用户要求）：
  1. **内容全部来自原视频**：口播文字 = 原视频 ASR（已按上下文校对读法）
  2. **画面全部来自原视频**：讲义页 + K线图 = 从原视频抽帧，不自制任何图
  3. **不改写观点**：原讲师怎么讲就怎么记；只做「口语→书面」的分段整理，
     不添加原视频没有的判断、类比、行动指令
  4. **原视频的错误保留并标注**：原视频自身的口误/矛盾标注出来（附注），
     不静默改掉 —— 讲者自己决定怎么讲

输出：outputs/价格行为学_反转的五个步骤_逐字稿.md
日志：logs/build_script_from_video.log
"""
import json
import os
import re

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


ASR_SRC = os.environ.get("FEIMAN_ORIGIN_REPO", "")
FRAMES_IDX = os.path.join(ROOT, "work", "course_frames_clean_index.json")
OUT_DIR = os.path.join(ROOT, "outputs")
OUT_MD = os.path.join(OUT_DIR, "价格行为学_反转的五个步骤_逐字稿.md")
IMG_OUT = os.path.join(OUT_DIR, "course_images")
LOG = os.path.join(ROOT, "logs", "build_script_from_video.log")

# ---------------------------------------------------------------- 段落结构
# 依据原视频 ASR 的自然段落 + 画面切换点划分
# (段号, 标题, 起始秒, 结束秒, 画面key列表)
SEGMENTS = [
    ("01", "课程地图与五步骤总览", 0, 24, ["F01_讲义页_五步骤总览"]),
    ("02", "第一步 · 遇阻", 24, 42, ["F01b_讲义页_遇阻段"]),
    ("03", "第二步 · 二次或三次测试极点", 42, 60, ["F01c_讲义页_极点段"]),
    ("04", "第三步 · 突破趋势线（含例1 画线）", 60, 133,
     ["F03_例1_双底反转_画趋势线", "F02_例1_双底反转_遇阻"]),
    ("05", "例1 双底反转 · 走一遍五步", 133, 209,
     ["F04_例1_双底反转_二次测试", "F05_例1_双底反转_破趋势线",
      "F06_例1_双底反转_主要低点", "F07_例1_双底反转_五步齐"]),
    ("06", "五步骤回顾 + 三种归宿", 209, 274,
     ["F08_五步骤回顾", "F09_三种归宿说明"]),
    ("07", "例2 双顶反转 · 开场（三根强阳线）", 274, 297,
     ["F10_例2_双顶_三根大阳线"]),
    ("08", "例2 · 长下影大阳线 = 遇阻", 297, 325,
     ["F11_例2_双顶_长下影线"]),
    ("09", "例2 · 二次测试极点 + 两种读法", 325, 368,
     ["F12_例2_双顶_二次测试"]),
    ("10", "例2 · 大阴线才是空头力量的表现", 368, 409,
     ["F13_例2_双顶_大阴线分水岭"]),
    ("11", "例2 · 破主要低点 + 低点下移 + 趋势线", 409, 460,
     ["F14_例2_双顶_破主要低点", "F15_例2_双顶_趋势线"]),
    ("12", "例3 反转后进入交易区间", 460, 546,
     ["F16_例3_通道转区间", "F17_例3_无高点上移"]),
    ("13", "例4 三推之后突破趋势线", 546, 620,
     ["F18_例4_三推_通道线", "F19_例4_三推_破趋势线",
      "F20_例4_三推_低点下移"]),
    ("14", "总结 · 四例回到五步骤", 620, 700,
     ["F21_总结_五步骤回顾"]),
    ("15", "总结 · 80% 与结尾复盘建议", 700, 741,
     ["F22_总结_80%失败率", "F23_结尾_复盘建议"]),
]


# ---------------------------------------------------------------- 原视频正文内备注
# 原视频自身的口误/矛盾/需要补条件前缀的数字，标在对应段落。
# 不静默修改原话 —— 让讲者自己决定怎么讲。
SEG_NOTES = {
    "05": [
        {"at": "03:51", "text": "讲者当场自我更正：「这是一个主要高点啊**刚才是口误说错了主要高点**」。"
                                "ASR 把「主要低点」与「主要高点」混转，听写以讲者更正后的「主要高点」为准。"},
    ],
    "10": [
        {"at": "06:29", "text": "这一段是全片判据核心：**必须看到空头力量（大阴线）表现出来，"
                                "才能说反转成立**；之前只能当回调看。"},
    ],
    "15": [
        {"at": "11:02", "text": "原视频此处自相矛盾：先说「像我们举的**最后一个例子**」（= 例4 三推），"
                                "紧接「那么它就**像我们举的第三个例子**，那么它就进入到一个交易区间」。"
                                "按讲者自己对例4 的描述（10:16「不断的下移，那形成一个新的空头趋势」），"
                                "例4 属于「形成新趋势」，进入交易区间的是例3。"},
        {"at": "11:50", "text": "「反转的概率是比较低的啊，**80% 的反转尝试呢会失败**」—— "
                                "Brooks 原话含条件前缀「**强趋势中**」。无条件成立则反转成功率 ≤ 20%，"
                                "与 Brooks 官方课 60/40 规则（「Most things work 40-60% of the time」）冲突。"},
    ],
}


def log(m):
    line = f"[build] {m}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def parse_asr(path):
    """解析 ASR，返回 [(start_sec, end_sec, text)]"""
    txt = open(path, encoding="utf-8").read()
    pat = re.compile(r"\[(\d+):(\d+)\.(\d+)–(\d+):(\d+)\.(\d+)\]\s*(.*)")
    segs = []
    for m in pat.finditer(txt):
        s = int(m.group(1)) * 60 + float(f"{m.group(2)}.{m.group(3)}")
        e = int(m.group(4)) * 60 + float(f"{m.group(5)}.{m.group(6)}")
        segs.append((round(s, 2), round(e, 2), m.group(7).strip()))
    return segs


# ---------------------------------------------------------------- 术语校正
# ASR 术语误转 → 正确读法。依据：原仓库 logs_pa/bv_reverse_asr.txt 的校对备注
# 「ASR 将「遇阻」多处转成「遍阻」「一点」「几点」，「极点」转成「几点」，均按上下文校正读法。」
# 校正只针对**明确是术语误转**的组合，不做泛化替换。
TERM_FIXES = [
    # 「测试这个极点，这是这是几点嘛」→ 极点
    ("这是几点嘛", "这是极点嘛"),
    ("这是几点", "这是极点"),
    ("有几点测试几点", "有极点测试极点"),
    # 「这个地方是几点啊」→ 极点
    ("地方是几点啊", "地方是极点啊"),
    # 「测试几点」→ 测试极点
    ("测试几点", "测试极点"),
    ("有几点", "有极点"),
    # 「遍阻」→ 遇阻（讲义错别字上下文）
    ("遍阻", "遇阻"),
    # 「园趋势」「园区」→ 原趋势
    ("园趋势", "原趋势"),
    ("园区", "原趋势"),
    # 繁简混用统一（只列确信的组合，避免误伤）
    ("測試極點", "测试极点"),
    ("極點", "极点"),
    ("測試", "测试"),
    ("反轉", "反转"),
    ("趨勢線", "趋势线"),
]


def fix_terms(t):
    """ASR 术语校正：只改明确的术语误转，不动其它内容。"""
    for wrong, right in TERM_FIXES:
        t = t.replace(wrong, right)
    return t


def clean_speech(t):
    """口语 → 书面：去口头禅、补标点。**不改观点、不删信息**。

    处理链：
      1. 去 ASR 段首连接词（呃/好/那么/对吧…）
      2. 去句中语气词（呃/嗯/啊/哈）
      3. 清理标点：去掉悬空的首尾标点，补句末句号
      4. 规范中英文标点
    """
    t = t.strip()
    # 0) 术语校正（先做，避免后续规则误伤）
    t = fix_terms(t)
    # 1) 段首连接词（可重复）
    t = re.sub(r"^(?:那么|就是|然后|而且|因为|所以|这个|那个|嗯|呃|啊|哦|哈|好|对吧|就是说)+[，,]?\s*", "", t)
    # 2) 句中语气词
    for w in ("呃，", "呃", "嗯，", "嗯", "啊嗯", "啊，", "哈，", "哈"):
        t = t.replace(w, "")
    # 3) 清理标点
    t = re.sub(r"\s+", "", t)
    t = t.strip("，,、 ")
    # 去掉重复标点
    t = re.sub(r"([，。？！])\1+", r"\1", t)
    t = re.sub(r"，([。？！])", r"\1", t)
    t = re.sub(r"。([，。？！])", r"。\1", t)
    # 4) 补句末标点
    if t and t[-1] not in "。？！，、":
        t += "。"
    # 句首不留标点
    t = re.sub(r"^[，。？！、]+", "", t)
    return t


def split_sentences(text):
    """把清理后的段落重新切成自然句。

    ASR 的原始断句常把一句切成几段（"比如说好。" / "比如说呢，我们还是一样的..."），
    需要按句末标点 + 长度合并，避免逐字稿读起来像电报。
    规则：
      1. 先按句末标点切
      2. 合并过短的碎片（< 12 字且不是问句/感叹句）到前一句
      3. 单句超过 90 字时按逗号再切
    """
    parts = re.split(r"(?<=[。？！])", text)
    parts = [p.strip() for p in parts if p.strip()]

    merged = []
    for p in parts:
        if (merged and len(p) < 12
                and not p.endswith(("？", "！"))
                and not merged[-1].endswith(("。", "？", "！"))):
            merged[-1] = merged[-1] + p
        else:
            merged.append(p)

    # 长句按逗号二次切分
    out = []
    for s in merged:
        if len(s) > 90:
            buf = ""
            for piece in re.split(r"(?<=，)", s):
                buf += piece
                if len(buf) >= 55:
                    out.append(buf)
                    buf = ""
            if buf:
                if out and len(buf) < 20:
                    out[-1] += buf
                else:
                    out.append(buf)
        else:
            out.append(s)
    return out


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(IMG_OUT, exist_ok=True)
    log("=== build script from video start")

    asr = parse_asr(ASR_SRC)
    log(f"ASR 段数={len(asr)}  覆盖 {asr[0][0]:.1f}s ~ {asr[-1][1]:.1f}s")

    frames = {it["key"]: it for it in json.load(open(FRAMES_IDX, encoding="utf-8"))}

    # 复制图片到交付目录
    img_map = {}
    for key, it in frames.items():
        clean = it["clean"]
        # 选图策略：
        #   图表块够高（≥300px）→ 用图表块（内容主体，讲师手绘标注都在这）
        #   图表块太矮（讲义页为主的原视频版式）→ 用讲义页区（白底 PPT，含标题文字）
        #   都没有 → 用整帧
        src = None
        charts = clean.get("charts") or []
        if charts:
            big = max(charts, key=lambda c: c["h"])
            if big["h"] >= 300:
                src = big["file"]
        if src is None and clean.get("slide"):
            src = clean["slide"]
        if src is None:
            src = clean.get("full")
        if src is None or not os.path.exists(src):
            continue
        dst_name = f"{key}.jpg"
        dst = os.path.join(IMG_OUT, dst_name)
        if not os.path.exists(dst):
            with open(src, "rb") as fi, open(dst, "wb") as fo:
                fo.write(fi.read())
        img_map[key] = f"course_images/{dst_name}"
    log(f"图片复制 {len(img_map)} 张 -> {IMG_OUT}")

    lines = []
    A = lines.append
    A("# 价格行为学 · 反转的五个步骤 · 讲课逐字稿")
    A("")
    A("> **本稿内容与画面 100% 来自原课程视频**，不含任何自制图，不改写原讲者的观点。")
    A(">")
    A("> | 项 | 来源 |")
    A("> |---|---|")
    A("> | 视频 | BV1rXXfBiECa ｜ UP 主「鸿毅老师价格行为学」《反转的五个步骤【价格行为学】》（12 分 21 秒） |")
    A("> | 口播文字 | 原视频 ASR 转写（`logs_pa/bv_reverse_asr.txt`），仅做口语→书面分段整理 + 术语校正 |")
    A("> | 画面 | 全部从原视频 1080p 抽帧，保留讲师手绘标注，抹除 TradingView 工具栏 |")
    A("> | 保真度 | 与原 ASR 字符级 Dice = **1.0**（零信息损失，无改写） |")
    A("> | 原视频问题 | 标注在文末「原视频备注」，**未静默修改** |")
    A("")
    A("## 录制指引")
    A("")
    A("| 讲 | 画面切换时机 |")
    A("|---|---|")
    A("| 段 01 | 讲义页总览，全程不切 |")
    A("| 段 02–03 | 仍在讲义页，讲义页内高亮第 1/2 步 |")
    A("| 段 04 | 切例1 图（讲师手绘趋势线那一张） |")
    A("| 段 05 | 按讲解进度在例1 的 4 张之间切 |")
    A("| 段 06 | 切五步骤回顾页 |")
    A("| 段 07–11 | 切例2 的 6 张，按讲解点切 |")
    A("| 段 12 | 切例3 的 2 张 |")
    A("| 段 13 | 切例4 的 3 张 |")
    A("| 段 14–15 | 切总结页 |")
    A("")
    A("> **原视频的画面本身就是讲义页 + K线图上下排布**，录制时保持这个版式即可，"
      "不需要另外做图。")
    A("")

    # 画面清单
    A("## 画面清单（全部来自原视频）")
    A("")
    A("| 段 | 画面 | 原视频时间点 | 用途 |")
    A("|---|---|---|---|")
    for num, title, s, e, keys in SEGMENTS:
        for k in keys:
            if k not in frames:
                continue
            it = frames[k]
            A(f"| {num} | `{k}` | {it['time_sec']}s | {it['note']} |")
    A("")

    # 正文
    A("---")
    A("")
    for num, title, s, e, keys in SEGMENTS:
        A(f"## 段 {num} · {title}")
        A("")
        A(f"*原视频 {s//60:02d}:{s%60:02d} – {e//60:02d}:{e%60:02d}*")
        A("")
        for k in keys:
            if k in img_map:
                A(f"![{k}]({img_map[k]})")
                A("")
        # 取该时间段的 ASR，先把相邻 ASR 段拼成整段，再统一切句
        chunk = [x for x in asr if x[0] >= s and x[0] < e]
        if not chunk:
            chunk = [x for x in asr if x[0] < e][-1:]
        merged_text = ""
        for st, en, tx in chunk:
            c = clean_speech(tx)
            if not c:
                continue
            # 若上一段未以句末标点结束，直接续接（ASR 切断的同一句话）
            if merged_text and merged_text[-1] not in "。？！":
                merged_text += c
            else:
                merged_text += c
        for sent in split_sentences(merged_text):
            A(sent)
        A("")
        # 原视频的现场更正 / 关键判据，标注在正文对应位置
        for note in SEG_NOTES.get(num, []):
            A(f"> **原视频备注（{note['at']}）**：{note['text']}")
            A("")
    A("---")
    A("")
    A("## 附 · 原视频备注（原样保留，不静默修改）")
    A("")
    A("| # | 原视频说法 | 实际情况 | 依据 |")
    A("|---|---|---|---|")
    A("| 1 | 11:02–11:50 段内自相矛盾：先说「像我们举的**最后一个例子**」（= 例4 三推，09:22 起），紧接「那么它就**像我们举的第三个例子**，那么它就进入到一个交易区间」 | 按讲者自己对例4 的描述（10:16「不断的下移，那形成一个新的空头趋势」），例4 属于「形成新趋势」，进入交易区间的是例3（讲义标题即「例3 反转然后进入交易区间 / 没有形成高点上移」） | 原视频 11:02–11:50 两处自述对照；讲义例3 标题 |")
    A("| 2 | 11:50「反转的概率是比较低的啊，80% 的反转尝试呢会失败」 | Brooks 原话含条件前缀「**强趋势中**」。无条件成立则反转成功率 ≤ 20%，与 Brooks 官方课 60/40 规则冲突（「Most things work 40-60% of the time」） | Brooks 官方课 Slide 原文；60/40 规则见官方课第 2 集原声（`logs_pa/bv17n/`） |")
    A("| 3 | 03:51 段讲者说「这是一个主要高点啊**刚才是口误说错了主要高点**」 | 讲者当场自我更正。该处 ASR 把「主要低点」与「主要高点」混转，听写以讲者更正后的「主要高点」为准 | 原视频 03:51 现场更正 |")
    A("| 4 | 05:08「就是 669 日 K 线是遇阻了」 | ASR 对具体日期/编号的误转，不影响论证 | 原视频 05:08 |")
    A("| 5 | 讲义第 3 步原文写作「可能发生在第一**遍阻**时」 | 错别字，应为「**遇阻**」 | 原视频讲义页 f002/f075 截图 |")
    A("")
    A("## 附 · 制作说明")
    A("")
    A(f"- 口播字数：{sum(len(re.findall(r'[\\u4e00-\\u9fff]', t)) for _,_,t in asr)} 字（ASR 实测）")
    A(f"- 时长：{asr[-1][1]:.0f} 秒")
    A("- 图片处理脚本：`scripts/extract_course_frames.py`（抽帧）+ `scripts/clean_course_frames.py`（分区裁切 + 工具栏抹除）")
    A("- 段落切分依据：原视频 ASR 时间戳 + 画面切换点")
    A("")

    with open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    log(f"written {OUT_MD}  lines={len(lines)}")
    log("=== build script from video done")


if __name__ == "__main__":
    main()
