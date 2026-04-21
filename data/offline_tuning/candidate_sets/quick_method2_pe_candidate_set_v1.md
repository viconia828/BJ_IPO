# quick_method2 PE 候选参数集 v1

基于 [tune_params_quick_method2_pe_focus_20260421_102535.md](C:/Users/ai/Desktop/北交所新股估值/输出/调参/tune_params_quick_method2_pe_focus_20260421_102535.md) 与 replay 口径的 PE 观察结果整理。

这版不再重新打开整组 `quick_method2` 大范围调参，而是只围绕当前仍有剩余空间的 PE 参数组收敛出 3 档候选，便于做一轮候选复核并决定是否吸收：

- `quick_method2_pe_low_0p20_keep_current`
  - `pe_low_threshold = 0.20`
  - `pe_discount_boost = 0.10`
  - `pe_high_threshold = 0.60`
  - `pe_premium_drag = -0.10`
  - 最小改动方案：优先验证“只减少不必要的低估加成”
- `quick_method2_pe_low_0p20_mild_boost`
  - `pe_low_threshold = 0.20`
  - `pe_discount_boost = 0.05`
  - `pe_high_threshold = 0.60`
  - `pe_premium_drag = -0.10`
  - 更保守的低估加成版本
- `quick_method2_pe_low_0p25_keep_current`
  - `pe_low_threshold = 0.25`
  - `pe_discount_boost = 0.10`
  - `pe_high_threshold = 0.60`
  - `pe_premium_drag = -0.10`
  - 作为比 `0.20` 更温和的中间档

完整结构化文件见：

- [quick_method2_pe_candidate_set_v1.json](C:/Users/ai/Desktop/北交所新股估值/data/offline_tuning/candidate_sets/quick_method2_pe_candidate_set_v1.json)
