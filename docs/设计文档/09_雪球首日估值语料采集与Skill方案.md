# 雪球首日估值语料采集与 Skill 方案

## 背景

当前本地估值模型主要基于可比公司 PE、同行业历史首日涨幅和近期新股情绪溢价。经过 2026-07-10 的固定区间命中率扫描后，综合估值命中率仍然偏低，说明仅依赖结构化市场数据不足以覆盖首日交易中的经验判断。

用户提供了四个雪球专栏作者，要求阅读其“上市估值、上市前瞻、首日价格分析、首日股价”类文章，抽象成可复用的首日估值 skill，并用本地数据验证有效性。

## 今日可访问性结论

2026-07-11 已完成第一轮访问探测：

- 普通 `curl` 可返回 HTTP 200，但内容是阿里云 WAF 挑战页，不能直接读正文。
- 内置浏览器本轮未能创建可控标签页，不作为后续采集依赖。
- 使用本机 Chrome + Playwright 渲染后，可以访问雪球主页、时间线接口和文章详情页。
- 四个作者均可读到未登录正文，页面仍显示“登录”按钮，但详情页正文、评论区和专栏元信息可见。

探测产物：

- `tools/xueqiu_access_probe.cjs`
- `outputs/xueqiu_access_probe.json`

首轮命中概况：

| 作者 | 用户 ID | 主页识别名 | 前序页命中 |
|---|---:|---|---:|
| 条条道路通罗马Lee | `8889879564` | 条条道路通罗马Lee | 前 5 页命中 22 篇 |
| 无房户小侯 | `8692639756` | 无房户小侯 | 前 3 页命中 13 篇 |
| 月半928 | `9833039947` | 月半928 | 前 2 页命中 6 篇 |
| 兔子兔888 | `8851207271` | 兔子兔888 | 前 5 页命中 94 篇 |

## 目标

- 建立可复现的雪球文章语料采集流程，避免手工复制正文。
- 把文章正文保存为结构化本地语料，保留来源、作者、时间、标题、URL、正文、关键词命中和初步字段抽取。
- 从语料中抽象“作者经验规则”，形成首日估值 skill 的参考资料和执行流程。
- 将作者规则映射到本地已有字段，区分可回测、需派生、需人工标注和暂不可验证的规则。
- 用本地 replay 数据验证作者规则是否提升首日均价区间命中率、方向判断和避坑能力。

## 非目标

- 不绕过登录、验证码、付费墙或网站安全校验。
- 不把雪球网页访问逻辑耦合进正式估值主流程。
- 不把作者观点直接当成模型答案；必须通过字段映射和本地回测过滤。
- 不一次性追求全文语义完美抽取；先保证语料完整、可复现、可迭代。

## 采集范围

初始作者：

- `https://xueqiu.com/u/8889879564`
- `https://xueqiu.com/u/8692639756`
- `https://xueqiu.com/u/9833039947`
- `https://xueqiu.com/u/8851207271`

初始关键词：

- `上市估值`
- `上市前瞻`
- `首日价格分析`
- `首日股价`

初始页数策略：

- 默认每个作者最多翻 `20` 页时间线。
- 如果连续 `3` 页无关键词命中，则停止该作者。
- 每次请求之间保留短等待，降低触发风控概率。

## 本地目录与文件

语料目录：

- `data/xueqiu_corpus/articles/`：单篇文章 JSON。
- `data/xueqiu_corpus/articles.jsonl`：所有文章的 JSONL 汇总，便于后续批处理。
- `data/xueqiu_corpus/index.json`：采集批次、作者、关键词、文章清单和质量统计。
- `outputs/xueqiu_corpus_collect_*.json`：每次采集运行报告。

后续 skill 目录：

- `skills/bj-ipo-first-day-valuation/SKILL.md`
- `skills/bj-ipo-first-day-valuation/references/corpus_schema.md`
- `skills/bj-ipo-first-day-valuation/references/heuristic_taxonomy.md`
- `skills/bj-ipo-first-day-valuation/references/local_field_mapping.md`
- `skills/bj-ipo-first-day-valuation/scripts/`

项目内先维护 repo-local skill，待语料和回测稳定后，再决定是否安装到 `$CODEX_HOME/skills`。

## 文章 Schema

单篇文章 JSON 使用以下核心字段：

- `source`：固定为 `xueqiu`。
- `user_id`、`author_name`。
- `status_id`、`url`、`canonical_url`。
- `title`、`created_at_ms`、`created_at_text`、`collected_at`。
- `matched_keywords`。
- `text`：清洗后的正文。
- `text_length`。
- `stock_mentions`：正文中出现的 `$简称(市场代码)$`、`920xxx` 等标的线索。
- `article_type`：按标题和正文粗分为 `listing_valuation`、`listing_preview`、`first_day_price_analysis`、`subscription_strategy`、`other`。
- `extracted`：初步规则抽取结果。
- `quality`：正文是否可读、字段是否足够、是否疑似截断、是否有发行价/估值/首日判断。

`extracted` 初始字段：

- `issue_price`
- `market_cap_range`
- `price_range`
- `target_pe`
- `float_market_cap`
- `total_market_cap`
- `comparable_companies`
- `industry_keywords`
- `listing_date_hints`
- `first_day_view`
- `risk_phrases`
- `author_rule_phrases`

## 采集器设计

新增脚本：

- `tools/xueqiu_corpus_collect.cjs`

核心流程：

1. 用本机 Chrome + `playwright-core` 打开作者主页，建立雪球 cookie/session。
2. 调用 `statuses/original/timeline.json` 翻页读取作者原发文章列表。
3. 用关键词过滤候选文章。
4. 打开候选详情页，读取渲染后的正文。
5. 清洗正文，抽取标题、发布时间、作者、正文和初步字段。
6. 写入单篇 JSON、JSONL 汇总和运行报告。

访问约束：

- 只读访问，不登录、不提交表单、不处理验证码。
- 遇到 WAF 挑战、验证码、正文不可读时记录 `quality.readable=false`，不中断全局采集。
- 保留 `--max-pages`、`--max-articles-per-author`、`--delay-ms` 参数。
- 默认复用已有文章，除非传入 `--refresh`。

## 规则抽象路径

语料采集完成后，按三层抽象：

1. **文章级事实抽取**：发行价、估值区间、流通市值、可比公司、作者首日判断、风险提示。
2. **作者级风格归纳**：估值锚、行业偏好、情绪溢价判断、负面过滤项、强弱市场下的修正。
3. **模型级可检验规则**：映射到本地字段后形成特征、阈值或打分项。

规则分类：

- `valuation_anchor`：PE/PS/PB、市值区间、可比公司估值。
- `liquidity_supply`：流通市值、发行价、网上发行规模、老股转让。
- `sector_heat`：行业热点、国产替代、机器人/半导体/汽车等主题热度。
- `fundamental_quality`：增长、毛利率、客户集中度、现金流、产能利用率。
- `market_mood`：近端次新表现、同日新股、全市场风险偏好。
- `listing_microstructure`：竞价、顶格申购资金、盘小低价、首日换手预期。
- `risk_filter`：高价、高 PE、业绩下滑、客户集中、出口汇率、周期风险。

## 本地字段映射

优先映射到已有本地数据：

- 发行价、发行 PE、发行规模、网上发行、申购资金：公告解析和申购 history。
- 首日均价、开盘/收盘涨幅、换手、分时强弱：`首日分时走势/` 和 replay 数据。
- 行业映射、同行业历史表现：现有 `industry_mapping` 和方法二样本池。
- 近期市场情绪、次日/第三日赚钱效应：方法三已有 post-listing 派生字段。
- 可比公司 PE：现有方法一可比估值数据。

需要派生或人工标注：

- 作者给出的市值区间和对应价格区间。
- 作者主观结论：偏强、一般、回避、竞价可能超顶等。
- 主题热度标签和热点叙事。
- “看点较少”“预期被超顶”“非科技不受待见”等语义规则。

## 验证方案

第一阶段验证只评估“规则是否带来增量”，不直接替换现有模型：

- 基线：当前综合估值模型。
- 对照一：作者文章中的显式价格区间/市值区间。
- 对照二：从作者规则抽取的打分模型。
- 对照三：基线模型 + 作者规则修正项。

指标：

- 首日成交均价落入估值区间命中率。
- 方向命中：预测强/弱与实际涨幅分位是否一致。
- 避坑能力：低分样本是否显著降低破发或低收益风险。
- Top 分位收益：高分样本的首日均价涨幅均值/中位数。
- 分作者、分月份、分行业稳定性。

防泄漏要求：

- 只使用文章发布时间早于标的上市日或上市前分析文章。
- 评论区收评和上市后复盘不得用于上市前预测规则训练。
- 对同一文章中“上市后补充评论”要与正文分离。

## 2026-07-11 首轮落地结果

已新增语料采集器：

- `tools/xueqiu_corpus_collect.cjs`

四作者首轮聚合结果：

- 采集候选文章：77 篇。
- 可读正文：75 篇。
- 被雪球验证页挡住：2 篇。
- 抽到发行价：54 篇。
- 抽到首日价格区间：35 篇。
- 抽到目标 PE：21 篇。
- 作者分布：无房户小侯 13 篇，兔子兔888 36 篇，条条道路通罗马Lee 22 篇，月半928 6 篇。

已新增 repo-local skill：

- `skills/bj-ipo-first-day-valuation/SKILL.md`
- `skills/bj-ipo-first-day-valuation/references/corpus_schema.md`
- `skills/bj-ipo-first-day-valuation/references/heuristic_taxonomy.md`
- `skills/bj-ipo-first-day-valuation/references/local_field_mapping.md`

已新增首轮本地验证脚本：

- `tools/validate_xueqiu_author_ranges.py`

最新验证报告：

- `outputs/xueqiu_author_range_validation_20260711_154243.md`
- `outputs/xueqiu_author_range_validation_20260711_154243.json`

验证口径：

- 只检验文章中能抽出的显式首日价格区间。
- 只纳入文章发布时间早于本地识别上市日的样本。
- 多标的前瞻文章按标的分段抽取。
- 首日均价优先取 replay 的 `AVERAGE_PRICE`，缺失时只读本地 `首日分时走势/<code>.csv` 计算。

首轮验证结论：

- 可评估预测行：51 条。
- 行级区间命中：15 条，命中率 29.4%。
- 覆盖本地代码：23 只。
- 至少一位作者命中的代码：10 只，代码级命中率 43.5%。
- 同代码对照：当前 baseline 命中 3/22；2026-07-10 扫描最优候选命中 7/22。

解释：

- 作者显式区间整体有增量信号，尤其是兔子兔888和条条道路通罗马Lee在当前样本上表现更好。
- 月半928的多标的前瞻更多偏基本面合理估值，未充分覆盖后续首日情绪溢价，不能直接当首日均价区间使用。
- 下一阶段不应简单照搬作者区间，而应抽象“估值锚 + 情绪溢价 + 供给/题材修正”的规则，再与现有模型做组合。

## 2026-07-11 扩展覆盖与 Author-Rule Score

根据本地样本均为 2026 年样本的约束，扩展采集改为从 2025-12-01 起按发文日期检查，并关闭关键词过滤，避免漏掉标题不含初始关键词但正文涉及上市前预测的文章。

扩展采集命令：

```powershell
C:\Users\Ai\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe tools\xueqiu_corpus_collect.cjs --since-date 2025-12-01 --no-keyword-filter --max-pages 80 --max-articles-per-author 1000 --stop-empty-pages 999 --delay-ms 500 --refresh-unreadable
```

扩展采集产物：

- `outputs/xueqiu_corpus_collect_20260711_155804.json`
- `outputs/xueqiu_local_sample_coverage_20260711_155832.md`
- `outputs/xueqiu_local_sample_coverage_20260711_155832.json`
- `outputs/xueqiu_author_rule_score_20260711_160221.md`
- `outputs/xueqiu_author_rule_score_20260711_160221.json`

覆盖审计结论：

- 本地样本数：42。
- 当前语料/缓存文章记录：180。
- 已发现四作者上市前预测的本地样本：39。
- 已抓到上市前可读正文：30。
- 只有候选/验证页但正文缺失：9。
- 有显式价格区间的本地样本：30。
- 未发现预测证据：`920050 爱伦医疗`。
- 只找到非上市前或非预测类文章：`920117 龙鑫智能`。

正文缺失样本不纳入规则评分。当前缺失 9 只均为已发现兔子兔888候选但详情页被验证/阻断或缓存过薄：

- `920086 科马材料`
- `920076 国亮新材`
- `920159 农大科技`
- `920180 爱得科技`
- `920166 海圣医疗`
- `920168 通宝光电`
- `920187 通领科技`
- `920078 族兴新材`
- `920028 新恒泰`

Author-rule score 首版口径：

- 仅使用上市前、预测类、正文可读且能抽到显式价格区间的作者证据。
- 作者权重：兔子兔888 1.0，条条道路通罗马Lee 0.9，无房户小侯 0.7，月半928 0.35。
- 先验证作者原始加权区间和作者中枢 ±10%，再进入与本地估值模型的组合阶段。

Author-rule score 首版结果：

- 可评分本地代码：30。
- 作者原始加权区间命中：11/29，命中率 37.9%。
- 作者中枢 ±10% 命中：10/29，命中率 34.5%。
- 与当前模型同代码交集 27 只中，作者中枢 ±10% 命中 10/27，命中率 37.0%。
- 同代码 baseline 命中 3/27，命中率 11.1%。
- 同代码 2026-07-10 扫描最优命中 8/27，命中率 29.6%。
- score 与实际首日均价涨幅 Spearman 为 0.873，高分组平均/中位实际涨幅为 465.3% / 450.1%，低分组为 104.6% / 94.9%。

阶段性判断：

- 尚未做到“四位作者预测过的本地样本全部抓到正文”，但已抓到的 30 只样本足够进行首版 author-rule score 验证。
- 当前 score 对区间命中有明显增量，对强弱排序的信号更强。
- 下一阶段应优先做“本地综合估值 + author-rule score”的组合，而不是继续把作者显式区间当作独立答案。

## 2026-07-11 组合验证结果

已新增组合验证脚本：

- `tools/evaluate_xueqiu_author_model_blend.py`

验证目标：

- 比较当前本地模型、2026-07-10 扫描最优模型、作者显式区间和二者融合后的表现。
- 验证 author-rule score 是只适合排序，还是也能作为目标价中枢修正项。
- 验证本地模型不可用时，作者区间能否作为兜底估值来源。

组合方式：

- `alpha` 表示作者中枢权重，扫描 `0.1` 到 `0.9`。
- 区间宽度扫描 `±8%/±10%/±12%/±15%/±20%`。
- `fallback` 表示当本地模型不可用时，是否允许直接使用作者区间。
- 结果只写入 `outputs/` 报告，不写回正式参数。

核心报告：

- `outputs/xueqiu_author_model_blend_scan_sample_20260711_161509_496929.md`
- `outputs/xueqiu_author_model_blend_author_scored_20260711_161515_071510.md`
- `outputs/xueqiu_author_model_blend_all_actual_20260711_161521_340317.md`

2026-03 起扫描样本（31 只）：

- 当前参数模型：3/31，全样本命中率 9.7%。
- 2026-07-10 扫描最优：8/31，全样本命中率 25.8%。
- 作者固定 ±10%：10/31，全样本命中率 32.3%。
- 作者原始加权区间：11/31，全样本命中率 35.5%。
- 最优融合：`current_params_author_blend_a0.8_w0.20_fallback`，17/31，全样本命中率 54.8%，可用样本命中率 56.7%。
- 固定 ±10% 口径下，最优仍为 `author_fixed10`，10/31。
- 不启用作者兜底时，最优为 `current_params_author_blend_a0.8_w0.20`，12/31。

作者覆盖样本（27 只）：

- 当前参数模型：3/27。
- 2026-07-10 扫描最优：8/27。
- 作者固定 ±10%：10/27。
- 作者原始加权区间：11/27。
- 最优融合：17/27，全样本命中率 63.0%。

全量有实际均价样本（39 只）：

- 当前参数模型：4/39。
- 2026-07-10 扫描最优：9/39。
- 作者固定 ±10%：10/39。
- 作者原始加权区间：11/39。
- 最优融合：18/39，全样本命中率 46.2%，可用样本命中率 48.6%。
- 固定 ±10% 口径下，最优为 `scan_best_author_blend_a0.3_w0.10_fallback`，12/39。

正式化原则：

- `±20%` 最优组合用于观察研究上限，不直接作为正式默认区间。
- 正式模型应拆成三个显式开关：作者中枢修正、作者兜底、作者驱动区间宽度。
- 默认上线候选应优先考虑固定 ±10% 或分层宽度，而不是全局放宽。
- 对作者兜底样本必须保留证据来源和作者明细，避免模型不可用时静默替换。
- 手工补齐缺失正文后，需要重新跑覆盖审计、author-rule score 和组合验证，确认增量样本没有改变权重结论。

## 2026-07-11 手工 MHTML 补采与复核

用户补充根目录 `新建文件夹/` 下的 9 个雪球 MHTML 文件，并确认 `920050 爱伦医疗` 在新股上市时名称为 `爱舍伦`。本轮新增手工导入与别名处理：

- 新增 `tools/import_manual_xueqiu_mhtml.py`，将手工保存的雪球 MHTML 解析为既有 `data/xueqiu_corpus/articles/<user>_<status>.json`、`index.json` 和 `articles.jsonl` 格式。
- 在 `tools/validate_xueqiu_author_ranges.py` 中加入本地样本别名：`爱伦医疗 -> 爱舍伦`。
- 手工导入仅保留正文里的显式股票标签，过滤 MHTML 页面行情组件中的匿名股票链接，避免多标的窗口切分被页面噪声污染。

最终导入报告：

- `outputs/xueqiu_manual_mhtml_import_20260711_163150.json`

导入结果：

- MHTML 文件：9 个。
- 成功导入：9 个。
- 导入错误：0 个。
- 补齐正文代码：`920086 科马材料`、`920076 国亮新材`、`920159 农大科技`、`920180 爱得科技`、`920166 海圣医疗`、`920168 通宝光电`、`920187 通领科技`、`920078 族兴新材`、`920028 新恒泰`。
- 语料索引统计更新为：145 篇文章，143 篇可读，2 篇仍为验证/阻断页，59 篇抽到价格区间。

复核报告：

- `outputs/xueqiu_local_sample_coverage_20260711_163201.md`
- `outputs/xueqiu_local_sample_coverage_20260711_163201.json`
- `outputs/xueqiu_author_range_validation_20260711_163201.md`
- `outputs/xueqiu_author_range_validation_20260711_163201.json`
- `outputs/xueqiu_author_rule_score_20260711_163211.md`
- `outputs/xueqiu_author_rule_score_20260711_163211.json`
- `outputs/xueqiu_author_model_blend_scan_sample_20260711_163212_001691.md`
- `outputs/xueqiu_author_model_blend_author_scored_20260711_163211_983588.md`
- `outputs/xueqiu_author_model_blend_all_actual_20260711_163212_003480.md`

覆盖复核结论：

- 本地样本数：42。
- 已发现四作者上市前预测的本地样本：40。
- 已抓到上市前可读正文：39。
- 有显式价格区间的本地样本：39。
- 仍缺正文：`920050 爱伦医疗`，已通过别名定位到兔子兔888的 `爱舍伦上市估值` 候选，但详情页仍是验证页。
- 另有 `920183 海菲曼` 只有阻断/薄缓存，尚未形成可用上市前预测证据。
- `920117 龙鑫智能` 仍仅找到非上市前或非预测类文章。

显式区间验证更新：

- 可评估预测：75 条。
- 行级区间命中：22/75，命中率 29.3%。
- 覆盖本地代码：38 只。
- 至少一位作者命中的代码：17 只，代码级命中率 44.7%。
- 同代码 baseline 对照：3/30，命中率 10.0%。
- 同代码 2026-07-10 扫描最优候选对照：8/30，命中率 26.7%。

Author-rule score 更新：

- 可评分本地代码：39。
- 作者原始加权区间命中：16/38，命中率 42.1%。
- 作者中枢 ±10% 命中：14/38，命中率 36.8%。
- 与当前模型同代码交集 30 只中，作者原始加权区间命中 12/30，命中率 40.0%；baseline 命中 3/30，扫描最优命中 8/30。
- score 与实际首日均价涨幅 Spearman 为 0.893，排序信号继续稳定。

组合验证更新：

- 2026-03 起扫描样本（31 只）：最佳融合 `current_params_author_blend_a0.8_w0.20_fallback` 命中 19/31，全样本命中率 61.3%，可用样本命中率 63.3%。
- 作者覆盖样本（30 只）：同一最佳融合命中 19/30，全样本命中率 63.3%。
- 全量有实际均价样本（39 只）：最佳融合变为 `current_params_author_blend_a0.9_w0.20_fallback`，命中 22/39，全样本命中率 56.4%，可用样本命中率 57.9%。
- 全量样本中，作者固定 ±10% 命中 14/39；作者原始加权区间命中 16/39。

本轮判断：

- 手工补采后，除 `920050 爱伦医疗/爱舍伦` 外，四位作者已预测过的本地样本正文基本补齐。
- 新增早期样本没有推翻“作者信号强于本地 baseline、与本地模型融合提升明显”的结论，反而提高了 scan sample 和 all actual 的最佳融合命中率。
- `920050` 需要继续人工补正文或等待可访问缓存；`920183` 如后续发现可读预测也应补入。补齐后再重跑覆盖审计和组合验证。

## 2026-07-11 作者逻辑蒸馏方案

当前 `作者中枢 + 本地模型` 融合命中率提升显著，但它仍依赖雪球作者的外部显式估值。下一阶段目标是把作者信号从“外部答案”转化为“本地可复刻的逻辑”，在不接入雪球正文或新增外部信息时，也能提升本地估值命中率。

### 目标

- 解释作者为什么能修正本地模型：作者在哪些样本上明显优于当前模型，依赖的可能是供给弹性、近期情绪、行业题材、估值容忍度还是模型不可用兜底。
- 从本地已有字段构造 `author_proxy_score`，作为作者逻辑的本地代理。
- 验证 `author_proxy_score` 是否能在不使用作者目标价的情况下改善命中率、排序和避坑能力。
- 区分可内化信号和不可内化信号：前者进入本地规则候选，后者保留为需要外部语料或人工判断的限制。

### 分析对象

把样本按当前模型、作者区间和实际首日均价拆成几类：

- `model_miss_author_hit`：本地模型未命中、作者命中，是蒸馏重点。
- `model_hit_author_hit`：双方命中，用于识别共同有效特征。
- `model_hit_author_miss`：作者误判，用于识别作者过拟合或情绪过热风险。
- `model_miss_author_miss`：双方失败，用于识别现有字段无法解释的外部冲击或区间宽度问题。
- `model_unavailable_author_hit`：本地模型不可用、作者命中，用于抽象兜底逻辑。

### 残差拆解

对每只可比样本计算三类残差：

```text
model_predicted_change = 本地模型中枢 / 发行价 - 1
author_predicted_change = 作者中枢 / 发行价 - 1
actual_change = 首日成交均价 / 发行价 - 1

author_delta_vs_model = author_predicted_change - model_predicted_change
actual_residual_vs_model = actual_change - model_predicted_change
author_error = author_predicted_change - actual_change
```

如果某个本地字段同时解释 `author_delta_vs_model` 和 `actual_residual_vs_model`，说明作者提升中存在可内化逻辑；如果只解释 `author_delta_vs_model` 但不解释实际残差，说明更可能是作者风格噪声。

### 本地代理特征

第一版仅使用本地 replay、分时、公告解析缓存和已有调参产物，不读雪球正文，不新增外部数据：

- `issue_price`：低价弹性、高价扣分。
- `after_issue_pe` 与 `industry_pe_new`：发行估值相对行业估值的折溢价。
- `float_market_cap`、`online_issue_num`、`old_shares`：首日流通供给和老股抛压。
- `top_apply_marketcap`：顶格申购资金压力和资金关注度。
- `industry_primary`、`industry_secondary`：行业题材代理。
- `recent_bse_mood`：上市日前近 3/5/10 只北交所新股首日均价涨幅均值、中位数和强弱分位。
- `same_industry_recent_mood`：同一级或二级行业近端新股表现，样本不足时回退全市场近端情绪。
- `model_availability`：方法一/方法二/方法三是否可用、当前模型是否不可用。
- `model_uncertainty`：当前模型与扫描最优预测差异、估值区间宽度、方法可用数。

### Proxy Score 第一版

第一版不直接训练复杂模型，先做可解释的分项打分：

```text
author_proxy_score =
  liquidity_elasticity_score
+ valuation_tolerance_score
+ recent_mood_score
+ sector_proxy_score
+ subscription_attention_score
- supply_overhang_penalty
+ model_uncertainty_score
```

每个分项输出 `[-2, 2]` 或 `[-3, 3]` 的离散分，并保留触发原因。整体分数用于：

- 中枢修正：`proxy_target = model_target * (1 + adjustment_pct)`。
- 情绪兜底：当本地模型不可用时，使用发行价和 proxy 预测涨幅生成本地兜底目标。
- 宽度分层：proxy 分数高、近期情绪强或模型不确定性高时，给更宽区间；普通样本保持窄区间。

### 验证口径

报告分两层：

1. **归因报告**：说明哪些本地字段最能解释作者优于模型的样本，输出分组命中率、均值/中位残差、Spearman/相关性和代表样本。
2. **Proxy 回测报告**：比较当前模型、扫描最优、作者融合上限和 `author_proxy_score` 候选。

候选模型至少包括：

- `proxy_score_rank_only`：只检验 score 与实际涨幅排序。
- `current_params_proxy_center`：当前模型中枢按 proxy 分数修正，固定 `±10%/±15%/±20%`。
- `scan_best_proxy_center`：扫描最优模型中枢按 proxy 分数修正。
- `proxy_fallback`：本地模型不可用时用 proxy 生成兜底目标。
- `proxy_width_layered`：按 proxy 强弱和模型不确定性动态区间宽度。

防泄漏要求：

- 所有近端情绪特征只使用标的上市日前已经上市的样本。
- 不使用作者目标价、作者文章正文或导出的作者情绪词作为 proxy 特征。
- 可以把作者结果作为 teacher label 做归因，但不能作为 proxy 预测输入。
- 报告必须同时列出全样本命中率和可用样本命中率，避免兜底逻辑把不可用样本静默剔除。

### 阶段验收

- 若 `author_proxy_score` 与实际首日涨幅 Spearman 明显高于当前模型，说明排序逻辑可内化。
- 若 proxy 修正后固定 `±10%` 或 `±15%` 命中率超过当前模型和扫描最优，说明可作为正式候选参数。
- 若 proxy 只能在 `±20%` 宽区间下提升，则暂列为观察项，不写入默认估值。
- 若归因显示作者优势主要来自本地字段无法覆盖的公告语义或市场体感，则保留为“外部信息依赖”，不强行规则化。

## 2026-07-11 作者逻辑蒸馏首轮结果

已新增脚本：

- `tools/analyze_xueqiu_author_logic_distillation.py`

脚本输出：

- 作者增量归因：按 `model_miss_author_hit`、`model_hit_author_miss`、`model_unavailable_author_hit` 等类别解释作者在哪些样本上提供增量。
- 本地 proxy 特征：发行价、流通市值、老股比例、发行 PE/行业 PE、近端新股情绪、同业近端表现、顶格资金、模型不确定性。
- 本地 proxy 候选：固定步长中枢修正、滚动历史校准、动态宽度分层、模型不可用时的本地情绪兜底。

核心报告：

- `outputs/xueqiu_author_logic_distillation_all_actual_20260711_165001_537058.md`
- `outputs/xueqiu_author_logic_distillation_scan_sample_20260711_165020_731086.md`
- `outputs/xueqiu_author_logic_distillation_author_scored_20260711_165032_647799.md`

三组结果：

- 全量有实际均价样本（39 只）：最佳 proxy 为 `current_params_proxy_step0_layered_fallback`，命中 16/39，全样本命中率 41.0%，可用样本命中率 41.0%；proxy score 与实际涨幅 Spearman 为 0.462。
- 2026-03 起扫描样本（31 只）：最佳 proxy 为 `current_params_proxy_step0_layered_fallback`，命中 13/31，全样本命中率 41.9%，可用样本命中率 41.9%；proxy score 与实际涨幅 Spearman 为 0.520。
- 作者覆盖样本（30 只）：最佳 proxy 为 `scan_best_rolling_proxy_a0.75_w0.20`，命中 12/30，全样本命中率 40.0%，可用样本命中率 50.0%；proxy score 与实际涨幅 Spearman 为 0.550。

对照关系：

- scan sample 当前模型：3/31；扫描最优：8/31；作者固定 ±10%：11/31；作者原始加权区间：12/31；雪球融合上限：19/31。
- 本地 proxy 在 scan sample 上达到 13/31，高于当前模型和扫描最优，也略高于作者原始区间，但仍低于雪球融合上限。
- 全量样本中，本地 proxy 16/39，显著高于当前模型 4/39 和扫描最优 9/39，接近作者原始加权区间 16/39；但其 MAE 和 Spearman 仍明显弱于作者目标价。

归因结论：

- 作者可内化的第一层逻辑不是“精确中枢”，而是“高不确定性/强情绪/小流通供给时，应放宽区间并允许本地情绪兜底”。
- `proxy_score` 的排序有稳定信号：低分组实际涨幅显著低于高分组。scan sample 中低分组平均/中位实际涨幅为 163.2% / 125.0%，高分组为 376.6% / 317.7%。
- 相关性较强的本地字段包括：模型不确定性、同业近端表现、近 5 只新股情绪、发行价和顶格资金。它们能解释一部分作者增量，但不足以完全复刻作者中枢。
- 直接用 `proxy_score * 固定步长` 修正中枢不稳定，容易把强样本推过头；滚动历史校准能降低 MAE，但命中率上仍未超过动态宽度/兜底方案。

阶段判断：

- 可以把 `author_proxy_score` 作为本地模型的排序和宽度分层信号进入下一版候选参数。
- 暂不建议把 proxy 中枢修正写入正式默认参数；需要扩大样本或加入更多公告结构化字段后再验证。
- 作者融合仍保留更高上限，说明作者文章中存在本地字段尚未完全覆盖的信息，如公告语义、题材体感、产品辨识度和当时市场共识。

## 2026-07-11 二轮手工样本与隐式区间抽取

用户完成第二轮人工查漏补缺，根目录 `新建文件夹/` 下新增 49 个 MHTML 和 10 个 TXT 手工正文。本轮目标从“显式 `首日区间 xx-xx 元`”扩展为“可评分区间”：

- 显式价格区间：`对应首日股价 49.3-65.1 元`、`首日股价区间大概率在 18.8-28.2 元之间`。
- 单点目标价：`不低于 XX 元`、`可能达到 XX 元`、`中枢价格 XX 元`。
- PE 区间：`XX-XX 倍 PE`、`XX-XX 倍 25 年动态 PE`。若没有显式价格，则用本地 `ISSUE_PRICE / AFTER_ISSUE_PE` 推 EPS 后折算成价格，并在 `forecast_kind` 中标记为 `*_implied_price`。

新增/调整：

- `tools/import_manual_xueqiu_mhtml.py` 支持同时导入 `.mhtml` 和 `.txt`，TXT 会按作者名、标题/正文标的、上市日期推断成标准 article JSON。
- `tools/validate_xueqiu_author_ranges.py` 增加单点价和 PE 区间到可评分区间的折算逻辑。
- 多标的文章的区间归属改为“区间前最近出现的标的优先”，并且显式价格跨窗口优先于 PE/单点价折算，避免正文中提到其他新股时误退化为 PE 折算。
- 覆盖表术语从“显式区间”改为“可评分区间（显式或折算）”，折算项在明细中标记 `折算`。

导入与覆盖：

- 导入报告：`outputs/xueqiu_manual_article_import_20260711_174431.json`。
- 文件数：59，MHTML 49，TXT 10，导入成功 59，错误 0。
- 语料索引：184 篇文章，182 篇可读，2 篇验证/阻断页，85 篇抽到价格区间，94 篇抽到 target PE。
- 最新覆盖表：`outputs/xueqiu_author_coverage_table_20260711_174807.md/json/csv`。
- 本地样本 42 只；除 `920117 龙鑫智能` 外，41 只有上市前可读预测且均有可评分区间。
- 重点状态：`920050 爱伦医疗/爱舍伦` 已补齐兔子兔888和月半928可评分区间；`920183 海菲曼` 已补齐条条道路通罗马Lee和月半928上市前预测；`920117 龙鑫智能` 仍仅为非上市前/非预测证据。

最新验证：

- 可抽取区间验证：`outputs/xueqiu_author_range_validation_20260711_174806.md/json`。
- 可评估预测行：114，行级命中 38/114，命中率 33.3%。
- 覆盖有实际均价代码：40；至少一位作者命中的代码 25，代码级命中率 62.5%。
- Author-rule score：`outputs/xueqiu_author_rule_score_20260711_174807.md/json`。
- 可评分代码：41；有实际均价 40；作者原始加权区间命中 18/40，命中率 45.0%；作者中枢 ±10% 命中 16/40，命中率 40.0%。
- 调参样本交集 31 只中，作者原始加权区间命中 16/31，当前 baseline 3/31，扫描最优 8/31。
- 作者 score 与实际首日均价涨幅 Spearman 提升到 0.886。

最新组合与本地蒸馏：

- Scan sample 最佳融合：`outputs/xueqiu_author_model_blend_scan_sample_20260711_174819_193571.md/json`，`current_params_author_blend_a0.9_w0.20_fallback` 命中 20/31，命中率 64.5%，Spearman 0.890。
- All actual 最佳融合：`outputs/xueqiu_author_model_blend_all_actual_20260711_174819_275061.md/json`，同类候选命中 23/39，命中率 59.0%，Spearman 0.864。
- 本地-only proxy 最新结果：scan sample 13/31，全量 16/39，与上一轮基本一致。

阶段判断：

- 第二轮人工样本主要提高了作者语料覆盖和 author-rule 的稳定性，确认除龙鑫外当前本地样本基本已被四作者可读预测覆盖。
- 隐式区间抽取减少了“写法不同但可评分”的漏检；不过 PE 折算存在 EPS 口径差异，报告必须保留 `forecast_kind`，不能把折算价和原文显式价格混为一类。
- 本地-only proxy 未随覆盖补齐同步提升，说明作者新增信息对“外部作者上界”帮助更大，对“不接外部信息”的内化仍需要更多公告语义、题材标签和产品辨识度字段。

## 2026-07-11 Proxy 策略推进：排序、动态宽度、本地情绪兜底

上一轮蒸馏显示，`proxy_score * 固定步长` 的中枢修正不稳定，容易把强样本目标价推过头。因此下一版本地候选策略不把 proxy 作为目标价中枢修正器，而是明确拆成三个职责：

1. **proxy 排序**：只用于判断样本强弱层级、风险/弹性状态和策略解释，不直接改变已有本地模型中枢。
2. **动态宽度**：本地模型可用时保留其目标价，按 proxy 分位、模型不确定性、近端情绪强弱调整区间宽度。
3. **本地情绪兜底**：本地模型不可用时，使用上市日前已知的近端新股情绪、滚动 proxy/实际涨幅关系、发行价等本地字段生成兜底中枢。

候选策略原则：

- 默认中心价：
  - 本地模型可用：使用 `current_params` 或 `scan_best` 的原始 `target_price`。
  - 本地模型不可用：使用 `rolling_proxy_expected_change_pct`，不足时回退 `recent5/recent3/recent10` 中位涨幅，再不足时回退参数中的情绪 baseline。
- proxy 排序：
  - 每次回测在目标样本内计算 `proxy_rank_pct` 和 `proxy_tier`：低/中/高三档。
  - 报告必须列出低分组和高分组的实际首日涨幅均值/中位数，作为排序有效性检查。
- 动态宽度：
  - 保守档：低/中/高分别使用约 `±8%/±12%/±15%`，模型不可用或高不确定性时上调但不超过 `±20%`。
  - 平衡档：低/中/高分别使用 `±10%/±15%/±20%`。
  - 观察档：允许 `±12%/±20%/±25%`，只用于研究上限，不写入默认参数。
- 本地情绪兜底：
  - `guarded` 版本：proxy 低分且近端情绪弱时不兜底，避免模型不可用样本被无差别估值。
  - `mood_fallback` 版本：模型不可用时总是用本地情绪兜底，用于衡量覆盖收益。

新脚本计划：

- `tools/evaluate_local_proxy_strategy.py`

输出内容：

- 三个目标域：`scan_sample`、`author_scored`、`all_actual`。
- 候选策略榜单：命中率、可用率、MAE、Spearman、平均宽度、宽度分布、兜底样本命中。
- 排序诊断：proxy 低/中/高分组实际涨幅、命中率、代表代码。
- 逐样本表：模型来源、兜底来源、proxy 分、rank/tier、动态宽度、命中情况、触发原因。

验收标准：

- 平衡档若在 scan sample 上稳定超过扫描最优，同时平均宽度不明显超过 `±20%`，可进入正式候选参数观察。
- 若提升主要来自观察档 `±25%`，只作为研究上限。
- 若 guarded 兜底显著降低覆盖但减少误判，可作为实盘前人工确认提示，而不是自动估值默认。

### 推进结果

新增脚本：

- `tools/evaluate_local_proxy_strategy.py`

同时修正了 `tools/analyze_xueqiu_author_logic_distillation.py` 中 `current_params` 字段映射问题：旧版 proxy 评估读取的是 `current_params_available/current_params_predicted_change_pct`，但 teacher rows 实际字段为 `current_available/current_predicted_change_pct`。修正后，`current_params` 不再被误判为全样本不可用。

重要口径修正：

- 旧报告中 `current_params_proxy_step0_layered_fallback` 的 13/31，更接近“全样本本地情绪兜底”，不是“当前模型中枢 + 不可用时兜底”。
- 新脚本将滚动本地情绪估计改为使用全量已上市本地样本历史，再切片到目标集合；`scan_sample` 不再只看 3 月以后样本内部历史。

最新输出：

- `outputs/local_proxy_strategy_scan_sample_20260711_180454_136649.md/json`
- `outputs/local_proxy_strategy_author_scored_20260711_180454_203828.md/json`
- `outputs/local_proxy_strategy_all_actual_20260711_180454_227200.md/json`
- 修正后的 distillation：
  - `outputs/xueqiu_author_logic_distillation_scan_sample_20260711_180213_160179.md/json`
  - `outputs/xueqiu_author_logic_distillation_author_scored_20260711_180331_854893.md/json`
  - `outputs/xueqiu_author_logic_distillation_all_actual_20260711_180213_190750.md/json`

严格三件套结果（不改可用模型中枢）：

- `scan_sample`：推荐 `scan_best_proxy_model_balanced_recent_mood`，命中 12/31，命中率 38.7%，平均宽度 16.1%，兜底 3/7。
- `author_scored`：同上，12/31，命中率 38.7%，平均宽度 16.1%，兜底 3/7。
- `all_actual`：同类策略命中 14/39，命中率 35.9%，平均宽度 16.7%，兜底 3/8。
- 对照：当前参数 3/31、4/39；扫描最优 8/31、9/39。

研究上限结果（允许全样本滚动情绪中枢混合）：

- `scan_sample`：`scan_best_proxy_all_rolling75_layered_v1_recent_mood`，14/31，平均宽度 19.2%。
- `all_actual`：`scan_best_proxy_all_rolling50_layered_v1_recent_mood`，18/39，平均宽度 19.4%。

阶段判断：

- “proxy 排序 + 动态宽度 + 本地情绪兜底”在不改模型中枢时有稳定增量，但提升幅度低于作者融合，也低于全量情绪中枢混合研究项。
- 纯相对排名三分位宽度不如绝对 proxy 分/模型不确定性触发宽度；proxy 排序更适合做解释、分层和人工关注优先级。
- 下一步若要进入正式候选，应优先考虑 `scan_best + balanced dynamic width + recent_mood fallback` 作为保守离线观察项；`all_rolling50/75` 只能作为研究上限继续跟踪。

## 实施阶段

### 阶段 1：语料采集

- 完成 `tools/xueqiu_corpus_collect.cjs`。
- 采集四个作者关键词文章。
- 生成 `data/xueqiu_corpus/` 语料文件。
- 输出运行报告和质量统计。

### 阶段 2：初步字段抽取

- 增加正则抽取发行价、市值区间、价格区间、PE、可比公司。
- 增加股票代码和上市日期线索抽取。
- 生成文章级字段覆盖率报告。

### 阶段 3：规则库与 skill 骨架

- 创建 repo-local skill。
- 写 `SKILL.md` 核心工作流。
- 写 `references/heuristic_taxonomy.md` 和 `local_field_mapping.md`。
- 把语料 schema 和采集命令纳入 skill 资源说明。

### 阶段 4：本地回测

- 将文章映射到本地 replay 样本。
- 实现作者规则打分和基线修正。
- 输出与当前综合估值模型的对比报告。

### 阶段 5：迭代

- 根据回测结果保留有效规则，剔除事后叙事。
- 补充人工标注字段。
- 视情况扩大作者、关键词和历史页数。

## 风险与处理

- 雪球反爬变化：保留采集失败报告，允许从手工导出的 HTML/正文补录。
- 作者文章混入上市后评论：正文和评论分层保存，回测只使用正文发布时间前可知信息。
- 文章标题不含关键词但内容相关：第一版用关键词召回，后续增加代码/上市日期窗口召回。
- 本地字段不完整：规则先标记为 `unmapped`，不强行纳入回测。
- 语料版权和引用：本地只用于研究与抽象规则，报告中避免长篇原文复刻。
