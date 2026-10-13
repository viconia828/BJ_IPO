"""Create the immutable, minimal replay input; no forecast or decision fitting."""
from pathlib import Path
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'code'))
import quality_sentiment_data as qdata
from ifind_helper import now_iso


def main():
    path=ROOT/'data/fundamental_cases/inputs/recent_history.json'
    if path.exists():
        print('Frozen history exists; keeping original bytes.'); return
    items, corrections=qdata.load_history()
    fields=['SECURITY_CODE','SECURITY_NAME_ABBR','ISSUE_PRICE','AFTER_ISSUE_PE','LISTING_DATE',
            'AVERAGE_PRICE','CLOSE_PRICE','verified_float_shares','verified_float_source',
            'industry_primary','industry_secondary']
    fields += [prefix+suffix for prefix in ['NEXT','THIRD'] for suffix in
               ['_TRADE_DATE','_DAY_CLOSE','_DAY_FROM_LISTING_CLOSE_PCT']]
    output=[]
    sources={ROOT/'data/offline_tuning/replay_dataset.json',ROOT/'data/quality_sentiment/historical_reference.json'}
    for item in items:
        row={k:item.get(k) for k in fields}
        row['comparable_data']=[{k:p.get(k) for k in ['code','trade_date','mkt_cap','pe_ttm','close','source']}
                                for p in item.get('comparable_data',[])]
        output.append(row)
        source=ROOT/f"data/tushare_db/variable_fields/{item['SECURITY_CODE']}.BJ.json"
        if source.exists(): sources.add(source)
    hashes={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(sources)}
    envelope={'schema':'frozen_recent_fundamental_history_v1','frozen_at':now_iso(),
              'historical_pit_eligible':False,'source_sha256':hashes,
              'note':'当日重建的标准化历史及缓存行情。冻结为了复现，不表示历史时点已存在；剔除所有人工F、v2和前向快照，分别由对照实验安装。',
              'normalization_corrections':corrections,'items':output}
    with path.open('x',encoding='utf-8',newline='\n') as handle:
        json.dump(envelope,handle,ensure_ascii=False,indent=2,allow_nan=False)
        handle.write('\n')
    print('Frozen',len(output),'items',len(hashes),'source hashes')


if __name__=='__main__': main()
