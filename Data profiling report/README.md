# Data profiling report

根据结构化减持公告CSV、公告元数据SQLite与已提取的公告正文，生成1-2页 `report.md` 数据画像。

报告包含：抓取总量、月度分布、公告明确披露的减持金额Top 20、东方财富行业分布和数据现象。

```powershell
python .\generate_report.py `
  --structured "..\outputs\structured_reduction_announcements.csv" `
  --notices-db "..\data acquisition\output\notices.sqlite3" `
  --text-cache "..\Structured data table\tmp\pdfs" `
  --output report.md
```

金额只采用公告正文明确披露值，不根据股数和价格估算；行业来自东方财富 `push2` 个股行业字段。
