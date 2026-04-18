# 方法二数据源边界与纯 Tushare 目标清单

更新日期：2026-04-18  
适用范围：北交所新股上市首日估值框架中的方法二（行业 + 近期新股动量）

## 1. 这份清单要解决什么问题

截至 2026-04-18，方法二已经不是“能不能用”的问题，而是“哪些字段必须继续坚持 Tushare，哪些字段允许长期保留为展示补充”的边界问题。

如果这条边界不先固定，后续很容易在两种目标之间反复摇摆：

- 一味追求“更纯的 Tushare”
- 为了稳定交付保留少量展示型补充字段

因此，本清单的目标是把“纯 Tushare”的判断标准和剩余东方财富补充字段正式定下来。

## 2. 当前可以视为 Tushare 主链路的字段

下列字段已经进入方法二的核心主链路，原则上继续保持 `Tushare` 优先，不再回到“默认依赖东方财富”的状态：

- `APPLY_DATE`
- `LISTING_DATE`
- `TOTAL_ISSUE_NUM`
- `ISSUE_PRICE`
- `AFTER_ISSUE_PE`
- `INDUSTRY_PE_NEW`
- `SW_INDUSTRY`
- `TOP_APPLY_MARKETCAP`
- `ONLINE_ISSUE_LWR`
- `recent_ipos`（近期新股样本）

其中当前已确认的稳定替代口径为：

- `INDUSTRY_PE_NEW`
  - `stock_basic.industry -> SW2021 行业指数 -> sw_daily.pe`
- `SW_INDUSTRY`
  - 申万行业指数名称补位
- `TOP_APPLY_MARKETCAP`
  - `new_share.limit_amount × ISSUE_PRICE`
- `ONLINE_ISSUE_LWR`
  - `new_share.ballot`

结论：

- 只要上述核心字段仍稳定由 `Tushare` 提供，就可以认为方法二的估值主链路已经成立
- 这部分字段不应再被归类为“长期展示补充”

## 3. 允许长期保留为展示补充的字段

下列字段当前可以明确归入“允许长期保留的展示补充字段”：

- `PRICE_WAY`
- `ONLINE_VA_NUM`
- `MAIN_BUSINESS`

这样划分的原因如下：

- `PRICE_WAY`
  - 只影响基础信息展示，不参与估值公式
- `ONLINE_VA_NUM`
  - 当前仅用于市场热度展示，不参与估值计算
- `MAIN_BUSINESS`
  - 主要用于报告描述层，且已有 PDF 路径可补充

结论：

- 即使这些字段继续由东方财富或 PDF 补充，也不影响方法二的核心估值口径
- 后续是否继续替换，优先级应低于估值主链路稳定性

## 4. “纯 Tushare” 的判定标准

从方法二视角，建议把“纯 Tushare”分成两层判断：

### 4.1 核心口径纯 Tushare

满足以下条件时，可认定“方法二核心口径已纯 Tushare”：

- 目标股核心 IPO 字段来自 `Tushare`
- `INDUSTRY_PE_NEW` 来自 `Tushare sw_daily`
- `TOP_APPLY_MARKETCAP` 来自 `Tushare new_share`
- `ONLINE_ISSUE_LWR` 来自 `Tushare new_share.ballot`
- 近期样本来自 `Tushare`
- 未发生“目标股回退”或“近期样本回退”

这是当前项目最重要、也最应该优先维护的“纯 Tushare”定义。

### 4.2 报告展示纯 Tushare

只有在以下条件同时满足时，才可进一步认定“报告展示层也已纯 Tushare”：

- `supplemented_fields` 为空
- 未触发东方财富展示补充
- 未触发目标股或近期样本回退

这是一种更严格的定义，但不应作为当前阶段是否可交付的前置条件。

## 5. 展示字段的统一处理规则

当前统一规则如下：

1. 能由 `Tushare` 替换的字段，优先使用 `Tushare`
2. `Tushare` 不能稳定替换的展示字段，允许继续由东方财富补充
3. 最终仍未取到的字段，展示层统一写为：
   - `未取到数据`

特别说明：

- `ONLINE_VA_NUM` 若东方财富未返回，直接显示“未取到数据”
- 该留空处理不会影响估值主结果

## 6. 当前阶段推荐的项目边界

截至 2026-04-18，建议项目正式采用以下边界：

- 方法二核心估值链路：
  - 以 `Tushare` 为准
- 报告展示尾项：
  - 允许保留少量东方财富 / PDF 补充
- 是否继续收掉展示尾项：
  - 以“是否显著改善用户理解”为标准
  - 不再以“追求绝对纯度”为唯一目标

## 7. 目前真正剩下的技术尾项

`new_share` 区间缓存优化完成后，当前剩余少量重复请求的主要来源已经变成：

- 新进入近期窗口样本的首轮预热
  - `stock_basic`
  - `daily`
  - `daily_basic`
- 新行业指数快照的首次加载
  - `sw_daily:*`

因此，若后续继续优化，优先级应转向：

- 是否为新进入窗口样本做更主动的预热
- 是否为新行业指数快照做更明确的复用策略

而不是继续把精力放在 `new_share` 本身。
