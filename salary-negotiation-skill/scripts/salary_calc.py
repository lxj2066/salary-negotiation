#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""加薪涨幅三锚定价计算器。

输入用户实况，输出开口锚 / 目标锚 / 底线锚三档，以及匹配检查结论。
所有数值可复算：同输入同输出，无随机、无外部依赖、不联网。

用法：
    python salary_calc.py --current 20000 --scene contribution \
        --band-min 15000 --band-mid 22000 --band-max 28000 --firm private

    python salary_calc.py --current 20000 --increment 600000 --json
"""

import argparse
import json
import sys

# ---------------------------------------------------------------- 经验参数表
# 涨幅经验区间（%）：该由头下公司通常能接受的幅度，超出即提示换由头
SCENE_RANGE = {
    "band":         (8, 20),    # 职级带宽内校准
    "role":         (10, 25),   # 职责扩展 / 岗位价值重估
    "contribution": (10, 25),   # 增量贡献
    "retention":    (15, 30),   # 关键人才保留
    "window":       (5, 10),    # 窗口内优先级争取
    "market":       (8, 12),    # 市场对标（不建议单独主谈）
}

SCENE_NAME = {
    "band": "职级带宽内校准",
    "role": "职责扩展/岗位价值重估",
    "contribution": "增量贡献/超额产出",
    "retention": "关键人才保留/稀缺性",
    "window": "窗口内优先级争取",
    "market": "市场对标（不建议单独主谈）",
}

# 增量分享比例（经验区间中值）
SCENE_SHARE = {
    "band": 0.15,
    "role": 0.18,
    "contribution": 0.20,
    "retention": 0.20,
    "window": 0.12,
    "market": 0.15,
}

# 开口倍数：虚高空间按企业类型收紧
FIRM_ASK = {
    "private": 1.18,   # 民企/成长型：决策链短会直接砍价
    "mnc": 1.10,       # 外企/成熟体系：走带宽逻辑，虚高判不专业
    "soe": 1.05,       # 国企/体系内：总额管控，虚高无效
    "unknown": 1.10,   # 不确定：按流程严格型保守处理
}

FIRM_NAME = {
    "private": "民企/成长型",
    "mnc": "外企/成熟体系",
    "soe": "国企/体系内",
    "unknown": "不确定（按流程严格型）",
}

WALK_FACTOR = 0.65      # 底线锚 = 目标 × 0.65
REPLACE_MONTHS = 7.5    # 替换成本默认 7.5 个月薪资（区间 6-9）


def _round100(x):
    """薪资取整到百位，避免输出 21347 这类假精度。"""
    return int(round(x / 100.0)) * 100


def compute(current, scene="role", firm="unknown",
            band_min=None, band_mid=None, band_max=None,
            increment=None, share=None, replace_months=REPLACE_MONTHS,
            target_pct=None):
    """返回 (result_dict, warnings_list)。"""
    warn = []
    notes = []

    has_band = all(v is not None for v in (band_min, band_mid, band_max))
    if any(v is not None for v in (band_min, band_mid, band_max)) and not has_band:
        warn.append("带宽三项未给全，锚 A 跳过；请同时提供 --band-min/--band-mid/--band-max")
        has_band = False

    if has_band and not (band_min < band_mid < band_max):
        warn.append("带宽不满足 下限<中位<上限，锚 A 跳过")
        has_band = False

    # ---- 锚 A 内部带宽分位
    target_a = None
    pos = None
    if has_band:
        pos = (current - band_min) / float(band_max - band_min)
        if pos < 0.50:
            target_a = band_mid * 1.05
        elif pos < 0.75:
            target_a = current * 1.10
        else:
            notes.append("当前位置已达带宽 75 分位以上，底薪空间已尽，应转结构收益或谈职级")
        if target_a is not None and target_a > band_max:
            warn.append("锚 A 计算值超出带宽上限 %.0f，已按上限截断" % band_max)
            target_a = float(band_max)

    # ---- 锚 B 增量产出折算
    target_b = None
    if increment is not None:
        if increment <= 0:
            warn.append("增量收益必须为正数，锚 B 跳过")
        else:
            s = SCENE_SHARE[scene] if share is None else share
            if not (0 < s <= 0.5):
                warn.append("分享比例应在 0 到 0.5 之间，已回退到由头默认值")
                s = SCENE_SHARE[scene]
            target_b = current + (increment * s) / 12.0

    # ---- 锚 C 替换成本倒推
    if not (1 <= replace_months <= 24):
        warn.append("替换成本月数应在 1 到 24 之间，已回退到 7.5")
        replace_months = REPLACE_MONTHS
    target_c = current * (1 + replace_months / 12.0 * 0.5)

    # ---- 加权
    anchors = []
    if has_band and target_a is not None:
        anchors.append(("A", target_a, 0.5))
    anchors.append(("B", target_b, 0.3 if (has_band and target_a is not None) else 0.5))
    anchors.append(("C", target_c, 0.2 if (has_band and target_a is not None) else 0.5))

    usable = [(k, v, w) for k, v, w in anchors if v is not None]
    if not usable:
        return None, ["三锚全部不可用：请至少提供带宽、增量收益或有效现有薪资"]

    wsum = sum(w for _, _, w in usable)
    target = sum(v * w for _, v, w in usable) / wsum
    used = [k for k, _, _ in usable]
    if len(used) == 1:
        notes.append("仅单锚可用（锚 %s），区间偏保守" % used[0])

    # ---- 用户自报期望覆盖目标
    user_pct = None
    lo, hi = SCENE_RANGE[scene]
    if target_pct is not None:
        if target_pct <= 0:
            warn.append("期望涨幅必须为正数，已忽略")
        else:
            user_pct = target_pct
            target = current * (1 + target_pct / 100.0)
            notes.append("目标锚采用你给的期望涨幅 %.1f%%，三锚值仅作对照" % target_pct)
    else:
        # 未自报期望时，按该由头经验上限收敛，避免模型给出永远超标的数字
        cap = current * (1 + hi / 100.0)
        if target > cap:
            notes.append("加权值 %.0f 超出该由头经验上限 %d%%，已收敛到 %.0f；"
                         "若有更强依据，用 --target-pct 直接指定" % (target, hi, cap))
            target = cap
        if has_band and target > band_max:
            notes.append("目标超出带宽上限，已按上限 %.0f 收敛；超出部分应转结构收益或谈职级"
                         % band_max)
            target = float(band_max)

    target = _round100(target)

    # ---- 三档
    ask_mult = FIRM_ASK[firm]
    ask = _round100(target * ask_mult)
    walk = target * WALK_FACTOR

    # 底线锚的带宽约束：不低于中位，但不得吃掉全部谈判空间（上限 target × 0.90）
    if has_band:
        if target <= band_mid:
            notes.append("目标锚未达带宽中位，本次谈成仍处低位，建议上调目标或改由头")
            walk = min(float(target), max(walk, float(band_min)))
        elif walk < band_mid:
            walk = min(float(band_mid), target * 0.90)
    if has_band and walk < band_min:
        warn.append("底线锚低于带宽下限，已按带宽下限截断")
        walk = float(band_min)
    walk = _round100(walk)

    # ---- 匹配检查
    pct = (target - current) / float(current) * 100.0
    if pct > hi:
        warn.append("目标涨幅 %.1f%% 超出「%s」经验上限 %d%%，建议换更强由头或把超出部分转结构收益"
                    % (pct, SCENE_NAME[scene], hi))
    if pct < lo:
        notes.append("目标涨幅 %.1f%% 低于该由头经验下限 %d%%，可能低估了自己的位置" % (pct, lo))
    if pct < 3:
        warn.append("涨幅低于 3%%，通常不值得消耗一次申请机会")
    if user_pct is not None and user_pct > hi * 1.5:
        warn.append("你的期望 %.1f%% 超过该由头上限的 1.5 倍，被拒概率高且会影响下次复评，建议先调预期"
                    % user_pct)
    if has_band and target > band_max:
        warn.append("目标锚超出带宽上限，这是职级问题不是加薪问题，应谈晋升")

    gap = target - walk

    result = {
        "input": {
            "current": current,
            "scene": scene,
            "scene_name": SCENE_NAME[scene],
            "firm": firm,
            "firm_name": FIRM_NAME[firm],
            "band": [band_min, band_mid, band_max] if has_band else None,
            "increment": increment,
            "replace_months": replace_months,
        },
        "anchors": {
            "A_band": _round100(target_a) if target_a is not None else None,
            "B_increment": _round100(target_b) if target_b is not None else None,
            "C_replace": _round100(target_c),
            "used": used,
            "position_pct": round(pos * 100, 1) if pos is not None else None,
        },
        "target": target,
        "ask": ask,
        "walk_away": walk,
        "raise_pct": round(pct, 1),
        "gap_to_fill": gap,
        "annual_gap": gap * 12,
        "scene_range_pct": [lo, hi],
        "notes": notes,
        "warnings": warn,
    }
    return result, warn


def render(r):
    """人类可读输出。"""
    i, a = r["input"], r["anchors"]
    L = []
    L.append("=" * 52)
    L.append("加薪定价结果")
    L.append("=" * 52)
    L.append("现有月薪      %s 元" % format(int(i["current"]), ","))
    L.append("主由头        %s" % i["scene_name"])
    L.append("企业类型      %s" % i["firm_name"])
    if i["band"]:
        L.append("带宽          %s / %s / %s" % tuple(format(int(x), ",") for x in i["band"]))
        L.append("当前位置      %.1f 分位" % a["position_pct"])
    else:
        L.append("带宽          未提供（锚 A 跳过）")
    L.append("-" * 52)
    L.append("锚 A 带宽校准 %s" % (format(a["A_band"], ",") if a["A_band"] else "不适用"))
    L.append("锚 B 增量折算 %s" % (format(a["B_increment"], ",") if a["B_increment"] else "未提供增量"))
    L.append("锚 C 替换倒推 %s  （上限参考，非建议值）" % format(a["C_replace"], ","))
    L.append("参与加权      %s" % " + ".join(a["used"]))
    L.append("-" * 52)
    L.append("开口锚 Ask    %s 元   ← 第一次报这个数" % format(r["ask"], ","))
    L.append("目标锚 Target %s 元   ← 心里真实目标，不主动说" % format(r["target"], ","))
    L.append("底线锚 Walk   %s 元   ← 低于此值改谈结构收益" % format(r["walk_away"], ","))
    L.append("-" * 52)
    L.append("目标涨幅      %.1f%%   该由头经验区间 %d%%—%d%%"
             % (r["raise_pct"], r["scene_range_pct"][0], r["scene_range_pct"][1]))
    L.append("结构收益缺口  %s 元/月（%s 元/年）"
             % (format(r["gap_to_fill"], ","), format(r["annual_gap"], ",")))
    L.append("=" * 52)
    if r["notes"]:
        L.append("提示：")
        for n in r["notes"]:
            L.append("  · " + n)
    if r["warnings"]:
        L.append("警告：")
        for w in r["warnings"]:
            L.append("  ! " + w)
    if not r["notes"] and not r["warnings"]:
        L.append("无异常。所有数字按你给的输入计算，公司带宽以实际为准。")
    return "\n".join(L)


def main(argv=None):
    p = argparse.ArgumentParser(description="加薪涨幅三锚定价计算器")
    p.add_argument("--current", type=float, required=True, help="当前月薪（元）")
    p.add_argument("--scene", default="role", choices=sorted(SCENE_RANGE),
                   help="主由头类型，默认 role")
    p.add_argument("--firm", default="unknown", choices=sorted(FIRM_ASK),
                   help="企业类型，默认 unknown")
    p.add_argument("--band-min", type=float, default=None, help="职级带宽下限")
    p.add_argument("--band-mid", type=float, default=None, help="职级带宽中位")
    p.add_argument("--band-max", type=float, default=None, help="职级带宽上限")
    p.add_argument("--increment", type=float, default=None, help="你带来的年化增量收益（元）")
    p.add_argument("--share", type=float, default=None, help="增量分享比例，0-0.5")
    p.add_argument("--replace-months", type=float, default=REPLACE_MONTHS,
                   help="替换成本月数，默认 7.5（区间 6-9）")
    p.add_argument("--target-pct", type=float, default=None, help="你自己的期望涨幅（%%）")
    p.add_argument("--json", action="store_true", help="输出 JSON")
    args = p.parse_args(argv)

    if args.current <= 0:
        print("错误：--current 必须为正数", file=sys.stderr)
        return 2

    r, _ = compute(
        current=args.current, scene=args.scene, firm=args.firm,
        band_min=args.band_min, band_mid=args.band_mid, band_max=args.band_max,
        increment=args.increment, share=args.share,
        replace_months=args.replace_months, target_pct=args.target_pct,
    )
    if r is None:
        print("错误：无法计算，" + "；".join(_), file=sys.stderr)
        return 2

    print(json.dumps(r, ensure_ascii=False, indent=2) if args.json else render(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
