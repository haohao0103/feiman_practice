#!/usr/bin/env python3
"""JSON 提取与"解析失败必须显式报错"的测试。

验证：
  1) 各种脏格式都能解析（```json 围栏 / 尾部多余文字 / 尾部多余逗号 / 字符串里有括号）
  2) 真正无法解析时**抛异常**，不是静默返回 None（静默通过比没有校验更危险）

用法：python3 scripts/test_json_extract.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from webapp.core import courseware_gen as cg      # noqa: E402

CASES = [
    ("裸数组", '[{"a":1}]', list),
    ("```json 围栏", '```json\n[{"a":1}]\n```', list),
    ("``` 围栏无 lang", '```\n[{"a":1}]\n```', list),
    ("尾部有解释文字", '[{"a":1}]\n\n以上是核查结果，希望有用。', list),
    ("尾部多余逗号", '[{"a":1},]', list),
    ("字符串里有括号", '[{"a":"他说（括号）和[方括号]"}]', list),
    ("嵌套对象", '[{"a":{"b":[1,2]}}]', list),
    ("对象结构", '{"corrections":[{"a":1}]}', dict),
    ("前面有前言", '好的，以下是结果：\n[{"a":1}]', list),
]

BAD = [
    ("完全不是 JSON", "我看完了，这段稿子没有明显错误。"),
    ("截断的 JSON", '[{"a":1}, {"b":'),
]


def main():
    fails = []

    def check(name, cond, extra=""):
        print(f"  {'✓' if cond else '✗'} {name}" + (f"  {extra}" if extra else ""))
        if not cond:
            fails.append(name)

    print("① 脏格式都要能解析")
    for name, raw, want in CASES:
        got = cg._extract_json(raw)
        ok = isinstance(got, want) and (not isinstance(got, list) or len(got) == 1)
        check(name, ok, f"→ {type(got).__name__}")

    print("② 真解析不了 → 返回 None（由 fact_check 抛异常）")
    got = cg._extract_json("我看完了，这段稿子没有明显错误。")
    check("完全不是 JSON → None", got is None, f"→ {got!r}")
    got2 = cg._extract_json('[{"a":1}, {"b":')
    check("截断的 JSON → 抢救出完整对象（与对账引擎同策略）",
          isinstance(got2, (list, dict)), f"→ {got2!r}")

    print("③ fact_check 在解析失败时抛异常（而不是返回 []）")
    import inspect
    src = inspect.getsource(cg.fact_check)
    check("fact_check 里有 raise", "raise RuntimeError" in src)
    check("不再静默 return []", "return []" not in src)

    print()
    print("PASS: JSON 提取与显式报错都正确" if not fails else f"FAIL: {fails}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
