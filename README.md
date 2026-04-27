# 北交所新股估值

这是一个面向日常使用的本地版北交所新股估值工具。

它主要解决三件事：

1. 输入 6 位股票代码，生成首日估值 PDF 报告
2. 补齐招股说明书、上市公告书和首日分时走势等本地资料
3. 对策略参数做离线复核和 replay 观察

如果你只是使用者，优先看下面这几节即可，不需要先研究 `code/` 和 `tests/`。

## 你最常用的入口

- 生成估值报告：双击 `运行.bat`
- 手动调参：双击 `手动调参.bat`
- 补最新首日分时走势：双击 `添加新股首日走势.bat`

也可以直接用命令行：

- `python code\bse_ipo_valuation.py 920177`
- `python tools\tune_params.py --mode offline --param-name small_cap_premium --candidate-values 0.10,0.15`
- `python tools\cache_listing_day_intraday.py --latest-until-cached --months 18`

## 首次使用前

### 1. 环境

- 需要本机可直接运行 `python`
- Windows 下建议直接双击根目录的 `.bat` 入口

### 2. Tushare

- 如需启用 Tushare，请先在本机环境变量中设置 `TUSHARE_TOKEN`
- 仓库不会保存 token

### 3. 本地目录

程序会自动使用和维护以下目录：

- `公告文件/`：招股说明书、上市公告书等 PDF
- `首日分时走势/`：首日分时 CSV
- `输出/`：报告输出目录

## 日常怎么用

### 1. 生成估值报告

最简单的方式：

- 双击 `运行.bat`

或命令行：

- `python code\bse_ipo_valuation.py 920177`

运行后会提示输入 6 位代码，并生成带时间戳的 PDF 报告到 `输出/`。

主程序取资料的顺序是：

1. 先看本地 `公告文件/`
2. 若缺文件，再自动尝试北交所官网链路补下载
3. 若招股说明书仍缺失，则本次报告终止
4. 若上市公告书缺失，但招股说明书已拿到，则报告仍可继续生成

### 2. 手动调参

推荐直接双击：

- `手动调参.bat`

它会按顺序提示你：

1. 选择模式：`离线复核` 或 `replay 观察`
2. 选择输入方式：`单参数多候选值` / `手动候选组` / `候选文件`
3. 输入必要参数

每次手动调参启动时，系统会先扫描 `首日分时走势/` 里的 CSV。如果本地样本文件比 `data/offline_tuning/replay_dataset.json` 更新，会自动重建回放数据集；训练集和验证集样本数会随之更新。若外部数据源临时超时或拒绝连接，本次会继续使用旧回放数据集，不会中断调参；网络恢复后再次运行即可自动重试同步。

底层统一调用：

- `tools/tune_params.py`

三种模式分别是：

- `--mode search`：按内置阶段批量调参
- `--mode offline`：手动候选离线复核，输出到 `输出/调参/`
- `--mode observe`：手动候选 replay 观察，输出到 `输出/观察期/`

常见例子：

- `python tools\tune_params.py --mode offline --param-name small_cap_premium --candidate-values 0.10,0.15`
- `python tools\tune_params.py --mode observe --param-name pe_low_threshold --candidate-values 0.20,0.25 --codes 920012,920036,920183`
- `python tools\tune_params.py --mode offline --candidate "price_range_width=0.12,float_size_threshold=1500,small_cap_premium=0.10"`
- `python tools\tune_params.py --mode offline --candidate-file data\offline_tuning\candidate_sets\quick_method2_pe_candidate_set_v1.json`

手动调参现在有两个重要规则：

- 如果某个“和为 1”的二因子权重组只输入了一个因子，会自动补足另一个因子到 `1`
- 如果某个“和为 1”的多因子组只调了一个因子，其余因子会按当前 `策略参数.txt` 的现有比例自动缩放

### 3. 补最新首日分时走势

最简单的方式：

- 双击 `添加新股首日走势.bat`

常用命令：

- `python tools\add_new_ipo_intraday_cache.py`
- `python tools\cache_listing_day_intraday.py`
- `python tools\cache_listing_day_intraday.py --latest-until-cached --months 18`

这套缓存会直接写入 `首日分时走势/`，后续主程序和调参会自动复用。

### 4. 下载公告文件

常用命令：

- `python tools\download_bse_official_pdf.py 920177`
- `python tools\download_bse_official_pdf.py 920177 --document listing`
- `python tools\download_bse_official_pdf.py 920177 --document all`
- `python tools\download_bse_official_pdf.py 920177 --resolve-only`

下载结果会保存到 `公告文件/`。

## 输出文件怎么看

### 1. 估值报告

- 主程序 PDF 报告：`输出/`
- 报告末尾的“近期北交所新股首日表现一览”会按 `recent_months` 截断，只展示对应月份窗口内的样本。

### 2. 调参报告

- 离线复核报告：`输出/调参/`
- replay 观察报告：`输出/观察期/`

### 3. replay 观察报告里的关键统计

现在报告会直接给你：

- 本次样本数
- 与 baseline 相比涨幅误差缩小样本
- 与 baseline 相比涨幅误差增大样本
- 与 baseline 相比涨幅误差无变化样本
- 不可比较但前后相同样本
- 展示变化样本数
- 已省略未变化样本数

其中：

- “误差缩小 / 增大 / 无变化”只统计 baseline 与候选都能算出涨幅绝对误差的样本
- “不可比较但前后相同样本”表示两边都没有可比较误差，但整体输出结果完全一致
- 正文默认只展开“发生变化的样本”，避免全样本表过长

## 参数怎么改

所有用户需要直接维护的参数，都在：

- `策略参数.txt`

当前文件已经按用途分成几类：

- 用户最常调：目标股、综合权重、估值区间宽度
- 调参专用：离线回放与候选复核
- 偶尔调：方法二核心阈值
- 基本别动：方法一口径、走势细项、WSI 细项

如果你只想做日常使用，通常重点关注这些参数：

- `weight_comparable`
- `weight_industry_momentum`
- `price_range_width`
- `float_size_threshold`
- `small_cap_premium`
- `pe_low_threshold`
- `pe_discount_boost`
- `pe_high_threshold`
- `pe_premium_drag`

调参工具默认也会读取 `策略参数.txt` 里的这几项运行参数：

- `tuning_replay_months`
- `tuning_page_size`
- `tuning_train_ratio`
- `tuning_min_train_samples`
- `tuning_top_n`

## 当前默认口径

截至目前，主程序默认使用：

- 综合权重：`weight_comparable = 0.20`，`weight_industry_momentum = 0.80`
- 方法二核心：`price_range_width = 0.12`，`float_size_threshold = 1500`，`small_cap_premium = 0.10`
- 方法二 PE：`pe_low_threshold = 0.20`，`pe_discount_boost = 0.10`，`pe_high_threshold = 0.60`，`pe_premium_drag = -0.10`

`trend_balance` 和 `WSI` 的扩展调参结论目前保留在项目里，但还没有继续吸收到默认参数中。

## 如果你要看项目过程记录

优先看这几个位置：

- `docs/工作日志/`
- `data/offline_tuning/candidate_sets/`
- `输出/调参/`
- `输出/观察期/`

## 如果你不是开发者，可以忽略这些目录

- `code/`
- `tests/`
- `docs/设计文档/`

## 保留的工具脚本

当前项目里保留的是仍然有直接使用价值的工具：

- `tools/tune_params.py`：统一调参入口
- `tools/review_candidate_params.py`：复核已有候选参数集
- `tools/cache_listing_day_intraday.py`：缓存首日分时走势
- `tools/add_new_ipo_intraday_cache.py`：补最新上市样本缓存
- `tools/download_bse_official_pdf.py`：下载公告 PDF
- `tools/scan_pdf_samples.py`：批量扫描本地 PDF 样本

早期按单一主题拆分的专用观察脚本已经清理，避免入口重复；现在统一通过 `tools/tune_params.py` 和 `手动调参.bat` 使用。
