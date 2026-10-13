"""TXT settings must reach pricing, diagnostics, experiments and console guards."""
from copy import deepcopy
from datetime import date, timedelta
import math
from pathlib import Path
import re
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'code'), str(ROOT/'tools'), str(ROOT/'tests')]
import config_loader
import quality_sentiment as qs
import sentiment_pricing as pricing
import sentiment_roles as roles
import sentiment_settings as settings
import sentiment_timing as timing
import sentiment_tuning as tuning
from tune_params import _write_param_updates
from validate_quality_sentiment import issuer, PARAMS
from validate_sentiment_roles import signals


class SentimentSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_root = ROOT/'tests/_tmp/sentiment_settings'
        cls.temp_root.mkdir(parents=True, exist_ok=True)

    def setUp(self):
        self.history = [issuer(f'910{i:03}', date(2026,1,1)+timedelta(days=i*3)) for i in range(20)]
        self.target = issuer('920999', date(2026,3,10))
        for row in self.history:
            for prefix, days, ratio in [('NEXT',1,.8), ('THIRD',2,.6)]:
                row[prefix+'_TRADE_DATE'] = str(qs.day(row['LISTING_DATE'])+timedelta(days=days))
                row[prefix+'_DAY_CLOSE'] = row['AVERAGE_PRICE']*ratio

    def params(self, **updates):
        with tempfile.TemporaryDirectory(dir=self.temp_root) as folder:
            path = Path(folder)/'params.txt'
            path.write_bytes((ROOT/'策略参数.txt').read_bytes())
            _write_param_updates(path, updates)
            return config_loader.load_params(path)

    def test_all_settings_are_grouped_once_with_chinese_comments(self):
        text = (ROOT/'策略参数.txt').read_text('utf-8').split('[industry_mapping]')[0]
        loaded = config_loader.load_params()
        for key, default in settings.DEFAULTS.items():
            matches = re.findall(rf'(?m)^{key}\s*=.*$', text)
            self.assertEqual(len(matches), 1, key)
            self.assertRegex(matches[0].split('#',1)[1], r'[\u4e00-\u9fff]', key)
            self.assertEqual(loaded[key], default, key)

    def test_old_parameter_files_keep_original_defaults(self):
        text = (ROOT/'策略参数.txt').read_text('utf-8')
        for key in settings.DEFAULTS:
            text = re.sub(rf'(?m)^{key}\s*=.*\n?', '', text)
        with tempfile.TemporaryDirectory(dir=self.temp_root) as folder:
            path = Path(folder)/'old.txt'
            path.write_text(text, encoding='utf-8')
            loaded = config_loader.load_params(path)
        self.assertEqual(settings.resolve(loaded), settings.DEFAULTS)

    def test_invalid_numbers_and_window_relationships_fail_at_load(self):
        bad = [
            {'sentiment_response_window_groups': 2.5},
            {'sentiment_response_min_groups': 4},
            {'sentiment_response_min_reference_groups': 21},
            {'sentiment_response_min_coverage': 1.1},
            {'sentiment_response_clip_multiplier': .9},
            {'sentiment_response_prior_groups': -1},
            {'quality_sentiment_min_history': 41},
            {'quality_sentiment_state_min_groups': 4},
            {'quality_sentiment_state_change_pct': 100},
            {'quality_sentiment_half_life_days': .5},
            {'quality_sentiment_ridge_penalty': 'nan'},
            {'sentiment_timing_material_pct': 'inf'},
            {'sentiment_tuning_half_life_candidates': '4,,8'},
            {'sentiment_tuning_half_life_candidates': '0,4'},
            {'sentiment_role_confirmation_cap_ratio': 1.1},
        ]
        for update in bad:
            with self.subTest(update=update), self.assertRaises(ValueError):
                self.params(**update)

    def test_response_window_reference_shrink_clip_and_cap_are_read_from_txt(self):
        params = self.params(sentiment_response_window_groups=4, sentiment_response_reference_groups=10,
            sentiment_response_prior_groups=7, sentiment_response_clip_multiplier=1.1,
            sentiment_response_max_multiplier=1.03)
        history = deepcopy(self.history)
        for row in history[-4:]:
            row['AVERAGE_PRICE'] = 120
        result = pricing.evaluate_all(self.target, history, params, as_of=self.target['LISTING_DATE'])
        response = result['expectation']['pricing_response']
        signal = response['signals']
        self.assertEqual(len(signal['recent_listing_dates']), 4)
        self.assertEqual(signal['post_reference']['NEXT']['group_count'], 10)
        self.assertAlmostEqual(signal['expectation']['shrinkage'], 4/11)
        self.assertTrue(all(abs(r['value']) <= math.log(1.1) for r in signal['expectation']['observations']))
        self.assertAlmostEqual(result['expectation']['sentiment']['center']/result['baseline']['sentiment']['center'], 1.03)
        self.assertEqual(result['expectation']['anchor'], result['baseline']['anchor'])

    def test_minimum_group_coverage_and_reference_thresholds_are_effective(self):
        observations = [{'listing_date': d, 'value': .2} for d in ['a','b']]
        self.assertTrue(pricing.grouped_signal(observations, ['a','b','c'])['available'])
        for update in [{'sentiment_response_min_coverage': .8}, {'sentiment_response_min_groups': 3}]:
            result = pricing.grouped_signal(observations, ['a','b','c'], params=self.params(**update))
            self.assertFalse(result['available'])
        params = self.params(sentiment_response_min_reference_groups=20)
        result = pricing.feedback_signals(self.target, self.history, params, as_of=self.target['LISTING_DATE'])
        self.assertFalse(result['aftermarket']['available'])
        self.assertIn('20', result['post_reference']['NEXT']['reason'])

    def test_multiplier_one_disables_feedback_for_every_variant(self):
        history = deepcopy(self.history)
        for row in history[-3:]: row['AVERAGE_PRICE'] *= 2
        params = self.params(sentiment_response_max_multiplier=1)
        results = timing.evaluate_all(self.target, history, params, as_of=self.target['LISTING_DATE'])
        for model in results.values():
            self.assertEqual(model['sentiment']['center'], results['baseline']['sentiment']['center'])

    def test_base_window_minimum_industry_weight_and_ridge_are_effective(self):
        params = self.params(quality_sentiment_history_window=10, quality_sentiment_ridge_penalty=7)
        result = qs.evaluate(self.target, self.history, params, as_of=self.target['LISTING_DATE'])
        self.assertEqual(result['sentiment']['sample_count'], 10)
        self.assertEqual(result['sentiment']['structural_model']['ridge_penalty'], 7)
        unavailable = qs.evaluate(self.target, self.history, self.params(quality_sentiment_min_history=21), as_of=self.target['LISTING_DATE'])
        self.assertFalse(unavailable['available'])
        row = {**self.history[-1], 'anchor': {'confidence': 1}}
        base = qs._weight(row, self.target, date(2026,3,10), PARAMS, time_weight=False)
        adjusted = qs._weight(row, self.target, date(2026,3,10), self.params(quality_sentiment_same_industry_weight=3), time_weight=False)
        self.assertEqual(adjusted, 2*base)

    def test_state_window_and_stale_days_are_effective(self):
        params = self.params(quality_sentiment_state_window_groups=2, quality_sentiment_state_stale_days=1)
        result = qs.evaluate(self.target, self.history, params, as_of=self.target['LISTING_DATE'])
        self.assertEqual(len(result['recent_details']), 4)
        self.assertEqual(result['state']['code'], 'stale')
        self.assertEqual(len(result['state']['recent_codes']), 2)

    def test_research_confirmation_and_turn_thresholds_use_txt(self):
        signal = signals([.2,.2,.2], aftermarket=.3)
        params = self.params(sentiment_role_confirmation_cap_ratio=0, sentiment_timing_material_pct=50)
        self.assertEqual(roles.response(signal, params)['aftermarket_log_correction'], 0)
        self.assertFalse(timing.response(signal, params)['timing']['fast_mode'])
        fast = timing.response(signal, self.params(sentiment_response_prior_groups=0))
        self.assertTrue(fast['timing']['fast_mode'])
        self.assertAlmostEqual(fast['expectation_log_correction'], .2)

    def test_console_search_space_and_guard_thresholds_use_txt(self):
        params = self.params(sentiment_tuning_half_life_candidates='6,10', sentiment_tuning_min_validation_samples=20)
        result = tuning.tune(self.history, params, train_ratio=.5, min_train_samples=1)
        self.assertEqual(result['search_space'], [6,8,10])
        self.assertIn('validation_coverage', result['guard']['failed'])
        model = {'scored': True, 'price_error_pct': 0, 'gain_error_pp': 0, 'hit': True}
        rows = [{'split': 'train' if i<8 else 'validation', 'recent': False, 'cooling': True,
                 'models': {'current': model, 'candidate': {**model, 'price_error_pct': -.5}}} for i in range(13)]
        self.assertTrue(tuning.acceptance_guard(rows)['checks']['earlier_under'])
        self.assertFalse(tuning.acceptance_guard(rows, params=self.params(sentiment_tuning_earlier_under_tolerance_pp=0))['checks']['earlier_under'])


if __name__ == '__main__': unittest.main()
