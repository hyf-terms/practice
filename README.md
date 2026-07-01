# 中证1000大股东减持公告数据工程

本项目基于东方财富公开数据，抓取 **2026-03-26 至 2026-06-25** 期间中证1000成分股公告，通过模糊关键词召回与 PDF 正文二次核验，生成“持股5%以上股东减持”公告级结构化数据，并输出数据画像报告。

当前全量运行结果：抓取公告元数据 51,221 份，召回候选公告 468 份，正文确认相关公告 221 份。结构化结果以公告为粒度，不等同于去重后的独立减持事件。

## 项目结构

```text
practice/
├─ data acquisition/
│  ├─ fetch_reduction_notices.py       # 公告元数据抓取、模糊关键词召回、断点续跑
│  ├─ test_fetch_reduction_notices.py  # 抓取与筛选规则测试
│  └─ README.md
├─ Structured data table/
│  ├─ parse_reduction_announcements.py # PDF下载、正文核验、字段解析
│  ├─ test_parser.py                    # 单位换算和字段解析测试
│  ├─ requirements.txt
│  └─ README.md
├─ Data profiling report/
│  ├─ generate_report.py               # 月度、金额Top 20、行业分布与画像报告
│  ├─ requirements.txt
│  └─ README.md
├─ requirements.txt                    # 项目统一依赖入口
└─ README.md
```

运行后会产生但默认不提交的目录包括：

- `data acquisition/output/`：公告 SQLite 数据库及候选公告 CSV；
- `Structured data table/tmp/`：PDF 与文本缓存、解析失败记录；
- `Data profiling report/cache/`：行业查询缓存。

成分股输入表和最终 CSV、报告属于数据产物，位于《中证1000股抓取》文件夹中。

## 环境与 requirements

- Python 3.10 或更高版本；
- 可访问东方财富公告、PDF 和行情接口；
- 唯一第三方依赖：`pypdf>=6.0,<7`；
- 抓取、SQLite、CSV、Excel 读取和报告生成的其余功能均使用 Python 标准库。

建议在仓库根目录创建虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## 输入数据

抓取模块需要 `index_constituents.xlsx`，至少包含股票代码和简称。该文件为 2026-06-25 时点的中证1000成分股表，共 1000 只股票。示例路径：

```text
..\..\outputs\index_constituents.xlsx
```

## 如何运行

以下命令均在仓库根目录 `practice` 中执行。PowerShell 中带空格的目录需要使用 `&` 调用运算符。

### 1. 抓取公告并召回候选记录

```powershell
python ".\data acquisition\fetch_reduction_notices.py" `
  --constituents "..\..\outputs\index_constituents.xlsx" `
  --start 2026-03-26 `
  --end 2026-06-25 `
  --db ".\data acquisition\output\notices.sqlite3" `
  --output ".\data acquisition\output\reduction_notice_candidates.csv"
```

程序将任务状态写入 SQLite，重复执行时默认跳过已完成股票。联调可增加 `--limit 10`；如需忽略断点状态，可增加 `--no-resume`。

### 2. 下载 PDF 并生成结构化表

```powershell
python ".\Structured data table\parse_reduction_announcements.py" `
  --candidates ".\data acquisition\output\reduction_notice_candidates.csv" `
  --output "..\..\outputs\structured_reduction_announcements.csv" `
  --cache ".\Structured data table\tmp\pdfs" `
  --failures ".\Structured data table\tmp\parse_failures.json"
```

核心字段包括：股票代码、简称、公告日期、减持股东、股东类型、减持股数、减持股数口径、占总股本比例、减持期间、期间口径、减持原因和公告原文链接。未披露或不能可靠确认的字段保留为空，不自动猜测。

### 3. 生成数据画像报告

```powershell
python ".\Data profiling report\generate_report.py" `
  --structured "..\..\outputs\structured_reduction_announcements.csv" `
  --notices-db ".\data acquisition\output\notices.sqlite3" `
  --text-cache ".\Structured data table\tmp\pdfs" `
  --industry-cache ".\Data profiling report\cache\industries.json" `
  --output "..\..\outputs\report.md"
```

报告包含抓取总量、月度分布、公告明确披露的减持金额 Top 20、东方财富行业分布和数据现象。金额不使用收盘价替代实际成交金额。

### 4. 运行测试

```powershell
python -m unittest discover -s ".\data acquisition" -p "test_*.py"
python -m unittest discover -s ".\Structured data table" -p "test_*.py"
```

## 数据源及选型理由

| 数据 | 东方财富接口/资源 | 选型理由 |
|---|---|---|
| 中证1000成分股 | 东方财富指数成分股接口 | 可直接得到股票代码、简称和权重等字段，便于与同一平台的证券标识对齐 |
| 公告元数据 | `np-anotice-stock.eastmoney.com/api/security/ann` | 支持按股票、日期和分页查询，返回公告编号、标题、日期及分类，适合全量抓取和断点续跑 |
| 公告原文 | `pdf.dfcfw.com` 公告 PDF | PDF 是字段核验的原始证据；可避免仅凭标题造成漏报或误报，并保留原文链接用于审计 |
| 行业信息 | 东方财富 `push2` 个股行情接口 | 股票代码体系与公告数据一致，减少跨平台代码映射；适合生成统一口径的行业分布 |

选择东方财富而非同时混用多个平台，主要考虑：数据链路完整、股票标识一致、公告原文可追溯，以及接口响应便于落地为CSV。需要注意这些接口属于公开网页数据接口，并非稳定性承诺的正式数据服务。



