"""Bounded supplement for the four reviewed IPO cases; immutable local evidence."""
from __future__ import annotations
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'code'))
import config_loader
import tushare_helper as ts
from ifind_helper import fetch_financials, now_iso
from local_file_db import LocalFileDB

FOLDER = ROOT / 'data/fundamental_cases/raw'
EXTRA = ['603348.SH', '002101.SZ', '603982.SH', '300176.SZ']


def main():
    FOLDER.mkdir(parents=True, exist_ok=True)
    settings = ts._build_settings(config_loader.load_params())
    db = LocalFileDB(settings['cache_root'])
    for code in EXTRA:
        calls = [
            ('income_full', {'ts_code': code, 'report_type': '1'},
             'ts_code,ann_date,f_ann_date,end_date,report_type,revenue,n_income_attr_p,update_flag'),
            ('income', {'ts_code': code, 'start_date': '20250101', 'end_date': '20260630', 'report_type': '1'},
             'ts_code,ann_date,f_ann_date,end_date,report_type,revenue,n_income_attr_p,update_flag'),
            ('fina_indicator', {'ts_code': code, 'start_date': '20250101', 'end_date': '20260630'},
             'ts_code,ann_date,end_date,profit_dedt,netprofit_yoy,tr_yoy'),
            ('daily_basic', {'ts_code': code, 'trade_date': '20260928'},
             'ts_code,trade_date,close,total_mv,pe_ttm,total_share'),
        ]
        for api, request, fields in calls:
            path = FOLDER / f'{code}_{api}.json'
            if path.exists():
                continue
            rows, error = ts._call_tushare_api('income' if api == 'income_full' else api, request, fields, settings, db)
            with path.open('x', encoding='utf-8', newline='\n') as out:
                json.dump({'source': 'tushare_' + api, 'retrieved_at': now_iso(), 'request': request,
                           'rows': rows, 'error': error}, out, ensure_ascii=False, indent=2)
            print(code, api, len(rows), 'error' if error else 'ok', flush=True)
    results = fetch_financials(EXTRA[1:], ['20250630', '20251231', '20260630'], FOLDER / 'api')
    print('ifind API', len(results), 'success', sum(r.get('response', {}).get('errorcode') == 0 for r in results))


if __name__ == '__main__':
    main()
