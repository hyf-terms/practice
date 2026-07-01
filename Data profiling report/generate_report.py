#!/usr/bin/env python3
"""基于结构化减持公告生成1-2页Markdown数据画像报告。"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path


INDUSTRY_API = "https://push2.eastmoney.com/api/qt/stock/get"


def read_csv(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def parse_amount(text: str) -> float | None:
    """返回公告明确披露的减持金额（元），不根据股数和价格估算。"""
    flat = re.sub(r"\s+", " ", text)
    patterns = [
        r"减持总金额\s*([\d,，]+(?:\.\d+)?)\s*(亿元|万元|元)",
        r"累计减持(?:总)?金额\s*([\d,，]+(?:\.\d+)?)\s*(亿元|万元|元)",
        r"减持金额\s*([\d,，]+(?:\.\d+)?)\s*(亿元|万元|元)",
    ]
    factors = {"元": 1.0, "万元": 10_000.0, "亿元": 100_000_000.0}
    amounts = []
    for pattern in patterns:
        for value, unit in re.findall(pattern, flat):
            amounts.append(float(value.replace(",", "").replace("，", "")) * factors[unit])
    return max(amounts) if amounts else None


def secid(code: str) -> str:
    return ("1." if code.startswith(("5", "6", "9")) else "0.") + code


def fetch_industry(code: str, attempts: int = 4) -> str:
    params = urllib.parse.urlencode({"secid": secid(code), "fields": "f57,f58,f127"})
    request = urllib.request.Request(
        INDUSTRY_API + "?" + params,
        headers={"User-Agent": "Mozilla/5.0 Chrome/126 Safari/537.36", "Referer": "https://quote.eastmoney.com/"},
    )
    error = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
            value = (payload.get("data") or {}).get("f127")
            return value if value and value != "-" else "未分类"
        except Exception as exc:
            error = exc
            time.sleep(0.5 * 2**attempt)
    return f"获取失败:{type(error).__name__}"


def load_industries(codes: list[str], cache_path: Path, workers: int) -> dict[str, str]:
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    missing = [code for code in codes if code not in cache or cache[code].startswith("获取失败")]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(fetch_industry, code): code for code in missing}
        for future in as_completed(futures):
            cache[futures[future]] = future.result()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return cache


def source_counts(db_path: Path) -> tuple[int, int]:
    with sqlite3.connect(db_path) as db:
        total = db.execute("SELECT COUNT(*) FROM announcements").fetchone()[0]
        candidates = db.execute("SELECT COUNT(*) FROM announcements WHERE candidate=1").fetchone()[0]
    return total, candidates


def money(value: float | None) -> str:
    if value is None:
        return "未披露"
    if value >= 100_000_000:
        return f"{value / 100_000_000:.2f}亿元"
    return f"{value / 10_000:.2f}万元"


def percent(numerator: int, denominator: int) -> str:
    return f"{numerator / denominator:.1%}" if denominator else "-"


def build_report(rows: list[dict], raw_total: int, candidate_total: int, industries: dict[str, str], text_cache: Path) -> str:
    monthly = Counter(row["公告日期"][:7] for row in rows)
    monthly_companies: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        monthly_companies[row["公告日期"][:7]].add(row["股票代码"])

    amount_rows = []
    for row in rows:
        text_path = text_cache / f'{row["公告编号"]}.txt'
        amount = parse_amount(text_path.read_text(encoding="utf-8")) if text_path.exists() else None
        if amount is not None:
            amount_rows.append((amount, row))
    amount_rows.sort(key=lambda item: item[0], reverse=True)

    industry_notices = Counter(industries.get(row["股票代码"], "未分类") for row in rows)
    industry_companies: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        industry_companies[industries.get(row["股票代码"], "未分类")].add(row["股票代码"])

    reasons = sum(row["减持原因"] not in ("", "未披露") for row in rows)
    amount_disclosed = len(amount_rows)
    high_confidence = sum(row["解析置信度"] == "高" for row in rows)
    peak_month, peak_count = max(monthly.items(), key=lambda item: item[1])
    top3 = sum(count for _, count in industry_notices.most_common(3))

    lines = [
        "# 中证1000持股5%以上股东减持公告数据画像",
        "",
        f"> 统计区间：2026-03-26至2026-06-25（首尾日均包含）；数据源：东方财富；生成时间：{datetime.now():%Y-%m-%d}。",
        "",
        "## 1. 数据概览",
        "",
        f"共抓取 **{raw_total:,}** 份中证1000成分股公告元数据，经模糊关键词召回 **{candidate_total:,}** 份候选公告；下载并解析公告PDF正文后，确认 **{len(rows):,}** 份与“持股5%以上股东减持”相关。正文确认率为 **{percent(len(rows), candidate_total)}**。",
        "",
        f"其中高置信度记录 **{high_confidence}** 份（{percent(high_confidence, len(rows))}）。公告明确披露减持总金额的记录为 **{amount_disclosed}** 份（{percent(amount_disclosed, len(rows))}），明确披露减持原因的记录为 **{reasons}** 份（{percent(reasons, len(rows))}）。未披露或无法可靠定位的字段保留为空，不进行金额估算。",
        "",
        "## 2. 月度分布",
        "",
        "| 月份 | 公告数 | 涉及公司数 | 占比 |",
        "|---|---:|---:|---:|",
    ]
    for month in sorted(monthly):
        lines.append(f"| {month} | {monthly[month]} | {len(monthly_companies[month])} | {percent(monthly[month], len(rows))} |")

    lines += [
        "",
        "## 3. 明确披露减持金额 Top 20",
        "",
        "> 仅统计公告正文明确出现“减持总金额/累计减持金额/减持金额”的记录；同一减持计划的进展与结果公告可能分别出现。",
        "",
        "| 排名 | 股票 | 公告日期 | 股东 | 公告类型 | 减持金额 |",
        "|---:|---|---|---|---|---:|",
    ]
    for rank, (amount, row) in enumerate(amount_rows[:20], 1):
        holder = row["减持股东"].replace("|", "/")
        if len(holder) > 24:
            holder = holder[:23] + "…"
        lines.append(f"| {rank} | {row['股票代码']} {row['简称']} | {row['公告日期']} | {holder} | {row['公告类型']} | {money(amount)} |")
    if not amount_rows:
        lines.append("| - | - | - | - | - | 无明确披露记录 |")

    lines += [
        "",
        "## 4. 行业分布",
        "",
        "| 行业（东方财富） | 公告数 | 涉及公司数 | 公告占比 |",
        "|---|---:|---:|---:|",
    ]
    for industry, count in industry_notices.most_common(12):
        lines.append(f"| {industry} | {count} | {len(industry_companies[industry])} | {percent(count, len(rows))} |")

    lines += [
        "",
        "## 5. 观察",
        "",
        f"1. **公告发布集中于{peak_month}。** 该月共有{peak_count}份，占全部确认公告的{percent(peak_count, len(rows))}。需注意3月仅覆盖26日至31日、6月仅覆盖1日至25日，因此月度数量不能直接解释为完整自然月趋势。",
        f"2. **行业分布呈一定集中度。** 公告数排名前三的行业合计占{percent(top3, len(rows))}；不过该分布是“公告次数”而非“独立减持事件数”，同一公司可能发布计划、进展和结果多份公告。",
        "",
        "---",
        "口径说明：成分股按2026-06-25时点名单；金额不根据减持股数与价格区间推算；行业取东方财富个股行业字段；本报告用于数据工程与信息整理，不构成投资建议。",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--structured", required=True, type=Path)
    parser.add_argument("--notices-db", required=True, type=Path)
    parser.add_argument("--text-cache", required=True, type=Path)
    parser.add_argument("--industry-cache", type=Path, default=Path("cache/industries.json"))
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    rows = read_csv(args.structured)
    codes = sorted({row["股票代码"] for row in rows})
    industries = load_industries(codes, args.industry_cache, args.workers)
    raw_total, candidate_total = source_counts(args.notices_db)
    report = build_report(rows, raw_total, candidate_total, industries, args.text_cache)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    print(json.dumps({"rows": len(rows), "companies": len(codes), "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
