# composite_weight 候选参数集 v1

基于 [tune_params_composite_weights_20260420_144915.md](C:/Users/ai/Desktop/北交所新股估值/输出/调参/tune_params_composite_weights_20260420_144915.md) 整理。

本版先不直接改正式参数文件，而是把首轮综合回放里表现更好的综合权重收敛成 3 档候选，便于做一轮候选复核，并形成“是否进入观察期”的结论：

- `composite_weight_best_method2_lean`
  - `weight_comparable = 0.20`
  - `weight_industry_momentum = 0.80`
  - 当前首轮验证集最优组合
- `composite_weight_second_best`
  - `weight_comparable = 0.30`
  - `weight_industry_momentum = 0.70`
  - 偏向方法二，但比最优组合更保守
- `composite_weight_conservative`
  - `weight_comparable = 0.40`
  - `weight_industry_momentum = 0.60`
  - 只做一档轻量调整，作为观察期保守备选

完整结构化文件见：

- [composite_weight_candidate_set_v1.json](C:/Users/ai/Desktop/北交所新股估值/data/offline_tuning/candidate_sets/composite_weight_candidate_set_v1.json)
