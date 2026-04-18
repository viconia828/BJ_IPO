# quick_method2 候选参数集 v1

基于 [tune_params_quick_method2_20260418_203547.md](C:/Users/ai/Desktop/北交所新股估值/输出/调参/tune_params_quick_method2_20260418_203547.md) 整理。

本版先不直接改正式参数文件，而是把 `quick_method2` 的高分组合收敛成 4 档候选，便于做一轮综合回放复核：

- `quick_method2_best_full`
  - 直接采用验证集最优组合
- `quick_method2_keep_current_pe_drag`
  - 保留当前更强的高 PE 压制
- `quick_method2_keep_current_pe_high`
  - 保留当前 `pe_high_threshold = 0.60`
- `quick_method2_core_only`
  - 只吸收区间宽度、小盘溢价和流通盘阈值三项核心改动

完整结构化文件见：

- [quick_method2_candidate_set_v1.json](C:/Users/ai/Desktop/北交所新股估值/data/offline_tuning/candidate_sets/quick_method2_candidate_set_v1.json)
