# trend_balance 候选参数集 v1

基于 [tune_params_trend_balance_20260420_165614.md](C:/Users/ai/Desktop/北交所新股估值/输出/调参/tune_params_trend_balance_20260420_165614.md) 整理。
这版先不直接改正式参数文件，而是把首轮 `trend_balance` 搜索里验证集最强的 3 档结构收敛成候选，便于做一轮候选复核，再决定是否进入观察期。

- `trend_balance_industry_0p7_half10_thr70_40`
  - `industry_trend_weight = 0.70`
  - `market_sentiment_weight = 0.30`
  - `sample_decay_half_life_days = 10`
  - `trend_strong_threshold = 70`
  - `trend_weak_threshold = 40`
  - 当前首轮验证集最优
- `trend_balance_industry_0p7_half10_thr75_45`
  - `industry_trend_weight = 0.70`
  - `market_sentiment_weight = 0.30`
  - `sample_decay_half_life_days = 10`
  - `trend_strong_threshold = 75`
  - `trend_weak_threshold = 45`
  - 保持主导结构不变，只把阈值整体上移一档
- `trend_balance_industry_0p6_half10_thr70_40`
  - `industry_trend_weight = 0.60`
  - `market_sentiment_weight = 0.40`
  - `sample_decay_half_life_days = 10`
  - `trend_strong_threshold = 70`
  - `trend_weak_threshold = 40`
  - 作为更保守的回退备选

完整结构化文件见：
- [trend_balance_candidate_set_v1.json](C:/Users/ai/Desktop/北交所新股估值/data/offline_tuning/candidate_sets/trend_balance_candidate_set_v1.json)
