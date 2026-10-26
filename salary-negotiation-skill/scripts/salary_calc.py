#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""加薪涨幅三锚定价计算器。

输入用户实况，输出开口锚 / 目标锚 / 底线锚三档，以及匹配检查结论。
所有数值可复算：同输入同输出，无随机、无外部依赖、不联网。

三锚的成立条件：
  锚 A 带宽分位  —— 需同时给 --band-min/mid/max
  锚 B 增量折算  —— 需给 --increment，且必须是可持续增量（一次性收益走奖金，不支撑底薪）
  锚 C 替换倒推  —— 需给 --market（外部同岗市场价）；没有市场价就无法算替换成本

用法：
    python salary_calc.py --current 20000 --scene role --firm private \
        --band-min 15000 --band-mid 22000 --band-max 28000 \
        --increment 600000 --market 24000
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

WALK_FACTOR = 0.65        # 底线锚起点 = 目标 × 0.65
WALK_FLOOR_MULT = 1.03    # 底线不得低于现有的 1.03 倍（低于此值不值得消耗一次机会）
WALK_CEIL_FACTOR = 0.85   # 底线不得高于目标的 0.85 倍（高于此值就没有谈判空间）
RECRUIT_RATE = 0.20       # 招聘一次性成本 ≈ 市场年薪的 20%（猎头/面试/流程）
RAMP_MONTHS = 3           # 上手期产能折损，默认 3 个月
AUTH_MANAGER_PCT = 8      # 直属主管常规调薪授权上限（常见区间 5%—8%，以公司授权表为准）


def _round100(x):
    """薪资取整到百位，避免输出 21347 这类假精度。"""
    return int(round(x / 100.0)) * 100


def compute(current, scene="role", firm="unknown",
            band_min=None, band_mid=None, band_max=None,
            increment=None, share=None, target_pct=None,
            market=None, increment_type="recurring", ramp_months=RAMP_MONTHS):
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
        elif increment_type == "oneoff":
            notes.append("一次性增量不折算进底薪：一次性的钱走项目奖/一次性奖励，"
                         "底薪只承接可持续增量。锚 B 已跳过")
        else:
            s = SCENE_SHARE[scene] if share is None else share
            if not (0 < s <= 0.5):
                warn.append("分享比例应在 0 到 0.5 之间，已回退到由头默认值")
                s = SCENE_SHARE[scene]
            target_b = current + (increment * s) / 12.0

    # ---- 锚 C 替换成本倒推（必须依赖外部市场价，不能只用现有薪资推算）
    target_c = None
    if market is not None:
        if market <= 0:
            warn.append("市场价必须为正数，锚 C 跳过")
        else:
            if not (0 < ramp_months <= 12):
                warn.append("上手折损月数应在 1 到 12 之间，已回退到 %d" % RAMP_MONTHS)
                ramp_months = RAMP_MONTHS
            # 替换一个同岗的人要花的年成本 = 市场年薪 + 招聘一次性成本 + 上手期折损
            replace_cost = market * 12 * (1 + RECRUIT_RATE) + market * ramp_months
            # 公司为避免这笔支出，理性上最多愿付其中一半来留人
            target_c = current + (replace_cost - current * 12) * 0.5 / 12.0
            if market < current:
                notes.append("市场价 %.0f 低于你现薪 %.0f：你在外部拿不到现在的价，"
                             "「市场对标」这条路不成立。锚 C 仍按替换成本计算，"
                             "但它衡量的是公司换人的代价，不是你的市场价" % (market, current))
            if target_c <= current:
                notes.append("替换你比留你更便宜，「关键人才保留」由头不成立，锚 C 已跳过")
                target_c = None
            elif target_c > current * 1.60:
                warn.append("锚 C 推算值偏高（超出现有 60%%），说明市场价输入可能偏高，已按 60%% 封顶")
                target_c = current * 1.60
    else:
        notes.append("未提供外部市场价，锚 C 无法计算（替换成本必须由市场价推出，"
                     "用现有薪资乘倍数会得到与个体无关的伪锚）")

    # ---- 加权
    anchors = []
    if has_band and target_a is not None:
        anchors.append(("A", target_a, 0.5))
    if target_b is not None:
        anchors.append(("B", target_b, 0.3 if (has_band and target_a is not None) else 0.5))
    if target_c is not None:
        anchors.append(("C", target_c, 0.2 if (has_band and target_a is not None) else 0.5))

    usable = [(k, v, w) for k, v, w in anchors if v is not None]
    used = []
    if not usable:
        # 三锚都不可用时，只有「用户自报期望」能单独支撑一次计算
        if target_pct is None or target_pct <= 0:
            return None, ["三锚全部不可用：请至少提供带宽、可持续增量、市场价之一"]
        target = current * (1 + target_pct / 100.0)
        notes.append("三锚均不可用，仅按你自报的期望涨幅计算；补带宽/增量/市场价后可重算")
    else:
        wsum = sum(w for _, _, w in usable)
        target = sum(v * w for _, v, w in usable) / wsum
        used = [k for k, _, _ in usable]
        if len(used) == 1:
            notes.append("仅单锚可用（锚 %s），区间偏保守，建议补一项信息后重算" % used[0])

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

    # ---- 开口锚：不得超出带宽上限（超出上限的报价在体系内不受理）
    ask_mult = FIRM_ASK[firm]
    ask = _round100(target * ask_mult)
    if has_band and ask > band_max:
        ask = _round100(float(band_max))
        notes.append("开口锚已按带宽上限 %.0f 收敛：超出岗位上限的报价在体系内不会被受理，"
                     "超出部分应转结构收益" % band_max)

    # ---- 底线锚：三条约束同时成立才算数
    #   ① 起点 = 目标 × 0.65
    #   ② 带宽已知时想靠中位，但不能吃掉谈判空间
    #   ③ 硬边界：不低于现有的 1.03 倍，不高于目标的 0.85 倍
    walk = target * WALK_FACTOR
    if has_band:
        walk = max(walk, min(float(band_mid), target * WALK_CEIL_FACTOR))
    walk = max(walk, current * WALK_FLOOR_MULT)
    walk = min(walk, target * WALK_CEIL_FACTOR)
    if walk <= current:
        notes.append("目标与现有薪资过近，构不成谈判空间；先调高目标或换由头")
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
    if scene == "market":
        warn.append("市场对标不宜单独作主由头：公司最省力的回应是「那你出去看看」。"
                    "建议降为辅由头，主由头换成职责扩展或关键人才保留")

    # ---- 授权联动：超过直属主管授权就必须往上走流程
    auth_note = None
    if pct > AUTH_MANAGER_PCT:
        auth_note = ("目标涨幅 %.1f%% 超过直属主管常规授权（常见 5%%—8%%），这件事他一个人批不了。"
                     "材料要做成一页可直接转发给上级的版本，并主动帮他把提报理由写好——"
                     "他要往上走流程，材料越完整他越愿意提" % pct)
        notes.append(auth_note)

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
            "increment_type": increment_type,
            "market": market,
            "ramp_months": ramp_months,
        },
        "anchors": {
            "A_band": _round100(target_a) if target_a is not None else None,
            "B_increment": _round100(target_b) if target_b is not None else None,
            "C_replace": _round100(target_c) if target_c is not None else None,
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
        "needs_escalation": pct > AUTH_MANAGER_PCT,
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
    L.append("锚 B 增量折算 %s" % (format(a["B_increment"], ",") if a["B_increment"] else "不适用"))
    L.append("锚 C 替换倒推 %s" % (format(a["C_replace"], ",") if a["C_replace"] else "不适用（缺市场价）"))
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
    if r["needs_escalation"]:
        L.append("流程提示      超出直属主管授权，需往上走；材料备成一页可转发版")
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
    p.add_argument("--increment", type=float, default=None,
                   help="你带来的年化增量收益（元），仅限可持续增量")
    p.add_argument("--increment-type", default="recurring", choices=["recurring", "oneoff"],
                   help="recurring 可持续（进底薪）/ oneoff 一次性（走奖金），默认 recurring")
    p.add_argument("--share", type=float, default=None, help="增量分享比例，0-0.5")
    p.add_argument("--market", type=float, default=None,
                   help="外部同岗市场月薪（元）。锚 C 必需，缺则锚 C 不参与")
    p.add_argument("--ramp-months", type=float, default=RAMP_MONTHS,
                   help="上手期产能折损月数，默认 3（区间 1-12）")
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
        target_pct=args.target_pct, market=args.market,
        increment_type=args.increment_type, ramp_months=args.ramp_months,
    )
    if r is None:
        print("错误：无法计算，" + "；".join(_), file=sys.stderr)
        return 2

    print(json.dumps(r, ensure_ascii=False, indent=2) if args.json else render(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
