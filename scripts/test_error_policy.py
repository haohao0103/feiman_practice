#!/usr/bin/env python3
"""回归测试：验证 LLM 缺失时的策略。

规则（用户硬性要求）：
  有小米（双通道 ch1/ch2 任一可达）→ 用小米；
  无小米 → 默认直接报错，绝不静默降级为素材包 / 本地引擎。

本测试只验证策略分支，不下载、不跑 ASR、不打真实 LLM。
用法：
  python3 scripts/test_error_policy.py 2>&1 | tee logs/test_error_policy.log
"""
import os
import sys
import traceback

# 仓库根目录（由文件位置推导，避免硬编码本机路径）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


sys.path.insert(0, ROOT)

from webapp.core import runner, styles  # noqa: E402

def fake_cfg(offline_engine):
    return {
        "script_target_min": 8,
        "script_target_max": 10,
        "script_speed_cpm": 240,
        "offline_engine": offline_engine,
        # 以下 LLM 字段故意无效，且 call_llm 被 mock 成返回 None，
        # 用来模拟"小米双通道都挂了"的情况。
        "llm_api_key": "", "llm_base_url": "",
        "llm2_api_key": "", "llm2_base_url": "",
    }


def fake_segs():
    return [
        (0.0, 12.0, "同学们今天我们讲双底形态，先给结论，双底不是随便两个低点。"),
        (12.0, 30.0, "第一个低点之后会有一个反弹，反弹的高点叫颈线，然后再下来不破前低。"),
        (30.0, 48.0, "第二次下探不破前低，放量突破颈线，这才是有效双底，记住这个顺序。"),
    ]


def fake_gap():
    return {"rows": [
        {"metric": "行动指令数", "actual": "1", "promised": "≥3",
         "fix": "每段结尾补一个可执行动作（复盘/标图/对照）", "ok": False},
        {"metric": "检验提问数", "actual": "0", "promised": "≥3",
         "fix": "在关键判断处插入'你品你细品'式检验提问", "ok": False},
        {"metric": "时长", "actual": "0.8min", "promised": "8~10min",
         "fix": "扩充每个概念的类比与展开", "ok": False},
    ]}


# 模拟"小米双通道都挂了"：call_llm 一律返回 None
def mock_call_llm_none(*a, **k):
    return None


def main():
    runner.ops.call_llm = mock_call_llm_none
    styles.get_preset("zhangxuefeng")  # 触发预设加载，确保无异常

    print("=" * 60)
    print("CASE 1: offline_engine=False, 小米缺失 → 必须硬报错")
    print("=" * 60)
    cfg = fake_cfg(offline_engine=False)
    raised = None
    try:
        runner.build_improved_script(cfg, "zhangxuefeng", 3, fake_segs(),
                                     fake_gap(), {}, {}, "", "")
    except RuntimeError as e:
        raised = e
    if raised is None:
        print("❌ 失败：未报错（发生了静默降级，违反规则）")
        sys.exit(1)
    print(f"✅ 正确报错：{raised}")

    print()
    print("=" * 60)
    print("CASE 2: offline_engine=True, 小米缺失 → 必须产出真·改进稿(ch3)")
    print("=" * 60)
    cfg = fake_cfg(offline_engine=True)
    res = runner.build_improved_script(cfg, "zhangxuefeng", 3, fake_segs(),
                                       fake_gap(), {}, {}, "", "")
    text = res["text"]
    assert res["used_llm"] is False, "used_llm 必须为 False"
    assert "必须修掉的问题" in text, "ch3 改进稿必须钉入评估差距"
    assert len(text) > 200, "ch3 产出过短，不像成稿"
    print(f"✅ ch3 产出 {len(text)} 字符，含'必须修掉的问题'段")
    print("---- 产出前 400 字预览 ----")
    print(text[:400])

    print()
    print("=" * 60)
    print("CASE 3: 确认链路 A 的报错分支与链路 B 同构（静态检查）")
    print("=" * 60)
    src = open(os.path.join(ROOT, "webapp/core/runner.py"), encoding="utf-8").read()
    assert 'raise RuntimeError(\n                "小米 LLM 不可用' in src, \
        "run_video_to_script 未包含'小米不可用→报错'分支"
    assert "build_fallback_package" not in src, "静默素材包函数仍存在，未彻底移除"
    print("✅ run_video_to_script 已含硬报错分支，且 build_fallback_package 已删除")

    print()
    print("ALL PASS —— 策略：有小米→用小米；无小米→默认报错（offline_engine 显式开启才退 ch3）")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        sys.exit(2)
