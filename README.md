# 北交所新股估值

这是当前可直接交付使用的本地版北交所新股估值工具。

## 入口

- 双击 [运行.bat](运行.bat)
- 或命令行运行：`python code\bse_ipo_valuation.py 920177`

程序会提示输入 6 位代码，生成带时间戳的 PDF 报告到 `输出/` 目录。

如需启用 Tushare，请先在本机环境变量中设置 `TUSHARE_TOKEN`。仓库不会保存 token。

## 当前核心文件

- `code/bse_ipo_valuation.py`：主入口
- `code/report_generator.py`：报告渲染与 PDF 导出
- `code/valuation_engine.py`：方法一、方法二、综合估值
- `code/data_fetcher.py`：IPO 基础信息与近期样本获取
- `code/comparable_data_helper.py`：方法一可比快照数据源分发层
- `code/ipo_data_helper.py`：IPO 信息与方法二样本数据源分发层
- `code/tushare_helper.py`：Tushare 可比快照适配层
- `code/tushare_ipo_helper.py`：Tushare IPO 信息与近期样本适配层
- `code/pdf_parser.py`：公告文件解析
- `code/trend_scorer.py`：首日走势评分
- `code/wind_helper.py`：当前保留的数据源适配层

## 目录结构

- 根目录：仅保留 `运行.bat`、`策略参数.txt`、`README.md`
- `code/`：全部核心运行模块
- `公告文件/`：原始 PDF 公告
- `首日分时走势/`：本地走势样本数据
- `输出/`：运行后生成的 PDF 报告
- `tests/`：回归验证与黄金样本脚本
- `tools/`：批量扫描等辅助工具
- `docs/设计文档/`：方案、实施计划、施工方案
- `docs/工作日志/`：工作日志与下一步工作清单

## 文档命名规则

- 设计文档：`01_...`、`02_...`、`03_...`
- 工作日志：`YYYYMMDD_工作日志.md`
- 下一步清单：`YYYYMMDD_下一步工作清单.md`

这样按文件名即可排序查看，不需要额外猜日期和版本。

## 编码约定

- Markdown、TXT、BAT、CMD 文档统一使用 `UTF-8 with BOM`
- 仓库已通过 `.editorconfig` 固定编码
- `docs/` 下文档已统一转码，Windows 下直接打开或读取更稳定

## 验证脚本

- `tests/validate_main_flow_regression.py`
- `tests/validate_method1_data_pipeline.py`
- `tests/validate_tushare_data_pipeline.py`
- `tests/validate_tushare_ipo_data_pipeline.py`
- `tests/validate_pdf_content_goldens.py`
- `tests/validate_pdf_old_shares_goldens.py`

## 工具脚本

- `tools/scan_pdf_samples.py`
- `tools/tune_params.py`：离线调参入口
- `tools/review_candidate_params.py`：候选参数集复核
- `tools/upgrade_replay_dataset_composite.py`：将现有回放数据集升级为 `composite` 口径
- `tools/observe_composite_weight_candidate.py`：综合权重观察期复核
- `tools/observe_trend_balance_candidate.py`：`trend_balance` 观察期复核
- `tools/observe_quick_method2_core_only.py`：`quick_method2_core_only` 观察期复核

## 当前默认参数状态

- 综合权重当前默认值：
  - `weight_comparable = 0.20`
  - `weight_industry_momentum = 0.80`
- 方法二当前保留的 `quick_method2_core_only` 试运行参数：
  - `price_range_width = 0.12`
  - `float_size_threshold = 1500`
  - `small_cap_premium = 0.15`
- `trend_balance` 当前结论：
  - 已完成离线调参与两轮真实样本观察
  - 暂不吸收为默认参数
- `WSI` 当前结论：
  - 已完成扩组复核
  - 当前未观察到稳定增益

如需追踪这些结论的推演过程，优先查看：

- `docs/工作日志/`
- `data/offline_tuning/candidate_sets/`
- `输出/调参/`
- `输出/观察期/`

## 说明

- 仓库里的早期探索脚本、临时模板和中间产物已经清理
- 目前保留的是“可运行入口 + `code/` 核心链路 + 当前验证资产”
- 数据源配置已内置到代码中，`策略参数.txt` 只保留用户需要调的业务参数
- 后续如果切换数据源，优先从 `code/data_fetcher.py` 和 `code/wind_helper.py` 接续
