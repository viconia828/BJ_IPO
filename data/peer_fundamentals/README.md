# 可比公司基本面资料

四个首批案例：安达股份、宇特光电、凯达重工、远大健科。候选来自现有招股书解析/研究资料，尚未自动判为核心可比。

## 数据职责

- iFind HTTP API：通过本机`IFIND_AUTH_TOKEN`、`IFIND_REFRESH_TOKEN`调用官方`basic_data_service`；当前注册并实测的字段为`ths_revenue_stock`和`ths_np_atoopc_stock`，参数为明确报告期、合并报表100。原始响应保存返回参数，入库时校验报告期和报表范围。未知指标不靠英文名称猜测后直接入库。
- iFind MCP：使用`get_stock_info`、`get_stock_financials`获取主营、产品、扣非利润、现金流、毛利率、披露日期及同期增长。按单个报告期查询并保存原始工具响应。API/MCP同属iFind，通道一致不能算独立来源验证。
- Tushare：沿用项目现有接口、配额和认证方式，以`income`合并报表财务独立复核营收及归母利润；保留返回的不同披露版本。
- 东方财富：继续用于现有行情估值交叉验证，本次未新增其财务接口。最终业务匹配与历史版本确认仍要回到招股书、年报等原始公告。

官方接口依据：[iFind基础数据HTTP手册](https://quantapi.10jqka.com.cn/gwstatic/static/ds_web/quantapi-web/help-center/manual.html)、[Tushare利润表](https://tushare.pro/document/2?doc_id=33)。

## 文件与时间口径

- `api/`：请求与响应不可覆盖快照；只保存业务请求体，凭据保持在内存。刷新创建新文件。
- `mcp/`：原始问题、工具结果及采集日期。以后新增查询使用新的文件名，保留旧证据；`financials.json`多期试查及重复日期试查仅留档，不入库。
- `tushare/`：独立核验原始响应；显式刷新前将旧响应按内容哈希归档。
- `catalog.json`：可重建的当前资料索引，保留字段级原始观测、数值冲突、日期差异及证据路径/hash。
- `review.md`：主营和财务复核摘要，不是投资建议或已完成的核心同行名单。

财务期为2023年报、2024年报、2025年中报/年报、2026年中报。金额统一人民币元，毛利率/同比为百分比；MCP单元格的万/亿后缀优先于表头，按显示精度容许四舍五入差异。保留归母与扣非归母两种盈利，不混用。

报告期、实际披露日、预计披露日、采集日分别处理：预计日期不充当实际日期；披露日期没有明确对应报告期则不绑定；当前主营没有历史可得日期。跨源日期冲突保留全部值，当前研究采用较晚可得日期，不据此宣称已解决历史修订版本问题。所有当前采集的历史财务标记`historical_pit_eligible=false`。

TTM需要最近年报+本期累计-上年同期全部齐全，且已披露、已采集、无相关数值冲突。如果计算同比与供应商当期披露同比差异超过0.10个百分点，暂停拼接并标记同期基数/范围/版本待核验。通过该检查仍不等于已完成财务质量和周期复核。

## 运行

在项目根目录执行：

```powershell
python tools/collect_peer_fundamentals.py --crosscheck-tushare --as-of 2026-10-13
python tools/collect_peer_fundamentals.py --offline --as-of 2026-10-13
python tests/validate_peer_fundamentals.py
```

`--as-of`为排他日期；例子表示10月12日已知资料。`--refresh`显式重新抓取API与Tushare；没有后台轮询。MCP由已连接的助手查询并保存，普通Python估值流程只读资料缓存，不假装可以自行调用桌面MCP。当前采集脚本服务上述四个案例，通用API适配器支持传入其他代码及报告期。

主流程通过`comparable_data_helper`附加`fundamental_evidence`及完整`fundamental_candidates`，没有行情PE的新三板候选仍保留资料。报告数据源显示iFind资料覆盖数及“复核参考，未用于定价”。没有把未经主营/盈利/周期复核的数据自动代入F，估值参数保持不变。
