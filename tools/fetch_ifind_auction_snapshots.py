"""Fetch small historical auction snapshot windows for the 60 local IPOs.

Uses the official API with an in-memory access token. Each snapshot is saved
separately; original CSVs and previous research outputs remain untouched.
"""
from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import analyze_turnover_halt_paths as a
from probe_ifind_intraday import post, refresh_access

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/ifind_auction_snapshots_20260923'


def extract(stock, payload):
    response = payload.get('response',{})
    candidates = []
    for tab in response.get('tables',[]) or []:
        if tab.get('thscode') != stock['code']+'.BJ':
            continue
        fields = tab.get('table',{})
        for i, timestamp in enumerate(tab.get('time',[])):
            vals = {k:fields.get(k,[])[i] if i<len(fields.get(k,[])) else None for k in ['latest','volume','amount']}
            if (timestamp[:10] == stock['date'] and '09:25:00' <= timestamp[11:] < '09:30:00'
                and all(isinstance(v,(float,int)) for v in vals.values()) and vals['volume'] > 0):
                candidates.append({'time':timestamp,**vals})
    if not candidates:
        return {'code':stock['code'],'name':stock['name'],'date':stock['date'],'status':'no_valid_auction','errorcode':response.get('errorcode'),'errmsg':response.get('errmsg')}
    record = max(candidates,key=lambda r:r['time'])
    consistent = abs(record['latest']-stock['open'])<.005 and abs(record['amount']/record['volume']/stock['open']-1)<.001
    unique = len({(r['volume'],r['amount']) for r in candidates})
    return {'code':stock['code'],'name':stock['name'],'date':stock['date'],
        'status':'verified' if consistent else 'price_or_amount_mismatch',
        'snapshot_time':record['time'],'auction_price':record['latest'],
        'auction_volume':record['volume'],'auction_amount':record['amount'],
        'auction_turnover':record['volume']/stock['shares']*100,
        'cached_opening_bar_turnover':stock['auction_turnover'],
        'opening_bar_excess_pp':stock['auction_turnover']-record['volume']/stock['shares']*100,
        'snapshot_count_after_match':len(candidates),'distinct_auction_values':unique,
        'denominator_source':stock['denominator_source'],'shares':stock['shares']}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--codes',default='920229,920025,920086,920288')
    parser.add_argument('--all',action='store_true')
    args=parser.parse_args()
    stocks=a.read_json(ROOT/'outputs/turnover_halt_study_20260923/analysis.json')['stocks']
    selected=stocks if args.all else [s for s in stocks if s['code'] in args.codes.split(',')]
    OUT.mkdir(parents=True,exist_ok=True)
    missing=[s for s in selected if not (OUT/f"{s['code']}_{s['date']}.json").exists()]
    token=None
    refresh_status={'status':'not_needed'}
    if missing:
        token,refresh_status=refresh_access()
        if not token:
            token=os.environ.get('IFIND_AUTH_TOKEN','')
        if not token:
            raise RuntimeError('iFinD authentication unavailable')
    def fetch(s):
        path=OUT/f"{s['code']}_{s['date']}.json"
        if path.exists():
            payload=a.read_json(path)
        else:
            body={'codes':s['code']+'.BJ','indicators':'latest,volume,amount',
                  'starttime':s['date']+' 09:24:50','endtime':s['date']+' 09:25:15'}
            payload={'retrieved_at':datetime.now().isoformat(timespec='seconds'),'endpoint':'snap_shot','request':body,**post('snap_shot',body,token)}
            path.write_text(json.dumps(payload,ensure_ascii=False,indent=2),'utf-8')
        return extract(s,payload)
    results=[]
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures=[pool.submit(fetch,s) for s in selected]
        for f in as_completed(futures):
            r=f.result();results.append(r)
            print(json.dumps(r,ensure_ascii=False),flush=True)
    results.sort(key=lambda r:(r['date'],r['code']))
    manifest={'as_of':'2026-09-23','source':'https://quantapi.51ifind.com/api/v1/snap_shot',
              'refresh_status':refresh_status,'requested_n':len(selected),'verified_n':sum(r['status']=='verified' for r in results),
              'rows':results}
    (OUT/('manifest.json' if args.all else 'probe_manifest.json')).write_text(json.dumps(manifest,ensure_ascii=False,indent=2),'utf-8')


if __name__=='__main__':
    main()
