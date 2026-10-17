"""Default-console routing, frozen anchors, selection and writeback boundaries."""
from copy import deepcopy
from datetime import date, timedelta
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'code'), str(ROOT/'tools'), str(ROOT/'tests')]
import config_loader
import manual_tune_prompt as menu
import sentiment_tuning as tuning
import sentiment_tune_console as console
import tune_params
from validate_quality_sentiment import issuer, PARAMS


class SentimentConsoleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_root = ROOT/'tests/_tmp/sentiment_tuning'
        cls.temp_root.mkdir(parents=True, exist_ok=True)

    def setUp(self):
        self.params = {**PARAMS, tuning.PARAMETER: 8}
        self.history = [issuer(f'910{i:03}', date(2026, 1, 1)+timedelta(days=i*3)) for i in range(25)]

    def test_default_menu_routes_formal_and_shadow_is_explicit(self):
        for choice in ('', '5'):
            with self.subTest(choice=choice), patch('builtins.input', return_value=choice):
                command, summary = menu._build_command()
            self.assertEqual(command[command.index('--mode')+1], 'auto')
            self.assertEqual(summary['auto_kind'], 'valuation')
            self.assertEqual(summary['mode'], '综合估值调参（正式）')
        with patch('builtins.input', return_value='1'):
            command, summary = menu._build_command()
        self.assertEqual(command[command.index('--mode')+1], 'sentiment-auto')
        self.assertEqual(summary['auto_kind'], 'sentiment')
        self.assertEqual(summary['mode'], '情绪组合影子调参')

    def test_frozen_inputs_current_tie_and_missing_outcomes(self):
        history = deepcopy(self.history)
        history[-1]['AVERAGE_PRICE'] = None
        before = deepcopy(history)
        result = tuning.tune(history, self.params, train_ratio=.6)
        self.assertEqual(result['rule'], 'combined')
        self.assertEqual(result['selected_half_life'], 8)
        self.assertEqual(result['changed_overrides'], {})
        self.assertEqual(history, before)
        self.assertEqual(self.params, {**PARAMS, tuning.PARAMETER: 8})
        self.assertFalse(result['rows'][-1]['models']['candidate']['scored'])
        for row in result['rows']:
            self.assertEqual(row['fundamental']['center'], 10)
        with self.assertRaises(ValueError):
            tuning.tune(history, {**self.params, 'quality_sentiment_mode': 'active'})

    def test_training_selection_cannot_rerank_on_validation(self):
        def run(history, params):
            half_life = params[tuning.PARAMETER]
            results = {}
            for i, target in enumerate(history):
                center = (30 if half_life == 4 else 33) if i < 15 else (60 if half_life == 4 else 30)
                result = {'anchor': {'center': 10}, 'state': {'code': 'cooling_watch'},
                          'sentiment': {'center': center, 'low': center*.9, 'high': center*1.1},
                          'pricing_response': {'rule': 'combined'}}
                results[target['SECURITY_CODE']] = (result, result)
            return results
        with patch.object(tuning, '_run', side_effect=run):
            result = tuning.tune(self.history, self.params, train_ratio=.6)
        self.assertEqual(result['selected_half_life'], 4)
        self.assertIn('validation_mape', result['guard']['failed'])
        self.assertEqual(result['changed_overrides'], {})

    def test_same_listing_date_never_straddles_split(self):
        history = deepcopy(self.history)
        history[15]['LISTING_DATE'] = history[14]['LISTING_DATE']
        train, validation, _ = tuning.split_codes(history, .6, 8)
        dates = lambda codes: {r['LISTING_DATE'] for r in history if r['SECURITY_CODE'] in codes}
        self.assertFalse(dates(train) & dates(validation))

    def test_future_and_duplicate_rows_and_invalid_case_are_not_silently_scored(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root) as folder:
            root = Path(folder)
            rows, audit = tuning.prepare_history({'items': self.history}, root=root, cutoff='2026-02-01')
            self.assertTrue(audit['excluded'])
            self.assertTrue(all(r['LISTING_DATE'] < '2026-02-01' for r in rows))
            with self.assertRaises(ValueError):
                tuning.prepare_history({'items': self.history + [self.history[0]]}, root=root, cutoff='2026-04-01')
            broken = {**self.history[0], 'fundamental_case': {'integrity_error': 'hash mismatch'}}
            with self.assertRaises(ValueError):
                tuning.prepare_history({'items': [broken]}, root=root, cutoff='2026-04-01')

    def test_writeback_is_one_key_shadow_only_and_stale_file_rejected(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root) as folder:
            path = Path(folder)/'params.txt'
            path.write_bytes((ROOT/'策略参数.txt').read_bytes())
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            result = {'guard': {'passed': True}, 'changed_overrides': {tuning.PARAMETER: 4},
                      'selected_half_life': 4, 'search_space': [4, 8, 12, 16]}
            invalid = {**result, 'changed_overrides': {'bse_discount_factor': .9}}
            with self.assertRaises(ValueError):
                console.accept(invalid, path, sha, tune_params._write_param_updates)
            console.accept(result, path, sha, tune_params._write_param_updates)
            accepted = config_loader.load_params(path)
            self.assertEqual(accepted[tuning.PARAMETER], 4)
            self.assertEqual(accepted['quality_sentiment_mode'], 'shadow')
            with self.assertRaises(ValueError):
                console.accept(result, path, sha, tune_params._write_param_updates)

    def test_application_placeholder_is_excluded_before_reviewed_issue_comparison(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root) as folder:
            row = {**self.history[0], 'listing_date_source': 'apply_date_fallback',
                   'fundamental_case': {'code': self.history[0]['SECURITY_CODE']}}
            # No saved peer input is needed for an IPO whose listing date is unknown.
            rows, audit = tuning.prepare_history({'items': [row]}, root=Path(folder), cutoff='2026-04-01')
            self.assertEqual(rows, [])
            self.assertEqual(audit['excluded'][0]['code'], row['SECURITY_CODE'])
            self.assertIn('申购日占位', audit['excluded'][0]['reason'])

    def test_prelisting_review_can_gain_real_listing_date_but_conflicts_still_fail(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root) as folder:
            root = Path(folder)
            row = {**self.history[0], 'fundamental_case': {'code': self.history[0]['SECURITY_CODE']}}
            path = root / 'data/fundamental_cases/inputs' / f"{row['SECURITY_CODE']}.json"
            path.parent.mkdir(parents=True)
            saved = {'SECURITY_CODE': row['SECURITY_CODE'], 'ISSUE_PRICE': row['ISSUE_PRICE'],
                     'LISTING_DATE': None, 'comparable_data': [{'code': 'PEER', 'pe_ttm': 15}]}
            path.write_text(json.dumps(saved), encoding='utf-8')
            rows, _ = tuning.prepare_history({'items': [row]}, root=root, cutoff='2026-04-01')
            self.assertEqual(rows[0]['comparable_data'], saved['comparable_data'])
            self.assertTrue(rows[0]['_fundamental_reconstruction'])
            for conflicting in ({'SECURITY_CODE': '999999'}, {'ISSUE_PRICE': 99},
                                {'LISTING_DATE': '2025-12-31'}, {'LISTING_DATE': 'invalid'}):
                with self.subTest(conflicting=conflicting):
                    path.write_text(json.dumps({**saved, **conflicting}), encoding='utf-8')
                    with self.assertRaisesRegex(ValueError, '发行批次不一致'):
                        tuning.prepare_history({'items': [row]}, root=root, cutoff='2026-04-01')

    def test_cli_evaluation_creates_reviewable_reports_without_writing(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root) as folder:
            folder = Path(folder)
            dataset, params = folder/'dataset.json', folder/'params.txt'
            dataset.write_text(json.dumps({'schema': 'offline_tuning_replay_v1', 'items': self.history,
                                          'available_count': len(self.history), 'evaluation_scope': 'composite'}), encoding='utf-8')
            params.write_bytes((ROOT/'策略参数.txt').read_bytes())
            before = params.read_bytes()
            completed = subprocess.run([sys.executable, '-X', 'utf8', str(ROOT/'tools/tune_params.py'),
                '--mode', 'sentiment-auto', '--dataset-path', str(dataset), '--params-file', str(params),
                '--no-auto-refresh-dataset', '--sentiment-evaluate-only', '--output-dir', str(folder/'reports'),
                '--train-ratio', '.6'], capture_output=True, encoding='utf-8', cwd=ROOT, timeout=60)
            self.assertEqual(completed.returncode, 0, completed.stdout+completed.stderr)
            self.assertIn('情绪影子自动调参完成', completed.stdout)
            self.assertEqual(params.read_bytes(), before)
            md = next((folder/'reports').glob('*.md'))
            result = json.loads(md.with_suffix('.json').read_text('utf-8'))
            self.assertEqual(result['rule'], 'combined')
            self.assertFalse(result['formal_promotion'])
            self.assertEqual(set(result['rows'][-1]['models']), set(tuning.LABELS))
            snap = result['input_snapshot']
            self.assertEqual(hashlib.sha256(Path(snap['path']).read_bytes()).hexdigest(), snap['sha256'])
            for label in tuning.LABELS.values():
                self.assertIn(label, md.read_text('utf-8'))

    def test_interactive_accept_and_eof_use_the_same_reviewed_result(self):
        def run(history, params):
            center = 33 if params[tuning.PARAMETER] == 8 else 30
            result = {'anchor': {'center': 10}, 'state': {'code': 'cooling_watch'},
                      'sentiment': {'center': center, 'low': center*.9, 'high': center*1.1},
                      'pricing_response': {'rule': 'combined'}}
            return {r['SECURITY_CODE']: (result, result) for r in history}
        for choice in ['y', EOFError()]:
            with tempfile.TemporaryDirectory(dir=self.temp_root) as folder:
                folder = Path(folder)
                path = folder/'params.txt'
                path.write_bytes((ROOT/'策略参数.txt').read_bytes())
                params = config_loader.load_params(path)
                args = SimpleNamespace(params_file=str(path), dataset_path=str(folder/'dataset.json'),
                    output_dir=str(folder/'reports'), train_ratio=.6, min_train_samples=8,
                    sentiment_evaluate_only=False, auto_record_path=str(folder/'record.txt'))
                with patch.object(tuning, '_run', side_effect=run), patch('sys.stdout', new=io.StringIO()), \
                        patch('builtins.input', side_effect=[choice]):
                    code = console.run(args, params, {'items': self.history}, write_updates=tune_params._write_param_updates)
                self.assertEqual(code, 0)
                after = config_loader.load_params(path)
                self.assertEqual({k for k in params if params[k] != after[k]},
                                 {tuning.PARAMETER} if choice == 'y' else set())
                self.assertEqual(after['quality_sentiment_mode'], 'shadow')
                report = next((folder/'reports').glob('*.md')).with_suffix('.json')
                saved = json.loads(report.read_text('utf-8'))
                self.assertEqual(saved['acceptance_status'], 'accepted_shadow' if choice == 'y' else 'not_accepted')
                self.assertEqual((folder/'record.txt').exists(), choice == 'y')


if __name__ == '__main__':
    unittest.main()
