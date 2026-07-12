# 北交所新股估值

这是一个面向日常使用的本地版北交所新股估值工具。

它主要解决三件事：

1. 输入 6 位股票代码，生成首日估值 PDF 报告
2. 补齐招股说明书、上市公告书和首日分时走势等本地资料
3. 对策略参数做离线复核和 replay 观察

如果你只是使用者，优先看下面这几节即可，不需要先研究 `code/` 和 `tests/`。

## 你最常用的入口

- 生成估值报告：双击 `运行.bat`
- 调参（手动 / 自动）：双击 `调参.bat`
- 补最新首日走势、调参样本、缺公告重试和手工雪球参考：双击 `添加新股首日走势.bat`

也可以直接用命令行：

- `python code\bse_ipo_valuation.py 920177`
- `python tools\tune_params.py --mode auto`
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
- `xueqiu/`：用户手工保存的雪球 MHTML/TXT；刷新入口不会联网抓取
- `输出/`：报告输出目录
- `outputs/`：雪球学习、proxy、regime-break 和盘中指导等研究报告

## 日常怎么用

### 1. 生成估值报告

最简单的方式：

- 双击 `运行.bat`

或命令行：

- `python code\bse_ipo_valuation.py 920177`

运行后会提示输入 6 位代码，并生成带时间戳的 PDF 报告到 `输出/`。同时会生成同名 `_一览.txt`，方便复制代码、名称、申购日期、市盈率、最大申购上限、1手/2手/...建议申购门槛、正股/碎股是否需要抢时间、上市日期和估价区间。

主程序取资料的顺序是：

1. 先看本地 `公告文件/`
2. 若缺文件，再自动尝试北交所官网链路补下载
3. 若招股说明书仍缺失，则本次报告终止
4. 若上市公告书缺失，但招股说明书已拿到，则报告仍可继续生成

### 2. 调参

推荐直接双击：

- `调参.bat`

它会先提示你选择四种调参模式：

- `1 估值自动调参`：系统先按原估值评分搜索候选，再用本地 proxy、动态宽度、regime-break 和滚动中枢做第二级重排；重排胜者会成为本轮建议和下一轮搜索中心，结束时继续刷新完整影子报告
- `2 估值手动调参`：沿用原来的候选参数复核 / replay 观察模式
- `3 申购资金自动调参`：读取申购历史样本和手工分档标签，搜索申购资金主参数，确认接受后写入 `策略参数.txt`
- `4 申购资金手动调参`：查看 baseline / search / robustness / account-pool 等申购资金诊断

估值手动调参会继续按顺序提示你：

1. 选择模式：`离线复核` 或 `replay 观察`
2. 选择输入方式：`单参数多候选值` / `手动候选组` / `候选文件`
3. 输入必要参数

每次通过 `调参.bat` 启动调参时，系统会先扫描 `首日分时走势/` 里的 CSV，自动同步估值回放样本集 `data/offline_tuning/replay_dataset.json`，再重建申购资金历史样本集 `data/offline_tuning/subscription_history_sample.csv`、同步手工分档表，并刷新 `account_pool_history_*` 户数分布表。估值回放会优先复用旧数据集条目和 `data/offline_tuning/replay_items/代码.json` 单样本缓存，只为新增样本补齐回放资料；申购资金历史表会优先采用本次解析出的 `model_ready` 发行结果行，只有新解析不可用时才用旧高质量行兜底，避免临时解析失败造成样本降级。若外部数据源临时超时或拒绝连接，本次会继续使用旧样本集，不会中断调参；网络恢复后再次运行即可自动重试同步。

申购配售模型的历史样本表保存在 `data/offline_tuning/subscription_history_sample.csv`，这是可提交的训练样本产物。它按历史新股逐行记录发行价、网上发行数量、顶格申购金额、有效申购户数、有效申购股数、冻结资金、申购冻结天数、正股门槛、碎股门槛估算和字段来源。申购规模按网上发行口径处理：系统会用网上发行数量和发行价得到网上发行金额，用顶格申购金额判断单户上限，再用有效申购股数或冻结资金反推全市场有效申购资金池。正股/碎股门槛和是否需要抢时间，都是在这个口径上计算出来的。

如果申购规模相关字段缺失，通常是发行公告或发行结果公告还没有补齐。你可以双击 `添加新股首日走势.bat`，选择 `2. 刷新新上市新股数据（估值 replay / 申购 history / 缺公告重试）`，系统会重新尝试下载缺失公告并更新申购 history。

常用生成命令：

- `python tools\build_subscription_history.py`
- `python tools\build_subscription_history.py --download-missing-issue --download-missing-result`
- `python tools\build_subscription_history.py --download-missing-issue --download-missing-result --download-retries 3 --download-delay-seconds 5`
- `python tools\tune_subscription_prediction.py`
- `python tools\tune_subscription_prediction.py --mode search --top-n 8`
- `python tools\tune_subscription_prediction.py --mode robustness --top-n 8`
- `python tools\tune_subscription_prediction.py --mode account-pool`
- `python tools\tune_subscription_prediction.py --mode account-pool-prior --top-n 8`
- `python tools\tune_subscription_prediction.py --mode auto --top-n 8`

`model_ready=true` 表示该样本已有发行结果公告字段；`guaranteed_label_ready=true` 表示可用于正股门槛调模；`fractional_label_ready=true` 表示已有真实申购梯度或“顶格仍不足正股、全员拼时间抢碎股”标签，可用于碎股/抢时间调模。若公告没有申购梯度表，生成器会用网上获配户数、网上发行手数、冻结资金和配售规则生成 `allocation_fit_json`，作为后续拟合申购金额梯度的初始约束，并同步输出 `allocation_fit_confidence`、`allocation_fit_usable_for_tuning` 和 `allocation_fit_residual_json` 供调参筛样本使用。若本机 Python 没有 PDF 文本解析库，脚本仍会生成样本骨架，但公告字段会缺失；需要先补齐 `pdfplumber`/`pypdf` 等 PDF 解析依赖，或使用已配置好依赖的运行环境。

手工分档标签表保存在 `data/offline_tuning/subscription_ladder_labels.csv`。该表一行一个本地样本，`manual_ladder` 可按 `1+0=251.2;2+1=564;0+1=顶格抢时间` 这样的格式补录真实分档；暂未找到资料的样本留空即可。`tools\build_subscription_history.py` 和 `tools\tune_subscription_prediction.py` 会自动为新增样本补空行，调参时默认读取该表，把手工分档误差和抢时间标签纳入候选参数排序；如需临时排除，可加 `--no-ladder-labels`。

账户池曲线以发行结果公告中的网上获配户数和网上获配手数为硬约束，以手工分档表中的核验门槛为金额切点，并用上一只样本完成后累计形成的完整账户快照及逐点偏差带约束各获配层人数。新样本会重新插值细化其相邻门槛之间的旧切点，并形成一个只使用当时及更早数据的新快照；正式预测和历史回放读取目标日期之前的最新快照，因此最新结果最细且不引入未来函数。`allocation_fit_json` 中的 `compressed_extra_lots` 只用于公告总量验算，不作为账户资金分布写入账户池。雪球作者的账户区间或资金拆分只作人工印证参考，不自动进入申购门槛主模型。

`tools\tune_subscription_prediction.py` 是申购配售调参的评估入口：默认输出当前 baseline 的正股门槛误差、手工分档误差、顶格不足正股分类准确率和拟合残差摘要；`--mode search` 会枚举候选参数并排序展示；`--mode robustness` 会用不同起始样本数和历史窗口复核当前最优候选；`--mode account-pool` 会按最近样本的获配分桶估算不同申购金额阈值以上的大户数量；`--mode account-pool-prior` 会把近期获配分布转换成有效申购资金池下限，离线检查它对当前 best 参数的修正效果；`--mode auto` 会先展示搜索结果，再确认是否把申购资金主参数写入 `策略参数.txt`。

估值调参底层统一调用：

- `tools/tune_params.py`

三种模式分别是：

- `--mode search`：按内置阶段批量调参
- `--mode auto`：自动搜索参数组合，确认接受后写入 `策略参数.txt`
- `--mode offline`：手动候选离线复核，输出到 `输出/调参/`
- `--mode observe`：手动候选 replay 观察，输出到 `输出/观察期/`

常见例子：

- `python tools\tune_params.py --mode auto`
- `python tools\tune_params.py --mode offline --param-name small_cap_premium --candidate-values 0.10,0.15`
- `python tools\tune_params.py --mode observe --param-name pe_low_threshold --candidate-values 0.20,0.25 --codes 920012,920036,920183`
- `python tools\tune_params.py --mode offline --candidate "price_range_width=0.12,float_size_threshold=1500,small_cap_premium=0.10"`
- `python tools\tune_params.py --mode offline --candidate-file data\offline_tuning\candidate_sets\quick_method2_pe_candidate_set_v1.json`

自动调参的评分规则：

- 越新的本地样本权重越高
- 3 个月前的样本权重衰减到 0；最近 30 天上市样本存在时，总权重至少 50%
- 每个历史样本回放时，只使用该样本上市日前 `recent_days` 天内可见的新股样本
- 命中估值区间会提高组合分
- `price_range_width` 只读取当前手动参数，不参与自动调参搜索和排序扣分；系统会单独展示当前手动区间宽度对应的诊断扣分
- 每轮核心搜索后，默认取前 20 个候选，加上 baseline、阶段起点和核心最优进行本地学习重排
- 综合学习分默认由原核心分 45%、保守动态区间 35%、regime-break 15%、滚动中枢 5% 组成
- 保守线采用约 15% 平均宽度的本地动态区间；超过 17% 的平均宽度会扣分，避免靠放宽区间提高命中率
- 重排只使用本地发行字段、候选模型输出和更早上市样本；不读取作者价格、文章正文或作者评分

自动调参的搜索规则：

- 第 1 轮使用粗步长，后续轮次围绕上一轮最优参数继续细步长搜索
- 搜索模块已拆为更细的参数项，例如综合权重、北交所折扣、流通盘阈值、小盘溢价、PE 低估/高估修正、样本权重、走势权重和强弱走势参数
- 走势参数包括 `industry_trend_weight` / `market_sentiment_weight`，以及 `trend_strong_boost`、`trend_weak_discount`、`trend_strong_threshold`、`trend_weak_threshold`
- 每轮默认最多评估 650 组候选，时间上限 180 秒
- 每轮结束后，窗口会询问是否接受当前累计最优参数并写入，或进入下一轮继续搜索
- 窗口会同时显示“核心评分最优”和“两级重排最优”；两者不一致时，参数建议与下一轮中心以两级重排结果为准
- `--auto-local-rerank-top-n` 可调整第二层候选数；`--no-auto-local-rerank` 可临时恢复旧排序做对照

如果接受自动调参结果，系统会把两级重排胜者的正式可调参数写入 `策略参数.txt`，并在根目录维护 `自动调参记录.txt`；记录按最新在最前排列。如果不接受，本次不会改参数，后续仍可手动修改 `策略参数.txt`。无论接受、暂不接受还是没有更优参数，自动调参都会写出 `调参/valuation_auto_shadow_context_latest.json` 并刷新扩展评价报告。proxy、动态宽度和 regime-break 本身仍不作为 `策略参数.txt` 的新参数写入，但会通过第二级评分影响现有参数的选择。

手动调参现在有两个重要规则：

- 如果某个“和为 1”的二因子权重组只输入了一个因子，会自动补足另一个因子到 `1`
- 如果某个“和为 1”的多因子组只调了一个因子，其余因子会按当前 `策略参数.txt` 的现有比例自动缩放

### 3. 补最新上市样本资料

最简单的方式：

- 双击 `添加新股首日走势.bat`

启动后可以选择：

- `1. 刷新新股首日走势`：只补首日分时 CSV。
- `2. 刷新新上市新股数据（估值 replay / 申购 history / 缺公告重试）`：补估值回放样本、申购资金历史样本、手工阶梯标签上下文和样本 manifest；缺失的发行公告/发行结果公告也会自动重试下载。申购 history 按 replay、PDF 和解析器版本签名逐代码增量刷新；account-pool 只从最早变化样本日期向后续算。排障时可分别使用 `--force-rebuild-subscription-history` 和 `--force-rebuild-account-pool` 强制全量重建。
- `3. Refresh Xueqiu reference from manual files in xueqiu folder`：读取根目录 `xueqiu/` 中手工保存的 `.mhtml` 和 `.txt`，自动完成语料导入、区间抽取、覆盖审计、author-rule score、作者/模型融合、本地规则蒸馏和影子报告刷新。该功能不访问雪球，也不自动下载文章。

雪球参考的日常用法：

1. 在浏览器中把文章保存为 MHTML，或把正文保存为 TXT。
2. 把文件放入根目录 `xueqiu/`；原文件可以长期保留，重复刷新会按稳定文章 ID 去重/覆盖。
3. 双击 `添加新股首日走势.bat` 并选择第 3 项。

也可命令行直达：`添加新股首日走势.bat 3 --no-pause`。

如果当天有正在交易的新股，程序会在盘中跳过当日上市样本，不写入不完整的首日分时 CSV，并提示盘后再运行。默认盘后缓存时间为 15:30 后。

盘后补缓存时，取数顺序为：

1. 先取 Tushare 分钟线
2. Tushare 失败时打印失败原因，再取东方财富分钟线
3. 东方财富也失败时继续打印失败原因，并尝试读取项目根目录中用户拖入的 Excel/CSV 文件

如果两个在线数据源都失败，可以把该股票首日分钟文件拖入项目根目录后重试。文件名建议包含股票代码，例如 `920200.BJ.xlsx`；表头可使用常见字段如 `代码`、`日期` / `时间`、`开盘价(元)`、`最高价(元)`、`最低价(元)`、`收盘价(元)`、`成交额(百万)`、`成交量`。成功后会统一写入 `首日分时走势/代码.csv`。

写入缓存时，系统会根据价格、成交量和成交额自动判断原始数据的单位，并统一保存为 `volume=股`、`amount=元`。如果你担心历史 CSV 里混有“手/股”或“万元/元”等不同口径，可以先运行一次归一化命令；它只会重写需要换算的文件。

常用命令：

- `python tools\add_new_ipo_intraday_cache.py`
- `python tools\cache_listing_day_intraday.py`
- `python tools\cache_listing_day_intraday.py --latest-until-cached --months 18`
- `python tools\cache_listing_day_intraday.py --normalize-existing`
- `python tools\sync_offline_tuning_dataset.py`

首日走势缓存会直接写入 `首日分时走势/`，后续主程序和调参会自动复用；调参样本刷新会更新 `data/offline_tuning/` 下的 replay、申购 history、手工标签上下文和样本 manifest。

### 4. 下载公告文件

常用命令：

- `python tools\download_bse_official_pdf.py 920177`
- `python tools\download_bse_official_pdf.py 920177 --document listing`
- `python tools\download_bse_official_pdf.py 920177 --document issue`
- `python tools\download_bse_official_pdf.py 920177 --document result`
- `python tools\download_bse_official_pdf.py 920177 --document all`
- `python tools\download_bse_official_pdf.py 920177 --resolve-only`

下载结果会保存到 `公告文件/`。

自动下载现在会对 PDF 做完整性校验：下载后会检查文件大小、PDF 文件头和结束标记；如果本地已有 PDF 不完整，主流程会跳过该文件并重新下载，避免半截公告文件被当成可用文件复用。

## 输出文件怎么看

### 1. 估值报告

- 主程序 PDF 报告：`输出/`
- 报告一览 TXT：`输出/` 中同名 `_一览.txt`
- 报告末尾的“近期北交所新股首日表现一览”会按 `recent_days` 截断，只展示对应天数窗口内的样本。

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

- 仅手动可调：单只标的输入，例如行业、可比公司、流通老股
- 仅手动可调：正式估值和自动调参都会使用，但自动调参不会修改，例如 `recent_days`、`price_range_width`
- 自动调参可写回：综合权重、北交所折价、方法二样本修正、趋势参数、WSI 权重
- 训练/回放运行设置：调参入口构建数据集和展示结果时使用

如果你只想做日常使用，通常重点关注这些参数：

- `weight_comparable`
- `weight_industry_momentum`
- `price_range_width`
- `recent_days`
- `float_size_threshold`
- `small_cap_premium`
- `pe_low_threshold`
- `pe_discount_boost`
- `pe_high_threshold`
- `pe_premium_drag`

自动调参会搜索并可能写回 `自动调参可写回` 分组里的参数；`price_range_width` 只作为当前手动区间宽度参与命中判断，不会被自动放大。

调参工具默认也会读取 `策略参数.txt` 里的这几项运行参数：

- `tuning_replay_months`
- `tuning_page_size`
- `tuning_top_n`
- `tuning_train_ratio`（仅旧版批量搜索与手动候选复核使用）
- `tuning_min_train_samples`（仅旧版批量搜索与手动候选复核使用）

## 当前默认口径

当前默认参数以 `策略参数.txt` 为准。README 不再复制具体数值，避免自动调参写回后手册滞后。

你需要确认当前口径时，直接看 `策略参数.txt` 里的这些分组：

- `仅手动可调`：单只标的输入、估值区间、回看天数、PE 口径等。
- `自动调参可写回`：综合估值权重、方法二样本修正、趋势参数和 WSI 细项权重。
- `申购资金预测`：正股/碎股门槛模型、申购规模修正、账户资金池先验和保护垫。
- `训练/回放运行设置`：调参入口构建 replay、输出 Top N 和旧版手动复核设置。

有两条固定使用口径不靠 README 里的数字维护：正式估值与调参回放统一使用首日成交均价；本地首日分时 CSV 写入时会统一为 `volume=股`、`amount=元`。申购资金模型的申购规模口径仍按网上发行数量、发行价、顶格申购金额、有效申购股数/冻结资金来计算，具体参数值同样以 `策略参数.txt` 为准。

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
- `tools/sync_offline_tuning_dataset.py`：刷新估值 replay、申购 history、手工标签上下文和缺公告重试
- `tools/download_bse_official_pdf.py`：下载公告 PDF
- `tools/scan_pdf_samples.py`：批量扫描本地 PDF 样本

早期按单一主题拆分的专用观察脚本已经清理，避免入口重复；现在统一通过 `tools/tune_params.py` 和 `调参.bat` 使用。
