#!/usr/bin/env python3
"""下载东方财富候选公告 PDF，并解析“持股5%以上股东减持”结构化字段。"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import random
import re
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path

from pypdf import PdfReader


PDF_URL = "https://pdf.dfcfw.com/pdf/H2_{art_code}_1.pdf"
FIELDS = [
    "股票代码", "简称", "公告日期", "减持股东", "股东类型", "减持股数",
    "占总股本比例", "减持期间", "减持原因", "公告原文链接",
    "公告标题", "公告类型", "公告编号", "PDF链接", "解析置信度", "解析说明",
]


@dataclass
class ParsedRow:
    股票代码: str
    简称: str
    公告日期: str
    减持股东: str
    股东类型: str
    减持股数: int | None
    占总股本比例: float | None
    减持期间: str
    减持原因: str
    公告原文链接: str
    公告标题: str
    公告类型: str
    公告编号: str
    PDF链接: str
    解析置信度: str
    解析说明: str


def flat(text: str) -> str:
    text = text.replace("\u00a0", " ").replace("\uf06c", " ")
    return re.sub(r"\s+", " ", text).strip()


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def parse_date(text: str) -> str:
    match = re.search(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}" if match else ""


def to_int(value: str) -> int:
    return int(value.replace(",", "").replace("，", ""))


def event_type(title: str) -> str:
    if re.search(r"实施完毕|完成|结果|期限届满", title):
        return "减持结果"
    if re.search(r"进展|达到\s*1%|触及\s*1%|权益变动", title):
        return "减持进展/权益变动"
    if re.search(r"计划|预披露", title):
        return "减持计划"
    if "终止" in title:
        return "减持终止"
    return "减持公告"


def eligible(text: str, title: str = "") -> bool:
    c = compact(text)
    title_c = compact(title)
    c = re.sub(r"直接持股5%以上股东(?:□是√否|☐是☑否|□是□否)", "", c)
    explicit_title = bool(re.search(r"(?:原)?持股5(?:\.0+)?%以上股东|5(?:\.0+)?%以上股东", title_c))
    positive_checkbox = bool(re.search(r"直接持股5%以上股东(?:√是|☑是)", c))
    narrative = bool(re.search(r"(?:原)?持股5(?:\.0+)?%以上股东[^□☐]{0,100}(?:保证|持有|计划|拟|出具|减持)", c))
    holder = explicit_title or positive_checkbox or narrative
    reduction_title = "减持" in title_c
    reduction_body = bool(re.search(r"累计减持|减持数量|减持比例|实施已披露的股份减持计划|通过[^。]{0,40}减持|持股(?:数量|比例)由[^。]{0,80}(?:减少|下降|降至)", c))
    obvious_increase = bool(re.search(r"本次权益变动为[^。]{0,100}增持|履行此前披露的增持计划", c))
    return holder and (reduction_title or reduction_body) and not (obvious_increase and not reduction_title)


def clean_name(name: str) -> str:
    name = flat(name).strip("：:，,。；;[]【】")
    name = re.sub(r"先\s*生$", "先生", name)
    name = re.sub(r"女\s*士$", "女士", name)
    name = re.sub(r"^(?:公司|本公司)(?:股东)?", "", name)
    name = re.sub(r"（(?:以下)?简称.*$|\((?:以下)?简称.*$", "", name)
    name = re.sub(r"\s+(?:持有|计划|拟|出于|保证|于20\d{2}).*$", "", name)
    return name.strip().removesuffix("合计")


def shareholder_names(text: str, title: str = "") -> list[str]:
    one_line = flat(text)
    patterns = [
        r"收到(?:公司)?股东\s*([^，。]{2,120}?)(?=出具|发来|提交)",
        r"股东名称[ \t]*[:：]?[ \t]*([^\n]{2,80}?)(?=[ \t]*股东身份|\n)",
        r"(?:原)?持股\s*5\s*%\s*以上股东\s*([^，。\n]{2,80}?)(?=保证|出具|（以下简称|\(以下简称)",
        r"公司股东\s+([^，。\n]{2,80}?)(?=（以下简称|\(以下简称)",
    ]
    names = []
    bad = re.compile(r"减持股份|股份减持|权益变动|提示性公告|重要内容|股份性质|减持方式|持股数量|持股比例|一致行动关系|因以下事项|实施结果|基本情况|减持计划|^变动$|^减持$")
    for index, pattern in enumerate(patterns):
        source = text if index == 1 else one_line
        for value in re.findall(pattern, source):
            name = clean_name(value)
            if 2 <= len(name) <= 80 and not bad.search(name):
                names.append(name)
    if not names:
        match = re.search(r"[（(]([^（）()]{2,60})[）)]\s*$", title)
        if match:
            names.append(clean_name(match.group(1)))
    result = []
    for name in names:
        display = re.sub(r"(?:先生|女士)$", "", name)
        base = re.sub(r"\s+", "", display)
        if not any(re.sub(r"(?:先生|女士)$", "", re.sub(r"\s+", "", old)) == base for old in result):
            result.append(display)
    return result[:8]


def holder_type(text: str) -> str:
    c = compact(text)
    title_area = c[:300]
    if re.search(r"控股股东、?实(?:际控制人|控人)及一致行动人[√☑]是", c) or "控股股东及其一致行动人" in title_area:
        return "控股股东/实际控制人及一致行动人"
    if re.search(r"(?:为|系)(?:公司)?实际控制人(?:及其)?一致行动人", c):
        return "实际控制人及一致行动人"
    if re.search(r"不属于(?:公司)?的?控股股东、?实际控制人", c):
        return "持股5%以上股东（非控股股东）"
    if "原持股5%以上股东" in c:
        return "原持股5%以上股东"
    if re.search(r"直接持股5%以上股东[√☑]是", c):
        return "直接持股5%以上股东"
    return "持股5%以上股东"


def reduction_shares(text: str, kind: str) -> int | None:
    c = flat(text)
    actual_patterns = [
        r"累计减持(?:公司)?股份\s*([\d,，]+)\s*股",
        r"累计减持[^。]{0,120}?共计\s*([\d,，]+)\s*股",
        r"合计减持(?:公司)?股份?\s*([\d,，]+)\s*股",
        r"减持数量\s*([\d,，]+)\s*股",
        r"本次(?:权益变动|减持)[^。]{0,100}?减持\s*([\d,，]+)\s*股",
    ]
    for pattern in actual_patterns:
        values = [to_int(x) for x in re.findall(pattern, c)]
        if values:
            return max(values)
    before_after = re.search(
        r"本次减持前所持股份.*?([\d,，]{4,})\s+[\d.]+\s+([\d,，]{4,})\s+[\d.]+",
        c, flags=re.S,
    )
    if before_after:
        before, after = to_int(before_after.group(1)), to_int(before_after.group(2))
        if 0 <= after <= before:
            return before - after
    if kind == "减持计划":
        planned = [to_int(x) for x in re.findall(r"(?:合计)?(?:计划|拟)?(?:减持)?[^。；]{0,40}?不超过\s*([\d,，]+)\s*股", c)]
        return max(planned) if planned else None
    return None


def reduction_ratio(text: str, kind: str) -> float | None:
    c = flat(text)
    patterns = [
        r"累计减持[^。]{0,100}?占(?:公司)?总股本(?:的)?\s*([\d.]+)\s*%",
        r"减持比例\s*([\d.]+)\s*%",
    ]
    for pattern in patterns:
        values = [float(x) / 100 for x in re.findall(pattern, c)]
        if values:
            return max(values)
    if kind == "减持计划":
        values = [float(x) / 100 for x in re.findall(r"不超过(?:公司)?总股本(?:的)?\s*([\d.]+)\s*%", c)]
        return max(values) if values else None
    return None


def reduction_period(text: str, kind: str) -> str:
    c = flat(text)
    pattern = r"减持期间\s*((?:20\d{2}\s*年)?\s*\d{1,2}\s*月\s*\d{1,2}\s*日)\s*[～~至—-]+\s*((?:20\d{2}\s*年)?\s*\d{1,2}\s*月\s*\d{1,2}\s*日)"
    match = re.search(pattern, c)
    if match:
        left, right = parse_date(match.group(1)), parse_date(match.group(2))
        if not left and right:
            year = right[:4]
            left = parse_date(year + "年" + match.group(1))
        return f"{left}至{right}" if left and right else flat(match.group(0).replace("减持期间", ""))
    section = re.search(r"股东减持股份情况(.*?)(?:减持股份来源|股东本次减持前后|二、)", c, flags=re.S)
    if section:
        dates = [parse_date(x) for x in re.findall(r"20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日", section.group(1))]
        dates = sorted(x for x in dates if x)
        if dates:
            return dates[0] if len(dates) == 1 else f"{dates[0]}至{dates[-1]}"
    if kind == "减持计划":
        match = re.search(r"(?:自|在)([^。；]{0,100}?(?:个月内|月内))", c)
        if match:
            return flat(match.group(1))
    return "未识别"


def reduction_reason(text: str) -> str:
    c = flat(text)
    patterns = [
        r"(?:减持原因|减持目的)\s*[:：]?\s*([^。；\n]{2,100})",
        r"出于([^，。；]{2,80})(?:，|,).*?(?:拟|计划)减持",
        r"因([^，。；]{2,60}(?:需要|需求))[^。；]{0,40}?减持",
    ]
    for pattern in patterns:
        match = re.search(pattern, c)
        if match:
            return flat(match.group(1)).strip("，,。；;")
    for term in ("自身资金需求", "个人资金需求", "经营发展需要", "资产配置需求", "集团战略与经营计划综合考虑"):
        if term in c:
            return term
    return "未披露"


def parse_notice(meta: dict, text: str) -> list[ParsedRow]:
    if not eligible(text, meta["title"]):
        return []
    title = meta["title"]
    kind = event_type(title)
    names = shareholder_names(text, title) or ["未识别"]
    shareholder = "、".join(names)
    shares = reduction_shares(text, kind)
    ratio = reduction_ratio(text, kind)
    period = reduction_period(text, kind)
    reason = reduction_reason(text)
    confidence = "高" if names != ["未识别"] and (shares is not None or ratio is not None) else "中" if names != ["未识别"] else "低"
    notes = []
    if shares is None: notes.append("减持股数未识别")
    if ratio is None: notes.append("占总股本比例未识别")
    if period == "未识别": notes.append("减持期间未识别")
    if reason == "未披露": notes.append("公告未明确披露减持原因或规则未识别")
    pdf_url = PDF_URL.format(art_code=meta["art_code"])
    return [ParsedRow(
        股票代码=meta["stock_code"], 简称=meta["stock_name"], 公告日期=meta["notice_date"],
        减持股东=shareholder, 股东类型=holder_type(text), 减持股数=shares, 占总股本比例=ratio,
        减持期间=period, 减持原因=reason, 公告原文链接=meta["detail_url"], 公告标题=title,
        公告类型=kind, 公告编号=meta["art_code"], PDF链接=pdf_url,
        解析置信度=confidence, 解析说明="；".join(notes) if notes else "字段完整",
    )]


def download_pdf(art_code: str, cache: Path, attempts: int = 4) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f"{art_code}.pdf"
    if path.exists() and path.stat().st_size > 1000:
        return path
    url = PDF_URL.format(art_code=art_code)
    headers = {"User-Agent": "Mozilla/5.0 Chrome/126 Safari/537.36", "Referer": "https://data.eastmoney.com/"}
    error = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=40) as response:
                payload = response.read()
            if not payload.startswith(b"%PDF"):
                raise ValueError("响应不是PDF")
            path.write_bytes(payload)
            return path
        except Exception as exc:
            error = exc
            time.sleep(0.8 * 2**attempt + random.uniform(0, .4))
    raise RuntimeError(f"{art_code} 下载失败：{error}")


def process(meta: dict, cache: Path) -> tuple[list[ParsedRow], str | None]:
    try:
        pdf = download_pdf(meta["art_code"], cache)
        text_path = cache / f'{meta["art_code"]}.txt'
        if text_path.exists():
            text = text_path.read_text(encoding="utf-8")
        else:
            text = "\n".join(page.extract_text() or "" for page in PdfReader(pdf).pages)
            text_path.write_text(text, encoding="utf-8")
        if len(compact(text)) < 80:
            return [], "PDF无可提取文本，可能需要OCR"
        return parse_notice(meta, text), None
    except Exception as exc:
        return [], repr(exc)


def read_candidates(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    seen, result = set(), []
    for row in rows:
        key = (row["stock_code"], row["art_code"])
        if key not in seen:
            seen.add(key); result.append(row)
    return result


def write_csv(path: Path, rows: list[ParsedRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            item = asdict(row)
            if item["占总股本比例"] is not None:
                item["占总股本比例"] = f'{item["占总股本比例"]:.6f}'
            writer.writerow(item)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--candidates", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--cache", type=Path, default=Path("tmp/pdfs"))
    p.add_argument("--failures", type=Path, default=Path("tmp/parse_failures.json"))
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--limit", type=int)
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    metas = read_candidates(args.candidates)
    if args.limit: metas = metas[:args.limit]
    all_rows, failures = [], []
    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(process, meta, args.cache): meta for meta in metas}
        for index, future in enumerate(as_completed(futures), 1):
            meta = futures[future]; rows, error = future.result()
            with lock: all_rows.extend(rows)
            if error: failures.append({"art_code": meta["art_code"], "title": meta["title"], "error": error})
            if index % 25 == 0 or index == len(futures):
                logging.info("进度 %d/%d；结构化记录 %d；失败 %d", index, len(futures), len(all_rows), len(failures))
    all_rows.sort(key=lambda x: (x.公告日期, x.股票代码, x.公告编号, x.减持股东), reverse=True)
    write_csv(args.output, all_rows)
    args.failures.parent.mkdir(parents=True, exist_ok=True)
    args.failures.write_text(json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info("完成：%d 条结构化记录，%d 个失败；输出 %s", len(all_rows), len(failures), args.output)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
