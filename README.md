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
- `code/bse_official_helper.py`：北交所官网公告下载封装，主走 `920 -> 公开发行一览详情 -> PDF`，审核项目链路保留兜底
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
- `tests/validate_bse_official_helper.py`
- `tests/validate_prospectus_autodownload.py`
- `tests/validate_bse_newshare_fallback.py`

## 工具脚本

- `tools/scan_pdf_samples.py`
- `tools/tune_params.py`：离线调参入口
- `tools/review_candidate_params.py`：候选参数集复核
- `tools/upgrade_replay_dataset_composite.py`：将现有回放数据集升级为 `composite` 口径
- `tools/observe_composite_weight_candidate.py`：综合权重观察期复核
- `tools/observe_trend_balance_candidate.py`：`trend_balance` 观察期复核
- `tools/observe_quick_method2_core_only.py`：`quick_method2_core_only` 观察期复核
- `tools/cache_listing_day_intraday.py`：扫描上市首日新股并用 Tushare 分钟线缓存本地 `首日分时走势/*.csv`
- `tools/add_new_ipo_intraday_cache.py`：按最新上市顺序补齐缺失的新股首日分时缓存
- `tools/download_bse_official_pdf.py`：按北交所官网映射链下载招股说明书 / 上市公告书 PDF 到 `公告文件/`

## 北交所官网公告下载

- 当前主链路已切到北交所官网“公开发行一览”：
  - `920xxx -> newShareController/infoResult.do -> 公开发行详情 id`
  - `详情 id -> newShareController/infoDetailResult.do -> 招股说明书 / 上市公告书 PDF`
- 若“公开发行一览”未命中，再回退旧官网审核项目链路：
  - `920xxx -> detailCompany.do -> 公司全称`
  - `公司全称 -> infoResult.do -> 8 开头项目代码 + project id`
  - `project id -> infoDetailResult.do -> 招股说明书 PDF`
- 当前已实现上市公告书下载链路：
  - 先尝试北交所官网“公开发行一览”详情页公告列表
  - 再尝试北交所官网上市公司公告接口
  - 若官网未命中，再回退东方财富公告列表和公告详情页提取 PDF 原文链接
- 当前工具默认优先取 `BHG(注册稿)`，再回退 `SYG(上会稿)`、`SBG(申报稿)`

示例：

- `python tools\download_bse_official_pdf.py 920177`
- `python tools\download_bse_official_pdf.py 920177 --resolve-only`
- `python tools\download_bse_official_pdf.py 920177 --overwrite`
- `python tools\download_bse_official_pdf.py 920177 --document listing`
- `python tools\download_bse_official_pdf.py 920177 --document all`

## 主程序公告取用顺序

- 生成估值报告时，主程序会先检查本地 `公告文件/` 里的招股说明书和上市公告书
- 若本地缺少任意一个文件，就会启动官网探测；缺招股说明书时会提示“招股说明书下载中，请稍候。”，仅缺上市公告书时会提示“上市公告书探测中，请稍候。”
- 官网探测仍优先走北交所“公开发行一览”链路；命中招股说明书和上市公告书时会直接下载到本地
- 若官网探测结束后仍没有可用招股说明书，程序会直接提示“未取到招股说明书，生成报告失败：...”并终止本次报告生成
- 只要已经拿到招股说明书，报告就会继续生成；若上市公告书仍未命中或下载失败，只提示“上市公告书未下载，可手动补充”，不再额外走其他公告链路兜底
- 上市公告书目前仍只按本地文件参与解析，不作为报告生成的必备项

## 首日分时自动缓存

- 当前实现优先复用东方财富新股列表做扫描，默认筛选“上市日期等于今天”的北交所新股
- 分钟线数据源使用 Tushare：
  - 今天：先尝试 `rt_min_daily`
  - 若无权限或未返回分钟线：自动回退到 `stk_mins`
  - 历史回补：直接使用 `stk_mins`
- 若 Tushare 分钟线受限，会再尝试东方财富分钟线兜底
  - 但如果东方财富返回的上市首日分钟串出现 `open = 0`，程序会取消本次缓存，并在执行界面提示“留待下次重试”
- 写入格式与现有 `trend_scorer.py` 完全一致，缓存后可直接参与 `WSI` 打分和离线调参
- 独立补缓存入口：双击根目录 [添加新股首日走势.bat](/C:/Users/ai/Desktop/北交所新股估值/添加新股首日走势.bat)
  - 逻辑为“按最新上市顺序往前扫，遇到本地已有缓存的代码就停止”
  - 执行过程中会实时提示“正在检查 / 已缓存 / 留待下次重试 / 命中已有缓存并停止”
  - 该入口使用独立的 `data/tushare_intraday_db` 请求日志，避免被主流程当天的 Tushare 调用次数挤占
  - 仓库提交约定：新增或更新的 `首日分时走势/*.csv` 需要随本次功能改动一并提交、推送，不单独留在本地

示例：

- `python tools\cache_listing_day_intraday.py`
- `python tools\cache_listing_day_intraday.py --date 2026-04-20`
- `python tools\cache_listing_day_intraday.py --codes 920188,920012 --force`
- `python tools\cache_listing_day_intraday.py --latest-until-cached --months 18`
- `python tools\add_new_ipo_intraday_cache.py`

## 当前默认参数状态

- 综合权重当前默认值：
  - `weight_comparable = 0.20`
  - `weight_industry_momentum = 0.80`
- 方法二当前保留的 `quick_method2_core_only` 试运行参数：
  - `price_range_width = 0.12`
  - `float_size_threshold = 1500`
  - `small_cap_premium = 0.10`
- 方法二 PE 参数当前默认状态：
  - `pe_low_threshold = 0.20`
  - `pe_discount_boost = 0.10`
  - `pe_high_threshold = 0.60`
  - `pe_premium_drag = -0.10`
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
