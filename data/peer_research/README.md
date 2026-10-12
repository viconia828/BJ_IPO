# 批量同业与发行人底稿

入口：[研究总览](review.md)、[规则](rules.json)、[异常与覆盖清单](checks.json)。每家公司有财务底稿，每只新股有可比匹配卡；机器可读JSON与中文Markdown同时保存。

本轮范围为66只发行人、225家不同同业候选，去重290家公司；2019–2025年报及2025/2026年中报，共9期。应收、存货、总资产、归母净资产补充2025中报/年报和2026中报。新三板保持NQ后缀，没有伪装成北交所股票。

## 来源与重建

- `universe.json`：本轮范围及原65只回放数据的SHA；远大健科另从已核验招股书候选补入。
- `api/`：iFind官方HTTP接口营收、归母精确金额及请求回显，按25家公司批量采集。成功请求复用缓存，刷新另建不可覆盖快照。
- `mcp_jobs.json`、`balance_jobs.json`：156项MCP计划；`mcp/`保存实际查询、工具返回及时间。MCP与API同属iFind，不算两个独立供应商。
- `tushare/`：265家非NQ公司合并利润表，保留同报告期的各披露版本，营收/归母与iFind独立逐字段核验。
- `source_excerpts/`、`precision_reviews.json`：原始招股书页面、SHA、页码与字段精度。六个已目视确认的中报字段可按万元两位小数处理四舍五入；其他差异不靠整数尾零猜精度。原PDF缺失或SHA变化时该精度复核失效。
- `management_guidance.json`：安达、宇特、远大的四个管理层业绩预计场景，分开保存期间、上下限、来源与非审计属性；公告发布时间未核验前不进入历史PIT。不同于已实现业绩。
- `notices/`：iFind公告搜索返回的检索线索，可能含供应商自动摘要，**不作为原始财务页或精度放行证据**。
- `workpapers/companies/`、`workpapers/issuers/`：290份公司底稿、66份新股底稿，JSON保留字段级观测，Markdown展示财务、增长、营运资金、周期线索与可比复核重点。

```powershell
python tools/collect_peer_research.py init
python tools/collect_peer_research.py api
python tools/collect_peer_research.py tushare
python tools/build_peer_workpapers.py --as-of 2026-10-14
python tests/validate_peer_research.py
```

`init`更新范围/任务计划，不启动MCP；桌面助手通过已配置的MCP按任务调用并留档，普通CLI不伪装成能调用桌面MCP。恢复时先检查已保存job_id，成功证据不覆盖；重新查询应保存带新时间戳的文件并保留同一job_id。HTTP/Tushare采集复用缓存，显式`--refresh`才重新查询，旧原始响应保留。`--as-of`是排他日期，上例表示截至10月13日采集的资料；本轮跨午夜采集，原始采集时刻分别保留。

证据目录通过`.gitattributes`保留原始字节，避免Git换行转换破坏SHA。`universe.json`对已有回放JSON的引用哈希单独注明采用UTF-8、LF换行规范化口径，其余原始证据均按文件字节计算。

## 检验与使用边界

规则版本`peer_research_rules_v1`，15项规则覆盖单位、期间、报表范围、披露/采集日、金额及版本差异、同期基数、低基数、非经常损益、现金流、营运资金、五年波动、业务匹配及EPS/PE分母。20个百分点、25%、0.8、1%等为固定复核筛查阈值，没有用IPO收益拟合，也不直接乘成估值折扣。

`pass`仅表示该项检查通过；`review`需要核验；`unknown`为资料不足；`blocked`阻断对应计算或方法。C03正常化、T01历史PIT是方法门槛，不能将其重复计数解释为每家公司财务出了问题。缺少原始版本、业务收入结构、恢复期、同口径同行倍数时不会自动批准核心池或正常化估值。

余额指标单元格标记`instant`，不做TTM相加减；利润、收入与现金流用累计口径。负基数不计算通常的同比/CAGR；TTM须齐备年度和上年同期、无相关冲突。当期披露同比与历史基数不一致会暂停拼接。五年利润率中位数×当前TTM营收只展示算术情景，未核验业务范围及周期，不用于定价。

跨源披露日期差异保留。iFind无日期而Tushare有多版本公告日期时，当前研究采用较晚版本日并注明来源；这不是原始历史PIT认证。主估值仅只读附加缓存及检验结果，不改价格、PE或正式参数；新情绪模型继续保持影子模式。

接口依据：[iFind官方数据接口](https://quantapi.10jqka.com.cn/gwstatic/static/ds_web/quantapi-web/help-center/manual.html)、[Tushare合并利润表](https://tushare.pro/document/2?doc_id=33)。认证只从本机环境读取，不入证据文件。
