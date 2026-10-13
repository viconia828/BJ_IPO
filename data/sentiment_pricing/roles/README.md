# 信号分工与转向速度：两步研究

按用户要求先做信号分工，再做响应速度；暂不加入大盘或行业行情。基本面、宽可比池、旧情绪模型及65条历史输入保持不变。

- [第一步报告](roles_review.md)：兑现决定方向，承接只在同向时加强，最多补充与兑现相同强度；冲突时提示风险，不反转中枢。
- [第二步报告](timing_review.md)：在第一步之上，最近两个实际上市日组的兑现偏差均同向达到阈值时，采用两组，否则使用原三组；上涨和下跌对称。
- `roles_protocol.json`与`timing_protocol.json`分别在对应阶段首次计算前冻结。第二步同时保存第一步结果哈希。
- 两份`*_replay.json`保留每条预测、反馈、不可用原因、相同样本指标及保护条件；两份`*_review.md`为可读报告。
- `validation.json`与`audit.json`记录38个验证脚本、新增13项规则以及冻结资料和时间边界核验。

最近9只有效样本的价格MAPE：原组合25.13%，第一步23.24%，第二步23.25%；全部48只则为39.11%、39.51%、39.61%。分工改善了宇特等近期案例，但华汇、凯达退步；两组响应没有稳定增加收益。不得仅根据近期命中数提高就宣布整体胜出。

两步均未通过全部预定保护条件，因此默认影子仍为原组合，正式价格不变。主流程和离线预览增加两个独立研究候选区间，并说明冲突/取样速度；候选异常不阻断原价格。现有真实前向档案未被覆盖。

```powershell
python tools/evaluate_sentiment_roles.py --stage roles
python tools/evaluate_sentiment_timing.py
python tests/validate_sentiment_roles.py
python tools/audit_sentiment_roles.py
```

`prepare_sentiment_roles_protocol.py --stage roles|timing`仅用于首次建立对应协议，存在时不会覆盖。所有样本已在前序研究中看过，以上都是历史诊断，不构成独立留出或真实前向胜出。
