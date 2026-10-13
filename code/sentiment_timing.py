"""Sequential experiment: confirmed two-event feedback, without market feeds."""
from copy import deepcopy
import math
import statistics

import sentiment_roles as roles
import sentiment_pricing as previous

VERSION='sentiment_timing_v1'
MATERIAL_LOG=math.log(1.10)
# Kept until BOTH staged checks justify a different shadow default.
DEFAULT_SHADOW_RULE='combined'


def response(signals):
    dates=signals['recent_listing_dates'][-2:]
    original=signals['expectation']
    by_date={r['listing_date']:r for r in original['date_groups']}
    groups=[by_date[d] for d in dates if d in by_date]
    complete=len(dates)==2 and len(groups)==2
    consistent=complete and all(abs(r['value'])>MATERIAL_LOG for r in groups) and \
        roles.direction(groups[0]['value'])==roles.direction(groups[1]['value'])
    selected=deepcopy(signals)
    if consistent:
        raw=statistics.mean(r['value'] for r in groups)
        shrink=2/(2+previous.PRIOR_GROUPS)
        selected['expectation']={**original,'available':True,'group_count':2,'date_groups':deepcopy(groups),
            'observations':[deepcopy(r) for r in original['observations'] if r['listing_date'] in dates],
            'raw_log_signal':raw,'shrinkage':shrink,'log_correction':raw*shrink,'reason':None}
    result=roles.response(selected)
    result['rule']='role_adaptive'
    result['timing']={'fast_mode':bool(consistent),'latest_two_dates':dates,
        'eligible_group_count':len(groups),'material_log':MATERIAL_LOG,
        'three_group_log_correction':original['log_correction'],
        'selected_log_correction':selected['expectation']['log_correction'],
        'reason':'最新两组同向且各自达到阈值，使用两组' if consistent else '最新两组缺失、异向或未达到阈值，使用三组'}
    return result


def evaluate_all(target,history,params,*,as_of):
    results=roles.evaluate_all(target,history,params,as_of=as_of)
    signals=results['combined']['pricing_response']['signals']
    result=roles.apply_response(results['baseline'],response(signals))
    result['version']=VERSION
    results['role_adaptive']=result
    return results


def attach_shadow(baseline,target,history,params,*,as_of):
    """Preserve the accepted default; keep newer experiments explicitly separate."""
    output=deepcopy(baseline)
    try:
        results=evaluate_all(target,history,params,as_of=as_of)
    except (ValueError,ArithmeticError) as exc:
        output=previous.attach_shadow(baseline,target,history,params,as_of=as_of)
        output['response_experiment_error']=f'分工和转向试算不可用：{type(exc).__name__}: {exc}'
        return output
    def summary(candidate):
        return {'version':candidate['version'],'mode':'shadow','formal_eligible':False,
                'available':candidate['available'],'as_of_exclusive':candidate['as_of_exclusive'],
                'sentiment':candidate['sentiment'],'pricing_response':candidate['pricing_response']}
    output['response_shadow']=summary(results[DEFAULT_SHADOW_RULE])
    output['response_experiments']={rule:summary(results[rule]) for rule in ['role_split','role_adaptive']}
    return output
