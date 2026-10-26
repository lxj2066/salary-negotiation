#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""加薪谈判输出预检：交付前跑一遍，拦五类硬伤。

拦什么：
  1. 禁用句式（卖惨 / 比较 / 威胁 / 年限）
  2. 未填占位符（[待补充] / { } / ___）
  3. 无出处数字（给了 --facts 才严格核对，否则只列清单）
  4. 邮件超长（默认 250 字）
  5. 六件套缺件

扫描范围：邮件代码块做全量检查；全文做缺件与占位符检查。
含「错误版」「绝对不说」「禁用」「反例」的行会被跳过——那些地方本就该出现错误示例。

用法：
    python preflight.py --file out.md
    python preflight.py --file out.md --facts 20000,15000,22000,28000 --json
"""

import argparse
import json
import re
import sys

# ---------------------------------------------------------------- 检测规则
BANNED = [
    ("卖惨", r"压力大|房贷|养家|生活困难|没有功劳也有苦劳|我太辛苦|活不下去"),
    ("比较", r"同事都|同龄人|别人都|凭什么他|新来的都比我|跟我同岗的"),
    ("威胁", r"不给.{0,4}就走|不涨.{0,4}就走|否则我就|我就离职|不批我就|只能考虑离开"),
    ("年限", r"干了.{0,3}[0-9一二三四五六七八九十]+.{0,2}年|在公司.{0,3}[0-9一二三四五六七八九十]+.{0,2}年|待了.{0,3}[0-9一二三四五六七八九十]+.{0,2}年|老员工|熬了.{0,3}[0-9一二三四五六七八九十]+"),
]

SKIP_MARKS = ("错误版", "绝对不说", "禁用", "反例", "为什么废", "不要写", "禁止")

PLACEHOLDER = re.compile(r"\[待补充[^\]]*\]|\{\s*[^}\n]{0,20}\s*\}|_{3,}")

SIX_PARTS = [
    ("场景与由头判断", ("由头",)),
    ("预沟通脚本", ("预沟通", "口头版")),
    ("正式邮件", ("主题：", "主题:")),
    ("面谈话术", ("话术", "错误版")),
    ("结构收益备选", ("结构收益",)),
    ("时机与复谈日历", ("日历", "时机")),
]

NUM_RE = re.compile(r"(\d+(?:,\d{3})*(?:\.\d+)?%?)\s*([分钟小时天周个月人条个次年月日点])?")


def _norm_num(s):
    return s.replace(",", "").rstrip("%")


def _keep_num(tok):
    """只保留需要核对出处的数字：金额、百分比、小数。
    跳过年份（1900-2100 整数）与单字符序号（Q3、第3条），避免误报。"""
    raw = _norm_num(tok)
    if not raw:
        return False
    if "%" in tok or "." in raw:
        return True
    if not raw.isdigit():
        return False
    if 1900 <= int(raw) <= 2100:
        return False
    return len(raw) >= 2


def read_text(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def split_email_blocks(text):
    """提取含「主题」的代码块，返回 [(行号起, 内容)]。"""
    blocks, cur, start, inside = [], [], 0, False
    for i, ln in enumerate(text.splitlines(), 1):
        if ln.strip().startswith("```"):
            if inside:
                body = "\n".join(cur)
                if "主题" in body:
                    blocks.append((start, body))
                cur, inside = [], False
            else:
                cur, start, inside = [], i, True
            continue
        if inside:
            cur.append(ln)
    return blocks


def check(text, facts=None, max_email_chars=250, strict=False):
    issues, notes = [], []
    lines = text.splitlines()

    if isinstance(facts, str):
        facts = [x.strip() for x in facts.split(",") if x.strip()]

    # ---- 六件套
    for name, keys in SIX_PARTS:
        if not any(k in text for k in keys):
            issues.append(("缺件", "未找到「%s」" % name))

    # ---- 禁用句式（全文扫，跳过示例行；strict 时连示例行也扫）
    for i, ln in enumerate(lines, 1):
        if not strict and any(m in ln for m in SKIP_MARKS):
            continue
        for tag, pat in BANNED:
            for m in re.finditer(pat, ln):
                issues.append((tag, "第 %d 行含%s句式：「%s」" % (i, tag, m.group(0))))

    # ---- 占位符
    for i, ln in enumerate(lines, 1):
        if any(m in ln for m in SKIP_MARKS):
            continue
        if PLACEHOLDER.search(ln):
            issues.append(("占位符", "第 %d 行仍有未填占位符：%s" % (i, ln.strip()[:60])))

    # ---- 邮件块检查
    emails = split_email_blocks(text)
    if not emails:
        issues.append(("邮件", "没有找到含「主题」的邮件代码块"))

    for start, body in emails:
        body_lines = [l for l in body.splitlines()
                      if not any(m in l for m in SKIP_MARKS)]
        clean = "\n".join(body_lines)

        # 字数（去主题行、去空白）
        no_subj = "\n".join(l for l in body_lines if not re.match(r"^\s*主题[:：]", l))
        n_chars = len(re.sub(r"\s+", "", no_subj))
        if n_chars > max_email_chars:
            issues.append(("超长", "邮件正文 %d 字，超过 %d 字上限（代码块起于第 %d 行）"
                           % (n_chars, max_email_chars, start)))
        else:
            notes.append("邮件正文 %d 字，在 %d 字以内" % (n_chars, max_email_chars))

        # 数字出处
        raw_nums = [m.group(1) for m in NUM_RE.finditer(clean) if not m.group(2)]
        nums = [_norm_num(x) for x in raw_nums if _keep_num(x)]
        if nums:
            if facts:
                fset = {_norm_num(x) for x in facts}
                unknown = [n for n in nums if n not in fset]
                if unknown:
                    issues.append(("数字", "邮件含无出处数字：%s（未在 --facts 中）"
                                   % "、".join(unknown[:6])))
                else:
                    notes.append("邮件内 %d 个数字全部可追溯到 --facts" % len(nums))
            else:
                notes.append("邮件内数字待你核对出处：%s" % "、".join(nums[:8]))

    return issues, notes


def main(argv=None):
    p = argparse.ArgumentParser(description="加薪谈判输出预检")
    p.add_argument("--file", required=True, help="要检查的 md/txt 文件")
    p.add_argument("--facts", default=None,
                   help="用户提供过的数字，逗号分隔。给了才严格核对邮件内数字")
    p.add_argument("--max-email-chars", type=int, default=250, help="邮件正文上限，默认 250 字")
    p.add_argument("--strict", action="store_true", help="全文扫禁用句式，不只看邮件")
    p.add_argument("--json", action="store_true", help="输出 JSON")
    args = p.parse_args(argv)

    try:
        text = read_text(args.file)
    except OSError as e:
        print("错误：读不到文件 %s（%s）" % (args.file, e), file=sys.stderr)
        return 2

    facts = [x.strip() for x in args.facts.split(",")] if args.facts else None
    issues, notes = check(text, facts, args.max_email_chars, args.strict)

    ok = not issues
    if args.json:
        print(json.dumps({
            "file": args.file,
            "verdict": "PASS" if ok else "FAIL",
            "issue_count": len(issues),
            "issues": [{"type": t, "detail": d} for t, d in issues],
            "notes": notes,
        }, ensure_ascii=False, indent=2))
    else:
        print("=" * 52)
        print("预检结果：%s" % ("PASS" if ok else "FAIL（%d 项）" % len(issues)))
        print("=" * 52)
        for t, d in issues:
            print("  [%s] %s" % (t, d))
        if notes:
            print("-" * 52)
            for n in notes:
                print("  · " + n)
        if not ok:
            print("-" * 52)
            print("改完必须重跑本脚本，不许口头确认。")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
