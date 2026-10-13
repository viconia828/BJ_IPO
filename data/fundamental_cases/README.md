# 基本面复核与近期新股影子回放

当前11份逐股复核包括最近10只已上市新股及远大健科。使用宽可比池：直接业务参照权重1，相邻产品/工艺0.5，同渠道品牌0.25；不按PE高低或首日涨幅筛选。权重为固定研究设定，尚未获得样本外验证。

- [最新回放](recent_review.md)：固定最近10只，有效覆盖9只，旧新共同6只；高估、低估、区间命中及降温预警分别列示。
- [凯达盈利与周期底稿](kada_earnings_review.md)：FY/H1/TTM、审核预测兑现、双方历史利润率压力参照及共同同行检查。
- [上一阶段报告](review.md)：提交e09ee41的四例消融历史结果，不代表当前11份底稿的最新结果。
- `review_decisions.json`：有原始页依据的业务判断、股本、盈利修正和周期/预测结论。
- `cases/`：结构化计算输入与逐股可读底稿。旧批量底稿和原始财报值不覆盖。
- `inputs/`：估值时点报价及历史评价输入；recent_history冻结65只历史输入及来源哈希，供离线复现。实际首日结果不进入基本面决策文件。
- `evidence/`：100页招股书及16页新增同行报告文本、页码和原PDF SHA256；原PDF仍保存在本机公告目录。
- `raw/`：原批次及stage3补采，含Tushare与iFind API/MCP；失败如实保存，API/MCP不视为两家独立供应商。
- `index.json`：主流程只读接入入口，读取时校验公司代码、路径和结构化底稿SHA256。
- `audit.json`：原PDF及依赖证据核验、情绪核心算法AST不变证明。
- `validation.json`：工程验证结果；基本面18项专项规则及新增周期/回放7项规则。

## 计算与时间规则

归母与扣非分别计算2025全年＋2026上半年－2025上半年，除以发行后总股本；同行倍数重新用估值日市值除以相同利润，不能沿用口径不同的现成PE。绿鞋全额仅作摊薄情景。SH/SZ参照沿用0.55市场折价，BJ参照不重复折价；不同市场明细分别保留，NQ缺少可靠流动性/报价口径时仍保留业务身份。

主研究F采用扣非口径，同时显示归母口径。凯达8家宽池中当前扣非PE有效2家；三年历史利润率压力参照保留亏损年份、双方同时处理，但与当前口径共同有效同行只有1家，不批准为可持续正常化F。年度审核预测缺同年度同行预测倍数时，不与TTM PE混乘。

当前底稿最早可用于2026-10-13采集完成后的影子试算。日期采用排他截点，例如2026-10-14表示10月13日收盘后。历史回放必须显式标记研究重建，不能回填为事前档案或启用正式定价。默认历史流程继续使用当时可得代理；单独消融工具才将本轮研究F用于历史重建。

F情景区间来自同行离散、盈利口径和股本情景，不是概率区间。回放统一使用固定±10%的首日价格区间，不能靠拓宽F区间提高命中率。

## 复现

```powershell
python tools/build_fundamental_cases.py
python tools/build_kada_earnings_review.py
python tools/evaluate_recent_fundamental_cases.py
python tools/audit_fundamental_cases.py
python tests/validate_fundamental_anchor.py
python tests/validate_recent_fundamentals.py
```

离线回放读取已归档输入，上一阶段对照从固定Git提交e09ee41读取。历史快照首次由`freeze_recent_fundamental_history.py`创建，存在时拒绝覆盖；冻结只为复现，仍不声称原始PIT。首次补采工具`collect_recent_fundamental_supplements.py`使用本机已有认证配置，凭据不入档；`prepare_fundamental_case_evidence.py`按review_decisions中的页码只读提取本机招股书，不覆盖已有摘录或修改原PDF。

覆盖提升不等于预测胜出。本轮共同6只高估部分均值10.48%→15.36%，涨幅MAE123.10→145.19个百分点；保持shadow。下一步应独立检验降温如何收缩情绪溢价，并保留低估及反弹反例，不通过继续压低F强行改善已知样本。
