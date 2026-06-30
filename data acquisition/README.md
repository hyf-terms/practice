# 东方财富公告抓取模块

读取 `index_constituents.xlsx` 中的中证1000成分股，抓取东方财富在 `2026-03-26` 至 `2026-06-25` 发布的公告，并用模糊关键词筛选“持股5%以上股东减持”候选公告。

## 筛选规则

- 减持信号：减持、权益变动、持股比例降至、协议转让、大宗交易等。
- 股东信号：持股5%以上、5%以上股东、控股股东、实际控制人、一致行动人、股东等。
- `high`：明确出现5%以上股东；`medium`：出现控股股东等强主体；`low`：一般股东减持，高召回待核验。

模糊筛选仅负责召回。最终结构化结果仍需根据公告正文核验减持前持股比例。

## 运行

要求 Python 3.10+，无第三方依赖。

```powershell
python .\fetch_reduction_notices.py `
  --constituents "C:\Users\hyf\Desktop\中证1000股\index_constituents.xlsx" `
  --start 2026-03-26 `
  --end 2026-06-25 `
  --db output\notices.sqlite3 `
  --output output\reduction_notice_candidates.csv
```

用 `--limit 10` 联调前10只。SQLite 保存原始公告和任务状态，程序再次运行会跳过已完成股票。

数据接口：`https://np-anotice-stock.eastmoney.com/api/security/ann`
