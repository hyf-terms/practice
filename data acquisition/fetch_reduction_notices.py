#!/usr/bin/env python3
"""东方财富：中证1000“持股5%以上股东减持”候选公告抓取器。纯标准库。"""
from __future__ import annotations

import argparse, csv, json, logging, random, re, sqlite3, threading, time
import unicodedata, urllib.parse, urllib.request, zipfile
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

API_URL = "https://np-anotice-stock.eastmoney.com/api/security/ann"
DETAIL_URL = "https://data.eastmoney.com/notices/detail/{code}/{art}.html"
REDUCTION_TERMS = ("减持", "权益变动", "持股比例降至", "股份变动", "协议转让", "大宗交易")
STRONG_TERMS = ("持股5%以上", "持股百分之五以上", "5%以上股东", "百分之五以上股东", "大股东")
HOLDER_TERMS = STRONG_TERMS + ("控股股东", "实际控制人", "一致行动人", "股东", "权益变动人")
NEGATIVE_TERMS = ("限制性股票激励", "股权激励", "员工持股计划", "股份回购")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").lower()
    return re.sub(r"[\s\-—_（）()《》:：,，。;；]", "", text)


@dataclass(frozen=True)
class MatchResult:
    candidate: bool
    level: str
    score: int
    terms: tuple[str, ...]
    reason: str


def fuzzy_match(title: str, categories: Iterable[str]) -> MatchResult:
    title_text = normalize(title)
    text = normalize(title + " " + " ".join(categories))
    reductions = tuple(x for x in REDUCTION_TERMS if normalize(x) in text)
    holders = tuple(x for x in HOLDER_TERMS if normalize(x) in text)
    explicit = tuple(x for x in STRONG_TERMS if normalize(x) in text)
    negatives = tuple(x for x in NEGATIVE_TERMS if normalize(x) in text)
    management_only = any(normalize(x) in title_text for x in ("董事", "监事", "高级管理人员", "董监高")) and not any(normalize(x) in title_text for x in STRONG_TERMS + ("控股股东", "实际控制人", "一致行动人"))
    candidate = bool(reductions and holders) and not management_only
    score = min(len(reductions), 3) * 2 + min(len(holders), 3) + (5 if explicit else 0) - len(negatives) * 2
    key_roles = ("控股股东", "实际控制人", "大股东", "一致行动人")
    level = "high" if candidate and explicit else "medium" if candidate and any(normalize(x) in text for x in key_roles) else "low" if candidate else "excluded"
    reason = f"减持词={list(reductions)}; 股东词={list(holders)}"
    if negatives: reason += f"; 降权词={list(negatives)}"
    if management_only: reason += "; 排除=仅董监高身份"
    return MatchResult(candidate, level, score, tuple(dict.fromkeys(reductions + holders + negatives)), reason)


def _xlsx_rows(path: Path) -> list[dict]:
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            shared = ["".join(n.text or "" for n in si.iterfind(".//m:t", ns)) for si in root.findall("m:si", ns)]
        sheets = sorted(x for x in z.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", x))
        if not sheets: raise ValueError("xlsx 中没有工作表")
        root = ET.fromstring(z.read(sheets[0])); matrix = []
        for row in root.findall(".//m:sheetData/m:row", ns):
            cells = {}
            for cell in row.findall("m:c", ns):
                letters = re.match(r"[A-Z]+", cell.get("r", "A1")).group(0); col = 0
                for ch in letters: col = col * 26 + ord(ch) - 64
                kind, v, inline = cell.get("t"), cell.find("m:v", ns), cell.find("m:is", ns)
                value = shared[int(v.text or 0)] if kind == "s" and v is not None else "".join(n.text or "" for n in inline.iterfind(".//m:t", ns)) if kind == "inlineStr" and inline is not None else v.text or "" if v is not None else ""
                cells[col - 1] = value
            matrix.append([cells.get(i, "") for i in range(max(cells, default=-1) + 1)])
    if not matrix: return []
    headers = [str(x).strip() for x in matrix[0]]
    return [dict(zip(headers, row)) for row in matrix[1:]]


def load_constituents(path: Path) -> list[tuple[str, str]]:
    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as f: rows = list(csv.DictReader(f))
    elif path.suffix.lower() == ".xlsx": rows = _xlsx_rows(path)
    else: raise ValueError("仅支持 .xlsx 或 .csv")
    result, seen = [], set()
    for row in rows:
        raw = str(row.get("股票代码") or row.get("stock_code") or "").strip()
        code = raw.split(".")[0].split("'")[-1].zfill(6)
        name = str(row.get("股票简称") or row.get("股票名称") or row.get("stock_name") or "").strip()
        if re.fullmatch(r"\d{6}", code) and code not in seen: seen.add(code); result.append((code, name))
    if not result: raise ValueError("未找到有效的‘股票代码’列")
    return result


def get_json(params: dict, attempts: int = 5) -> dict:
    url = API_URL + "?" + urllib.parse.urlencode(params)
    headers = {"User-Agent": "Mozilla/5.0 Chrome/126 Safari/537.36", "Referer": "https://data.eastmoney.com/", "Accept": "application/json"}
    error = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as exc:
            error = exc
            if attempt + 1 < attempts: time.sleep(0.8 * 2**attempt + random.uniform(0, .3))
    raise RuntimeError(f"请求失败，已重试{attempts}次：{error}")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False); self.db.row_factory = sqlite3.Row; self.lock = threading.Lock()
        self.db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS announcements(art_code TEXT,stock_code TEXT,stock_name TEXT,notice_date TEXT,title TEXT,categories TEXT,candidate INTEGER,match_level TEXT,match_score INTEGER,matched_terms TEXT,match_reason TEXT,detail_url TEXT,raw_json TEXT,fetched_at TEXT,PRIMARY KEY(art_code,stock_code));
        CREATE TABLE IF NOT EXISTS tasks(stock_code TEXT PRIMARY KEY,stock_name TEXT,status TEXT,total_hits INTEGER DEFAULT 0,pages INTEGER DEFAULT 0,last_error TEXT,updated_at TEXT);
        """); self.db.commit()

    def completed(self):
        with self.lock: return {x[0] for x in self.db.execute("SELECT stock_code FROM tasks WHERE status='done'")}

    def page(self, code, name, items, page, total):
        now = datetime.now().isoformat(timespec="seconds"); records = []
        for item in items:
            cats = [x.get("column_name", "") for x in item.get("columns") or []]; m = fuzzy_match(item.get("title", ""), cats); art = item.get("art_code", "")
            records.append((art,code,name,str(item.get("notice_date", ""))[:10],item.get("title", ""),json.dumps(cats,ensure_ascii=False),int(m.candidate),m.level,m.score,json.dumps(m.terms,ensure_ascii=False),m.reason,DETAIL_URL.format(code=code,art=art),json.dumps(item,ensure_ascii=False),now))
        with self.lock, self.db:
            self.db.executemany("INSERT OR REPLACE INTO announcements VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", records)
            self.db.execute("INSERT INTO tasks VALUES(?,?, 'running',?,?,NULL,?) ON CONFLICT(stock_code) DO UPDATE SET status='running',total_hits=excluded.total_hits,pages=excluded.pages,last_error=NULL,updated_at=excluded.updated_at",(code,name,total,page,now))

    def finish(self, code, name, total, pages):
        with self.lock, self.db: self.db.execute("INSERT INTO tasks VALUES(?,?, 'done',?,?,NULL,?) ON CONFLICT(stock_code) DO UPDATE SET status='done',total_hits=excluded.total_hits,pages=excluded.pages,last_error=NULL,updated_at=excluded.updated_at",(code,name,total,pages,datetime.now().isoformat(timespec="seconds")))

    def fail(self, code, name, error):
        with self.lock, self.db: self.db.execute("INSERT INTO tasks(stock_code,stock_name,status,last_error,updated_at) VALUES(?,?, 'failed',?,?) ON CONFLICT(stock_code) DO UPDATE SET status='failed',last_error=excluded.last_error,updated_at=excluded.updated_at",(code,name,error[:1000],datetime.now().isoformat(timespec="seconds")))

    def export(self, path: Path, minimum: str):
        rank={"low":1,"medium":2,"high":3}; levels=[x for x in rank if rank[x]>=rank[minimum]]; q=",".join("?"*len(levels))
        rows=self.db.execute(f"SELECT stock_code,stock_name,notice_date,title,categories,match_level,match_score,matched_terms,match_reason,art_code,detail_url,fetched_at FROM announcements WHERE candidate=1 AND match_level IN ({q}) ORDER BY notice_date DESC,stock_code",levels).fetchall()
        path.parent.mkdir(parents=True,exist_ok=True)
        headers=(list(rows[0].keys()) if rows else ["stock_code","stock_name","notice_date","title","categories","match_level","match_score","matched_terms","match_reason","art_code","detail_url","fetched_at"])
        with path.open("w",encoding="utf-8-sig",newline="") as f: w=csv.writer(f); w.writerow(headers); w.writerows([tuple(x) for x in rows])
        return len(rows)


def fetch_stock(store, code, name, start, end, page_size, dmin, dmax):
    page,total=1,0
    try:
        while True:
            payload=get_json({"sr":"-1","page_size":page_size,"page_index":page,"ann_type":"A","client_source":"web","stock_list":code,"f_node":"0","s_node":"0","begin_time":start,"end_time":end})
            if payload.get("success") != 1: raise RuntimeError(payload.get("error") or "success != 1")
            data=payload.get("data") or {}; items=data.get("list") or []; total=int(data.get("total_hits") or 0); store.page(code,name,items,page,total)
            if page*page_size>=total or not items: break
            page+=1; time.sleep(random.uniform(dmin,dmax))
        store.finish(code,name,total,page); return code,total
    except Exception as exc: store.fail(code,name,repr(exc)); raise


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--constituents",required=True,type=Path); p.add_argument("--start",default="2026-03-26"); p.add_argument("--end",default="2026-06-25"); p.add_argument("--db",type=Path,default=Path("output/notices.sqlite3")); p.add_argument("--output",type=Path,default=Path("output/reduction_notice_candidates.csv")); p.add_argument("--workers",type=int,default=4); p.add_argument("--page-size",type=int,default=100); p.add_argument("--delay-min",type=float,default=.15); p.add_argument("--delay-max",type=float,default=.45); p.add_argument("--minimum-level",choices=("low","medium","high"),default="low"); p.add_argument("--limit",type=int); p.add_argument("--no-resume",action="store_true"); a=p.parse_args()
    if date.fromisoformat(a.start)>date.fromisoformat(a.end): raise ValueError("开始日期不能晚于结束日期")
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s"); stocks=load_constituents(a.constituents); stocks=stocks[:a.limit] if a.limit else stocks; store=Store(a.db)
    if not a.no_resume: done=store.completed(); stocks=[x for x in stocks if x[0] not in done]
    logging.info("待抓取股票数：%d；日期：%s 至 %s",len(stocks),a.start,a.end); failures=0
    with ThreadPoolExecutor(max_workers=max(1,a.workers)) as pool:
        futures={pool.submit(fetch_stock,store,c,n,a.start,a.end,a.page_size,a.delay_min,a.delay_max):(c,n) for c,n in stocks}
        for i,future in enumerate(as_completed(futures),1):
            c,n=futures[future]
            try: _,hits=future.result(); logging.info("[%d/%d] %s %s：%d条",i,len(futures),c,n,hits)
            except Exception as exc: failures+=1; logging.error("[%d/%d] %s %s：%s",i,len(futures),c,n,exc)
    count=store.export(a.output,a.minimum_level); logging.info("候选公告：%d；失败股票：%d；输出：%s",count,failures,a.output); return 1 if failures else 0


if __name__ == "__main__": raise SystemExit(main())
