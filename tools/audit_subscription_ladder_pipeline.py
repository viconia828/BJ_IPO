"""Audit manual ladder ingestion and derived account conservation without syncing.

Writes a rebuilt account table only below --output-dir. Production refresh is
deliberately a separate invocation of build_account_pool_history after review.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
import math
from pathlib import Path

import build_account_pool_history as pool
import subscription_ladder_labels as labels

ROOT=Path(__file__).resolve().parents[1]


def read_csv(path):
    with Path(path).open(encoding='utf-8-sig',newline='') as handle:
        return list(csv.DictReader(handle))


def audit(output_dir: Path, before_dir: Path | None = None):
    output_dir.mkdir(parents=True,exist_ok=True)
    raw_labels=labels.load_label_rows()
    history=read_csv(pool.DEFAULT_HISTORY_PATH)
    rows=pool._merge_rows(history,raw_labels)
    unchanged_paths=(pool.DEFAULT_HISTORY_PATH,pool.DEFAULT_LADDER_LABEL_PATH,ROOT/'策略参数.txt')
    hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in unchanged_paths}
    duplicate_codes=[k for k,n in Counter(x['security_code'] for x in raw_labels).items() if n>1]
    assert not duplicate_codes,duplicate_codes
    state={}; checks=[]; expected_points=[]; parsed_items=0
    for r in rows:
        items=labels.parse_manual_ladder(r.get('manual_ladder'),r.get('top_apply_amount_wan'))
        parsed_items+=len(items)
        assert all(x['threshold_amount_wan'] is not None and x['threshold_amount_wan']>0 for x in items),r['security_code']
        # Verify every nonempty CSV token was consumed, not just regex matches.
        tokens=[x.strip() for x in str(r.get('manual_ladder') or '').replace('；',';').split(';') if x.strip()]
        assert len(tokens)==len(items),(r['security_code'],tokens,items)
        manual=pool._manual_points(r)
        fit=pool._parse_json_object(r.get('allocation_fit_json'))
        fitted=pool._fit_points_by_lot(pool._bucket_rows(fit))
        lots=float(r.get('online_issue_shares') or 0)/100
        accounts=float(r.get('online_allocated_accounts') or 0)
        if manual and accounts:
            max_lots=max(max(manual),max(fitted,default=1),math.ceil(lots/accounts))
            thresholds=pool._complete_lot_thresholds(manual_by_lot=manual,fit_by_lot=fitted,
                max_lots=max_lots,top_apply_amount_wan=float(r['top_apply_amount_wan']))
            assert all(abs(thresholds[k]-x['threshold_amount_wan'])<1e-8 for k,x in manual.items()),r['security_code']
            cumulative,basis=pool._announcement_constrained_cumulative(allocated_accounts=accounts,total_lots=lots,
                thresholds_by_lot=thresholds,prior_state=state)
            assert cumulative and cumulative[1]==accounts,(r['security_code'],basis)
            values=[cumulative[k] for k in sorted(cumulative)]
            assert all(a>=b>=0 for a,b in zip(values,values[1:])),r['security_code']
            residual=sum(values)-lots
            # An announced final account may receive a partial lot (<100 shares).
            allowed_partial=lots<accounts and accounts==math.ceil(lots)
            assert abs(residual)<1e-4 or allowed_partial,(r['security_code'],residual)
            checks.append({'security_code':r['security_code'],'source_lots':lots,'source_accounts':accounts,
                'manual_thresholds':manual,'effective_thresholds':thresholds,'cumulative_accounts':cumulative,
                'lot_residual':residual,'partial_final_lot':allowed_partial,'basis':basis})
        points=pool._build_points_for_row(r,state)
        expected_points.extend(points)
        pool._update_threshold_state(state,points,r['security_code'])
    summary=pool.build_account_pool_history(points_path=output_dir/'account_pool_history_points.csv',
        thresholds_path=output_dir/'account_pool_history_thresholds.csv',summary_path=output_dir/'account_pool_history_summary.json',
        force_rebuild=True)
    written=read_csv(output_dir/'account_pool_history_points.csv')
    assert len(written)==len(expected_points)
    for actual,expected in zip(written,expected_points):
        for key in ('security_code','manual_ladder_item'):
            assert actual[key]==expected[key]
        assert abs(float(actual['threshold_amount_wan'])-expected['threshold_amount_wan'])<1e-6
        assert abs(float(actual['accounts_ge_threshold'])-expected['accounts_ge_threshold'])<1e-6
    changes=[]
    if before_dir:
        old=read_csv(before_dir/'data/offline_tuning/account_pool_history_points.csv')
        old_by_key={(x['security_code'],x['manual_ladder_item']):x for x in old}
        for r in written:
            previous=old_by_key.get((r['security_code'],r['manual_ladder_item']))
            old_count=float(previous['accounts_ge_threshold']) if previous else None
            new_count=float(r['accounts_ge_threshold'])
            if old_count is None or abs(new_count-old_count)>1e-5:
                changes.append({'security_code':r['security_code'],'manual_ladder_item':r['manual_ladder_item'],
                                'old_accounts':old_count,'new_accounts':new_count})
    assert all(hashlib.sha256(p.read_bytes()).hexdigest()==hashes[str(p)] for p in unchanged_paths)
    report={'label_rows':len(raw_labels),'history_rows':len(history),'parsed_ladder_items':parsed_items,
            'written_account_points':len(written),'account_constraint_checks':len(checks),
            'constraint_failures':0,'manual_threshold_overwrites':0,'unparsed_labels':0,
            'input_hashes':hashes,'input_hashes_unchanged':True,'production_data_written':False,
            'account_pool_build_version':pool.ACCOUNT_POOL_BUILD_VERSION,'checks':checks,'point_changes':changes,
            'change_summary':{'changed_or_added_points':len(changes),
                              'affected_stocks':len({x['security_code'] for x in changes})},'staged_build':summary}
    (output_dir/'audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('checks','point_changes','input_hashes','staged_build')},ensure_ascii=False))
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--before-dir',type=Path)
    args=parser.parse_args()
    audit(args.output_dir,args.before_dir)


if __name__=='__main__':
    main()
