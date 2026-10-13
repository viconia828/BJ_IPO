"""Bounded, cached supplements for the frozen September/October IPO cohort."""
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'code'))
import config_loader
import tushare_helper as ts
from local_file_db import LocalFileDB
from ifind_helper import fetch_financials, now_iso

FOLDER = ROOT / 'data/fundamental_cases/raw/stage3'
# Chosen from disclosed business relationships, before computing new forecasts.
QUOTES = {
    '20260902': ['002601.SZ'],
    '20260908': ['301033.SZ', '002901.SZ', '920009.BJ', '688198.SH', '300653.SZ',
                 '920166.BJ', '300171.SZ', '300412.SZ', '300813.SZ', '688755.SH', '300358.SZ'],
    '20260922': ['300971.SZ', '002347.SZ', '603135.SH'],
    '20260923': ['688366.SH', '300653.SZ'],
}


def main():
    FOLDER.mkdir(parents=True, exist_ok=True)
    settings = ts._build_settings(config_loader.load_params())
    db = LocalFileDB(settings['cache_root'])
    codes = sorted({code for values in QUOTES.values() for code in values})
    for code in codes:
        jobs = [('income_full', 'income', {'ts_code': code, 'report_type': '1'},
                 'ts_code,ann_date,f_ann_date,end_date,report_type,revenue,n_income_attr_p,update_flag'),
                ('fina_indicator', 'fina_indicator', {'ts_code': code},
                 'ts_code,ann_date,end_date,profit_dedt,netprofit_yoy,tr_yoy')]
        jobs += [('daily_basic_' + date, 'daily_basic', {'ts_code': code, 'trade_date': date},
                  'ts_code,trade_date,close,total_mv,pe_ttm,total_share')
                 for date, values in QUOTES.items() if code in values]
        for label, api, request, fields in jobs:
            path = FOLDER / f'{code}_{label}.json'
            if path.exists():
                continue
            rows, error = ts._call_tushare_api(api, request, fields, settings, db)
            with path.open('x', encoding='utf-8', newline='\n') as output:
                json.dump({'source': 'tushare_' + api, 'retrieved_at': now_iso(), 'request': request,
                           'fields': fields, 'rows': rows, 'error': error}, output, ensure_ascii=False, indent=2)
            print(code, label, len(rows), 'error' if error else 'ok', flush=True)
    result = fetch_financials(codes, ['20231231', '20241231', '20250630', '20251231', '20260630'], FOLDER / 'api')
    print('iFind API', len(result), 'success', sum(r.get('response', {}).get('errorcode') == 0 for r in result), flush=True)


if __name__ == '__main__':
    main()
