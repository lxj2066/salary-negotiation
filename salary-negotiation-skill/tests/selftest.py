#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""脚本级回归：定价与预检的确定性断言。

跑法：
    python tests/selftest.py

全部 PASS 退出码 0，任一 FAIL 退出码 1。
断言只增不改口径：要让断言变绿，先改脚本，不许改断言。
"""

import io
import os
import sys
import tempfile
import contextlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import salary_calc  # noqa: E402
import preflight    # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (("  -> " + detail) if detail and not cond else ""))


# ------------------------------------------------------------------ 定价用例
def test_pricing():
    print("[定价]")

    r, _ = salary_calc.compute(20000, "role", "private",
                               band_min=15000, band_mid=22000, band_max=28000,
                               increment=600000)
    ok("三锚全给：目标被 25% 上限收敛", r["target"] == 25000, str(r["target"]))
    ok("三锚全给：开口锚 = 目标×1.18", r["ask"] == 29500, str(r["ask"]))
    ok("三锚全给：底线锚 = 带宽中位", r["walk_away"] == 22000, str(r["walk_away"]))
    ok("三锚全给：三锚均参与加权", r["anchors"]["used"] == ["A", "B", "C"], str(r["anchors"]["used"]))
    ok("三锚全给：缺口为正", r["gap_to_fill"] > 0, str(r["gap_to_fill"]))

    r2, _ = salary_calc.compute(20000, "retention", "mnc")
    ok("无带宽单锚：目标收敛到 30% 上限", r2["target"] == 26000, str(r2["target"]))
    ok("无带宽单锚：标注单锚", any("单锚" in n for n in r2["notes"]), str(r2["notes"]))

    r3, w3 = salary_calc.compute(27000, "role", "mnc",
                                 band_min=15000, band_mid=22000, band_max=28000)
    ok("接近上限：目标不超过带宽上限", r3["target"] <= 28000, str(r3["target"]))
    ok("接近上限：提示转职级或结构收益", any("职级" in x or "结构收益" in x for x in r3["notes"] + w3),
       str(r3["notes"] + w3))

    r4, _ = salary_calc.compute(20000, "role", "mnc", target_pct=10)
    ok("自报期望覆盖目标", r4["target"] == 22000, str(r4["target"]))
    ok("自报期望：涨幅计算正确", r4["raise_pct"] == 10.0, str(r4["raise_pct"]))

    r5, w5 = salary_calc.compute(20000, "role", "unknown",
                                 band_min=28000, band_mid=22000, band_max=15000)
    ok("带宽倒挂：锚 A 跳过并告警", r5["anchors"]["A_band"] is None and any("带宽" in x for x in w5),
       str(w5))

    r6, w6 = salary_calc.compute(20000, "market", "unknown", target_pct=40)
    ok("期望远超上限：给出高被拒告警", any("被拒" in x for x in w6), str(w6))

    # 非法输入走 main，验证退出码
    for args in (["--current", "-1"], ["--current", "abc"]):
        with contextlib.redirect_stderr(io.StringIO()):
            try:
                code = salary_calc.main(args)
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 2
        ok("非法输入 %s 返回非零" % args[1], code not in (0, None), str(code))


# ------------------------------------------------------------------ 预检用例
CLEAN = """## 一、场景与由头判断
场景：里程碑后，主由头为职责扩展。

## 二、预沟通脚本（口头版）
约 20 分钟，先问预算与岗位定位。

## 三、正式邮件
```text
主题：关于薪酬调整的沟通 — 张三

王经理：

去年我负责投放线，把获客成本从 120 元降到 90 元。
从 2026 年 Q3 起我接手私域与复购，团队未增编。
希望月薪从 20000 元调整到 25000 元。
方便的话本周聊 20 分钟。
```

## 四、面谈话术
错误版：「您看能涨多少」；正确版：给区间并请对方反馈。

## 五、结构收益备选
奖金系数由 1.0 提到 1.3，并写进考核表。

## 六、时机与复谈日历
D+3 发确认邮件，D+30 进展沟通，D+60 正式复评。
"""

DIRTY = """## 一、场景与由头判断
主由头：我在公司干了八年。

## 三、正式邮件
```text
主题：加薪申请

领导，我最近房贷压力大，生活不容易。
同事都比我高，不给我涨我就走了。
希望涨到 [待补充：目标薪资]。
```
"""


def test_preflight():
    print("[预检]")

    def run(text, facts=None):
        fd, p = tempfile.mkstemp(suffix=".md")
        os.close(fd)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        try:
            issues, notes = preflight.check(text, facts)
            code = preflight.main(["--file", p] + (["--facts", facts] if facts else []))
        finally:
            os.remove(p)
        return issues, notes, code

    with contextlib.redirect_stdout(io.StringIO()):
        issues, notes, code = run(CLEAN)
    ok("干净样例：预检 PASS", code == 0 and not issues, str(issues))
    ok("干净样例：识别到邮件字数", any("邮件正文" in n for n in notes), str(notes))

    with contextlib.redirect_stdout(io.StringIO()):
        issues2, _, code2 = run(DIRTY)
    tags = {t for t, _ in issues2}
    ok("脏样例：预检 FAIL", code2 == 1, str(code2))
    ok("脏样例：命中缺件", "缺件" in tags, str(tags))
    ok("脏样例：命中占位符", "占位符" in tags, str(tags))
    ok("脏样例：命中年限", "年限" in tags, str(tags))
    ok("脏样例：命中卖惨", "卖惨" in tags, str(tags))
    ok("脏样例：命中威胁", "威胁" in tags, str(tags))
    ok("脏样例：命中比较", "比较" in tags, str(tags))

    # 数字出处：给了 facts 才严格核对
    with contextlib.redirect_stdout(io.StringIO()):
        issues3, _, code3 = run(CLEAN, facts="120,90,20000,25000")
    ok("facts 覆盖全部数字时无数字告警", not any(t == "数字" for t, _ in issues3), str(issues3))

    with contextlib.redirect_stdout(io.StringIO()):
        issues4, _, code4 = run(CLEAN, facts="20000")
    ok("facts 缺数字时报警", any(t == "数字" for t, _ in issues4), str(issues4))

    # 超长邮件
    long_mail = "## 一、场景与由头判断\n## 二、预沟通脚本（口头版）\n## 四、面谈话术\n" \
                "## 五、结构收益备选\n## 六、时机与复谈日历\n```text\n主题：X\n" + ("字" * 300) + "\n```\n"
    with contextlib.redirect_stdout(io.StringIO()):
        issues5, _, code5 = run(long_mail)
    ok("超长邮件被拦", any(t == "超长" for t, _ in issues5), str(issues5))

    # 错误版话术不应被误报
    sample = CLEAN + "\n错误版：「不给我涨我就走了」「我房贷压力大」「我干了八年」\n"
    with contextlib.redirect_stdout(io.StringIO()):
        issues6, _, _ = run(sample)
    ok("错误版示例行不被误报", not any(t in ("威胁", "卖惨", "年限") for t, _ in issues6), str(issues6))


def main():
    print("=" * 52)
    print("salary-negotiation-skill 脚本回归")
    print("=" * 52)
    test_pricing()
    test_preflight()
    print("-" * 52)
    print("PASS %d / FAIL %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  未过：" + f)
        return 1
    print("全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
