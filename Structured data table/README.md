# Structured data table

抓取取中证1000成分股最近3个月"持股 5% 以上股东减持"相关公告将，将 `data acquisition` 模块生成的候选公告 CSV 转换为“持股5%以上股东减持”结构化数据表。

模块会下载东方财富公告 PDF，提取正文，核验正文是否明确出现“持股5%以上股东”，并输出以下核心字段：

- 股票代码、简称、公告日期
- 减持股东、股东类型
- 减持股数、减持股数口径、占总股本比例
- 减持期间、期间口径、减持原因
- 公告原文链接

同时输出公告类型、公告编号、PDF链接、解析置信度和解析说明，便于人工复核。

```powershell
python -m pip install -r requirements.txt

python .\parse_reduction_announcements.py `
  --candidates "..\data acquisition\output\reduction_notice_candidates.csv" `
  --output "structured_reduction_announcements.csv" `
  --cache "tmp\pdfs"
```

该模块采用可审计的正则与表格文本规则，未对公告未披露字段进行猜测；无法识别时保留空值并在解析说明”中提及。

本次针对已知问题做了三项口径微调：

- 将`股/万股/亿股`统一换算为“股”。
- 用`减持股数口径`区分“计划上限”和“实际减持”，避免把计划数当作成交数。
- 用`期间口径`区分“绝对日期”“相对期间”和“未识别”；相对交易日暂不伪转换。
