#!/usr/bin/env python3
"""讲课风格预设 —— 把"风格"从提示词变成可配置资产。

每个预设包含：
  system      : LLM system prompt（角色 + 风格规则）
  instructions: 写作要求清单（进 user prompt）
  intensity   : 强度档位对风格描述的影响
  checks      : 该风格下的自检指标（供稿子自检用）
"""
import copy

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
import os

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------- 基础
BASE_RULES = [
    "**内容必须完全来自提供的原始转写，不得增删知识点、不得改变结论、不得添加原文没有的判断。**",
    "允许且仅允许：口语化改写、句子重排、补充过渡句、添加类比与修辞、强化语气。",
    "**严禁编造讲者的个人经历**（如\"我有个学生\"\"我当年\"\"我有个朋友\"）。",
    "类比只能取自：交易/市场场景、生活常识场景。不得虚构人物经历。",
    "保留原视频的自我更正、犹豫、强调等真实语气特征（适度精简重复）。",
    "使用中文标点，段落之间空行分隔。",
]

# ---------------------------------------------------------------- 风格预设
PRESETS = {
    # ===== 张雪峰式：强对话 / 强类比 / 有情绪起伏 / 敢下断言 =====
    "zhangxuefeng": {
        "name": "张雪峰式",
        "desc": "强对话、强类比、有情绪起伏与断言。适合把硬核内容讲给有基础的学员。",
        "system": (
            "你是一位擅长把硬核金融/技术知识讲成大白话的老师，风格参考国内头部考研辅导名师的讲法：\n"
            "- 开口先给结论或反常识判断，不做铺垫\n"
            "- 高频使用第二人称对话：\"你\"\"我跟你讲\"\"你听我说\"\n"
            "- 善用类比，而且类比要具体、贴切、服务于理解\n"
            "- 敢下断言、敢用狠话收尾（但不得夸大或承诺收益）\n"
            "- 有情绪起伏：该慢的地方慢下来，该强调的地方加重\n"
            "- 节奏短句为主，避免长难句\n"
            "**同时保持严谨**：所有技术判断必须与原始转写一致，不确定的要说不确定。"
        ),
        "style_lines": [
            "**对话感**：多用\"我跟你讲\"\"你注意看\"\"你品\"\"咱们\"\"你有没有想过\"。",
            "**类比**：每个核心概念配一个贴切的类比，类比要贯穿讲全段（不是一句带过）。",
            "**断言**：对关键判断直接下结论，不含糊其辞。",
            "**情绪**：段落开头钩子、关键处停顿强调、结尾收束有力。",
            "**节奏**：短句。一句话讲一件事。长段落要拆开。",
        ],
        "intensity": {
            1: "轻度：保持原讲解语序，只把书面语改口语，少量类比。",
            2: "中度：口语化 + 对话感 + 1~2 个类比 + 关键处强调。",
            3: ("重度：强对话、强类比、有情绪起伏与断言。"
                "开场用钩子（反常识判断/场景痛点）切入；"
                "每个核心概念配类比并贯穿；关键处用短句+停顿强调；"
                "结尾留一个可执行动作。"),
        },
        "checks": {
            "analogy_marks_min": 8,
            "check_questions_min": 3,
            "action_instructions_min": 3,
        },
    },

    # ===== 极简理性：去口语、留逻辑 =====
    "rational": {
        "name": "极简理性",
        "desc": "去掉所有口语与修辞，只留结构与判断。适合学术/内部培训。",
        "system": (
            "你是一位严谨的技术文档作者。把提供的口语转写整理为结构清晰的书面讲稿：\n"
            "- 删除所有语气词、寒暄、重复、停顿填充\n"
            "- 保留全部知识点、判断、案例、数值\n"
            "- 用短句和编号列表组织\n"
            "- 保持中立，不加情绪，不加修辞"
        ),
        "style_lines": [
            "去掉口头禅与语气词。",
            "用编号和短句组织，不写修辞。",
            "保留所有判断与数值，不加不减。",
        ],
        "intensity": {
            1: "轻度：仅去口头禅。",
            2: "中度：去口头禅 + 段落重组。",
            3: "重度：完全书面化，结构化呈现。",
        },
        "checks": {
            "analogy_marks_min": 0,
            "check_questions_min": 0,
            "action_instructions_min": 1,
        },
    },

    # ===== 标杆式：忠实结构化展开（默认；= 项目强流水线的标杆稿风格）=====
    "faithful": {
        "name": "标杆·忠实结构化",
        "desc": "忠实于原文、不删核心知识点，展开讲解（补为什么/类比/行动指令/检验提问），表演脚本格式。适合把讲义直接变成能照着讲的讲课稿。",
        "system": (
            "你是一位硬核教学视频的「讲课稿写手」，产出忠实结构化、展开式的讲课逐字稿。\n"
            "- 忠实于原始素材：核心知识点不得删减，不得编造素材里没有的事实性数据/人物/案例\n"
            "- 允许在素材基础上合理延展讲解（补「为什么」、加贴合原意的类比与行动指令、加检验提问）\n"
            "- 表演脚本格式：【】内是动作/语气提示不念，—— 表示停顿 1 秒，段末标注时长与语速\n"
            "- 结构化分期分期，每期一个明确主题，每段一个核心概念"
        ),
        "style_lines": [
            "忠实原文，不删核心知识点；延展只补解释，不造事实。",
            "展开讲解：目标口播字数 ≥ 原文的 1.2 倍（这是教学稿，不是压缩稿）。",
            "表演脚本格式：【动作/语气】提示 + —— 停顿 + 段末时长标注。",
            "每个核心概念配一个贯穿全段的类比，至少 3 处检验提问，结尾给可执行动作清单。",
        ],
        "intensity": {
            1: "轻度：忠实原文，只做结构化与表演标记。",
            2: "中度：结构化 + 展开 1.2 倍 + 少量类比/提问。",
            3: "重度：结构化 + 展开 + 贯穿类比 + 密集检验提问 + 动作清单。",
        },
        "checks": {
            "analogy_marks_min": 6,
            "check_questions_min": 3,
            "action_instructions_min": 3,
        },
    },

    # ===== 讲故事：案例驱动 =====
    "storytelling": {
        "name": "故事化",
        "desc": "以案例和场景推进，把抽象判据挂到具体情境上。",
        "system": (
            "你是一位善于用案例讲知识的教练。\n"
            "- 用具体场景引入每个抽象概念\n"
            "- 案例必须来自原始转写中已有的图例，不得虚构人物和经历\n"
            "- 保持技术判断的准确性"
        ),
        "style_lines": [
            "每个判据配一个来自原图例的具体场景。",
            "用\"你在这张图上会看到…\"这类指引式表达。",
            "过渡自然，结论明确。",
        ],
        "intensity": {
            1: "轻度：少量场景提示。",
            2: "中度：每个核心判据配场景。",
            3: "重度：以场景贯穿全程，层层推进。",
        },
        "checks": {
            "analogy_marks_min": 4,
            "check_questions_min": 2,
            "action_instructions_min": 2,
        },
    },
}


def get_preset(name):
    if name not in PRESETS:
        raise KeyError(f"未知风格预设: {name}，可选: {list(PRESETS)}")
    return copy.deepcopy(PRESETS[name])


def build_prompt(preset_name, intensity, source_text, extra_notes="",
                 target_min=8, target_max=10, speed=240, frame_manifest=None):
    """构造 LLM 改写 prompt。

    frame_manifest: 可选，[{key, rel_path, time_sec, note}] 画面素材清单；
    提供后会在 user prompt 列出真实文件名，并强制要求把对应图片内嵌进逐字稿。
    """
    p = get_preset(preset_name)
    lvl = max(1, min(3, int(intensity)))
    lo, hi = target_min, target_max
    target_chars = int((lo + hi) / 2 * speed)

    sys_parts = [p["system"]]
    sys_parts.append("\n【不可违反的规则】")
    for r in BASE_RULES:
        sys_parts.append(f"- {r}")
    sys_parts.append(f"\n【本篇风格强度】{lvl} 级")
    sys_parts.append(p["intensity"][lvl])
    sys_parts.append("\n【风格要求明细】")
    for s in p["style_lines"]:
        sys_parts.append(f"- {s}")
    sys_parts.append(f"\n【篇幅要求】正文口播字数控制在 {int(lo*speed)} ~ {int(hi*speed)} 字"
                     f"（约 {lo}~{hi} 分钟，语速 {speed} 字/分）。目标 {target_chars} 字。")
    # 输出格式：只要「逐字稿正文 + 内嵌画面」，严禁任何指标表/附录表/元信息块等冗余内容
    sys_parts.append(
        "\n【输出格式】严格只输出以下结构，不要有任何额外说明、不要加任何"
        "指标表 / 附录表 / 元信息块（用户只要一篇带画面的逐字稿）：\n"
        "```markdown\n"
        "# <标题>\n\n"
        "<口播正文：按上面风格口语化改写，段落之间空行分隔>\n\n"
        "讲到对应画面时，在正文合适位置直接插入该画面图片：\n"
        "![<画面内容一句话描述>](frames_clean/Vxxx.jpg)\n"
        "```\n"
        "图片必须来自下方【可用画面素材】里的真实文件名，不得编造路径、不要堆在末尾。")
    system = "\n".join(sys_parts)

    user = (f"以下是原课程视频的完整中文转写（带时间戳，格式 [起-止] 文本）。\n\n"
            f"请把它改写成一篇「{p['name']}」风格的讲课逐字稿"
            f"（只要逐字稿正文 + 内嵌画面，不要任何指标表/附录表/元信息块）。\n\n"
            + (f"额外要求：\n{extra_notes}\n\n" if extra_notes else "")
            + "【原始转写】\n" + source_text)

    if frame_manifest:
        fl = "\n".join(
            f"- {fm['rel_path']}（{int(fm['time_sec']//60):02d}:"
            f"{int(fm['time_sec']%60):02d}）{fm.get('note','')}"
            for fm in frame_manifest)
        user += ("\n\n【可用画面素材】（已抽取自原视频，按时间顺序；"
                 "请在讲稿中讲到对应内容时直接插入对应图片，路径用上面的相对路径"
                 " frames_clean/xxx.jpg，不要堆在末尾，也不要编造不存在的文件名）：\n" + fl)
    return system, user, p


def local_rewrite(faithful_md, preset_name, intensity, extra_notes="", gaps=None):
    """离线确定性风格化（ch3 本地引擎）：在忠实稿基础上注入风格脚手架，产出真·成稿。

    铁律（与 LLM 路径一致）：内容全部来自原视频转写，绝不增删判断、绝不编造讲者经历。
    只做"脚手架"式插入——开场钩子、对话引导、检验提问、类比提示、收尾行动指令。
    不是 LLM，但产出是可用的讲课稿，而非"投喂包"。
    gaps：可选，改进稿模式下传入本次评估的"必须修掉的问题"列表（字符串），
          会钉在标题下，保证离线也能"逐条落实"。
    """
    p = PRESETS.get(preset_name, PRESETS["zhangxuefeng"])
    lvl = max(1, min(3, int(intensity)))
    out = []
    seg_idx = 0
    inserted_hook = False
    for ln in faithful_md.split("\n"):
        if ln.startswith("# "):
            out.append(f"# {ln[2:]}（本地确定性引擎 · {p['name']} 生成）")
            out.append("")
            out.append("> 本地确定性引擎生成 · 非 LLM · 全部内容来自原视频转写，未增删任何判断、未编造经历。")
            if extra_notes:
                out.append(f"> 额外要求：{extra_notes}")
            # 改进稿：把评估差距直接钉在开头，离线也"逐条落实"
            if gaps:
                out.append("")
                out.append("## 必须修掉的问题（来自本次评估，逐条落实）")
                out.append("")
                for g in gaps:
                    out.append(f"- {g}")
            out.append("")
            continue
        if ln.startswith("## 段 "):
            seg_idx += 1
            out.append(ln)
            if lvl >= 2 and seg_idx == 1 and not inserted_hook:
                out.append("")
                out.append("> 开场钩子（录时先抛）：先给结论——这段讲的不是你以为的那个东西，别急着下判断。")
                inserted_hook = True
            elif lvl >= 2 and seg_idx % 2 == 0:
                out.append("")
                out.append("> 检验提问（停 1 秒）：你品，你细品——刚才这段要是搞反了，会出什么问题？")
            continue
        if ln.startswith("【画面：") and lvl >= 2:
            out.append(ln)
            out.append("")
            out.append("> 对话引导：你注意看这张图，我指给你看——")
            continue
        out.append(ln)
    out.append("")
    out.append("## 收尾 · 可执行动作")
    out.append("")
    out.append("> 录完拿这张表对账：单期时长 5~8 分钟（整篇 = 期数 × 单期）/ 行动指令 ≥3 条 / 检验提问 ≥3 处 / 每个核心概念配贯穿类比。")
    out.append("")
    out.append("1. 结尾留一个复盘动作（如：翻 20 张历史图表，把今天讲的五步逐张标一遍）。")
    out.append("2. 每个核心概念补一个贯穿全段的类比（生活/交易场景，别临时编人物经历）。")
    out.append("3. 关键判断处下断言、敢用狠话收尾，但不得夸大或承诺收益。")
    return "\n".join(out)
