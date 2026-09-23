"""Follow-up: temporal ordering of turnover, halts, and afternoon confirmation."""
from __future__ import annotations

import csv
import hashlib
import json
import math

import analyze_turnover_halt_paths as a


def wilson(k, n):
    if not n:
        return None
    z=1.96; p=k/n; den=1+z*z/n
    m=(p+z*z/(2*n))/den; h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return [100*(m-h),100*(m+h)]


def table(head, rows):
    return '\n'.join(['| '+' | '.join(head)+' |','| '+' | '.join(['---']*len(head))+' |']+
                     ['| '+' | '.join(str(v) for v in row)+' |' for row in rows])


def fmt(x):
    return '—' if x is None else f'{x:.2f}'


def band(x):
    return '<60' if x<60 else '60-80' if x<80 else '>=80'


def summarize(rows):
    return {'n':len(rows), 'fly':sum(r['group']=='double_fly' for r in rows),
            'fade':sum(r['group']=='halt_fade' for r in rows),
            'near_fail':sum(r['group']=='near_fail' for r in rows),
            'codes':[r['code'] for r in rows]}


def main():
    source=a.OUT/'analysis.json'; data=a.read_json(source); stocks=data['stocks']
    evrows=[]; pmrows=[]; checks=0
    failed_am={r['code'] for r in data['near_halt_failure']['rows'] if r['node']<='11:30'}
    for r in stocks:
        path=a.ROOT/r['bar_source']; assert hashlib.sha256(path.read_bytes()).hexdigest()==r['sha256']
        bars,_=a.read_bars(path)
        for ev in r['events']:
            if ev['type'] not in ['up30','up60']:
                continue
            idx=ev['hit_index']; before=bars[:idx]; upto=bars[:idx+1]
            tb=sum(b['volume'] for b in before)/r['shares']*100
            ti=sum(b['volume'] for b in upto)/r['shares']*100
            # Assert all turnover comes from the trigger-minute prefix.
            assert all(b['t']<ev['time'] for b in before)
            assert all(b['t']<=ev['time'] for b in upto)
            resume=ev['resume_time']; future=[b for b in bars if resume and b['t']>=resume and b['volume']>0]
            # Administrative close limits observation; use >=20 tradable minutes
            # remaining so a 14:56 second threshold touch is not called a failure.
            left=len(a.active_slots(bars,r['events'],'15:00')-a.active_slots(bars,r['events'],ev['time']))
            evaluable=bool(ev['corroborated'] and left>=20)
            evrows.append({'code':r['code'],'name':r['name'],'date':r['date'],'group':r['group'],
                           'event':ev['type'],'time':ev['time'],'resume':resume,
                           'turnover_1030':r['nodes']['10:30']['turnover'],
                           'turnover_before_trigger_minute':tb,'turnover_through_trigger_minute':ti,
                           'before_band':band(tb),'trigger_before_1030':ev['time']<'10:30',
                           'corroborated':ev['corroborated'],'remaining_active_minutes':left,'evaluable':evaluable,
                           'close_vs_threshold':a.pct(r['close'],ev['threshold']),
                           'post_resume_peak_vs_threshold':a.pct(max(b['high'] for b in future),ev['threshold']) if future else None})
            checks+=1
        if r['code'] not in failed_am|a.TARGETS:
            continue
        am=[b for b in bars if b['t']<='11:30']; amclose=am[-1]['close']; amhigh=max(b['high'] for b in am)
        for node in ['13:05','13:10','13:15','13:20']:
            s=a.snapshot(bars,r['events'],node,r['shares'],r['close'],r['open'])
            dt=a.window(bars,r['events'],'11:30',node,r['shares'])['turnover']
            pmgain=a.pct(s['price'],amclose)
            # This coarse rule was specified before looking at its matched cohort.
            # It remains exploratory and is not a preregistered trading strategy.
            candidate=bool(node=='13:10' and r['code'] in failed_am and not s['hit30_sofar'] and dt<2 and pmgain<=0)
            prefix=[b for b in bars if b['t']<=node]
            prefix_pm=sum(b['volume'] for b in prefix if b['t']>'11:30')/r['shares']*100
            assert abs(dt-prefix_pm)<1e-10
            assert s['price']==next(b['close'] for b in reversed(prefix) if b['volume']>0)
            checks+=1
            pmrows.append({'code':r['code'],'name':r['name'],'date':r['date'],'group':r['group'],'node':node,
                           'am_failed_observed':r['code'] in failed_am,'pm_turnover':dt,'pm_return':pmgain,
                           'price':s['price'],'vs_am_high':a.pct(s['price'],amhigh),'vwap_bias':s['bias_vwap'],
                           'hit30_sofar':s['hit30_sofar'],'active':s['active'],
                           'candidate':candidate,'future_peak':s['future_peak'],'close_return':s['close_return']})
    thresholds={}
    for period,rs in [('history',[r for r in stocks if r['date']<'2026-09-22']),('including_latest',stocks)]:
        thresholds[period]={str(cut):{'above':summarize([r for r in rs if r['nodes']['10:30']['turnover']>=cut]),
                                     'below':summarize([r for r in rs if r['nodes']['10:30']['turnover']<cut])}
                            for cut in [60,65,70,75]}
    events={}
    for event in ['up30','up60']:
        events[event]={}
        for b in ['<60','60-80','>=80']:
            rs=[r for r in evrows if r['event']==event and r['before_band']==b and r['evaluable']]
            events[event][b]=dict(summarize(rs),fade_wilson95=wilson(sum(r['group']=='halt_fade' for r in rs),len(rs)))
    matched=[r for r in pmrows if r['candidate'] and r['date']<'2026-09-22']
    result={'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'events':evrows,'afternoon':pmrows,
            'fixed_clock_thresholds':thresholds,'event_bands':events,'validation_checks':checks,
            'pm_rule_history':{'n':len(matched),'negative_close_n':sum(r['close_return']<0 for r in matched),
                               'median_close':a.stats([r['close_return'] for r in matched]),'codes':[r['code'] for r in matched]},
            'notes':['Minute volume does not identify buyer/seller intention or large individual orders.',
                     'Bands are exploratory, with time of day fully confounded with low/high turnover for evaluable second halts.',
                     'Turnover denominators are the same retrospective share counts as the base report.']}
    (a.OUT/'拉停前换手与午后确认.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    for name,rows in [('拉停前换手.csv',evrows),('冲停失败后的午后.csv',pmrows)]:
        with (a.OUT/name).open('w',encoding='utf-8-sig',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    seconds=[r for r in evrows if r['event']=='up60']
    report=[
        '# 补充研究：10:30换手、拉停前换手与午后确认',
        '截至 2026-09-23。基础样本仍为 60 只；历史统计单列 58 只，纳入最新两股的结果明确标识。',
        '**结论：较充分的前期换手可能是顺利二停后继续上涨的准备条件；在本样本中，拉停前累计换手比固定10:30累计换手区分更明显。但不能把这一观察解读为经验证的回落概率，也不能将70%当硬门槛。**',
        '**先后顺序是关键**',
        '主检验使用触停分钟之前的累计成交，触停分钟本身不计入，避免把该分钟的拉升成交当作预先准备；同时列示包含触停分钟的常用口径。分钟内先后仍无法还原。全部分母沿用原报告的事后股本校准口径。',
        table(['股票','二停时间','10:30累计%','二停前一分钟累计%','含触停分钟累计%','收盘较二停价%','观察是否充足'],
              [[r['name'],r['time'],fmt(r['turnover_1030']),fmt(r['turnover_before_trigger_minute']),fmt(r['turnover_through_trigger_minute']),fmt(r['close_vs_threshold']), '是' if r['evaluable'] else '否，尾盘触及'] for r in seconds]),
        '振宏 10:30 为 68.66%，不是超过70%；凯达 70.23%却没有触发一停。双英虽在10:30达到72.62%，但09:51已经二停，因此72.62%不能解释为二停前已有充分换手。',
        '剔除嘉晨14:56才触及二停、复牌后仅数分钟的时间不足样本，余下5例：低换手的千岸、双英二停前分别55.91%、43.39%，均在收盘落到二停价以下；高换手的振宏、维琪、世纪为86.79%、89.81%、87.95%，收盘均高于二停价20%。',
        '因此“二停前换手不足，后续更容易回落”在这5例中得到支持，但只有低组2例、高组3例。低组观测回落2/2的95% Wilson区间约34.2%–100%；不是未来回落概率100%。此外，低组恰好都上午较早二停，高组都是更晚二停，时间、换手、存量卖盘释放共同变化，无法识别换手的独立因果作用。',
        '本样本二停前60%–80%没有可充分观察的案例，不能推导70%、75%或80%哪个是有效分界。两个历史成功加世纪才3例，世纪用于提出和复核假设，不是严格样本外验证。',
        '千岸复牌后也一度达到二停价上方约25%，随后才回落；“回落”不等于二停后完全没有冲高机会。本表终局与原报告一致，使用收盘保留涨幅衡量。',
        '**一停样本提供的旁证（含最新世纪）**',
        table(['一停前累计换手','可观察样本','临停回落','二停后大涨','其他'],
              [[b,events['up30'][b]['n'],events['up30'][b]['fade'],events['up30'][b]['fly'],events['up30'][b]['n']-events['up30'][b]['fade']-events['up30'][b]['fly']] for b in ['<60','60-80','>=80']]),
        '一停前低于60%的4例全部归入临停回落；达到80%的4例中3例最终二停后大涨、1例仍回落（农大）。因此高换手仍是准备条件，不是充分条件。样本少、分段是探索性的，表格不能当作未来精确胜率。',
        '**10:30可以当筛选节点，但不要硬卡70%**',
        table(['10:30门槛','达到门槛只数（全部60只）','其中二停后大涨','未达到只数','其中二停后大涨'],
              [[str(c)+'%',thresholds['including_latest'][str(c)]['above']['n'],thresholds['including_latest'][str(c)]['above']['fly'],thresholds['including_latest'][str(c)]['below']['n'],thresholds['including_latest'][str(c)]['below']['fly']] for c in [60,65,70,75]]),
        '约65%–70%可作为换手较充分的观察区间：本样本三例大涨都在65%以上，但达到65%的16只中只有3只最终归入大涨。达到70%的10只中只有2只大涨，另外3只临停回落、5只接近一停但失败回落。固定节点越高并不意味着越可靠。该统计是描述性筛选，不是扣费用的策略收益。',
        '**上午冲停失败，午后是否继续等：再加一次确认**',
        '上午失败沿用原报告盘中条件：先到开盘价+27%，至少10个交易分钟仍未到+30%，并且从当时最高回撤≥5%。历史有4只上午触发：族兴、普昂、创达、振宏。凯达也触发；世纪上午最高只比开盘高14.12%，没有进入接近一停的失败模式。',
        '13:10观察午后新增换手、相对11:30的价格变化，且此时还未触及一停：',
        table(['股票','午后前10分钟换手pp','相对午间收价%','此时较上午最高%','随后最高较此时%','持有至收盘%'],
              [[r['name'],fmt(r['pm_turnover']),fmt(r['pm_return']),fmt(r['vs_am_high']),fmt(r['future_peak']),fmt(r['close_return'])] for r in pmrows if r['node']=='13:10']),
        '凯达13:05新增0.60pp、价格已比午间低5.27%；13:10新增1.06pp、价格仍低4.66%，没有修复上午冲停失败；等到14:00看到换手增加再决定，已不是同一个风险位置。世纪13:05新增3.55pp并涨9.22%，13:10新增5.59pp并涨17.90%，量价同步确认明显更强。',
        '振宏是重要反例：上午也冲停失败，但13:10已新增2.29pp、比午间涨2.57%，不是“上午失败＋午后仍无修复”，其后才继续大涨。因此不宜把“上午一次失败”直接等同于全天放弃。',
        '可试观察规则：上午已冲停失败，13:10仍未临停，午后新增换手<2pp且现价不高于午间收价，则降低继续等临停的意愿。该条件只命中历史族兴与创达两例：从13:10到收盘分别-6.02%、+0.33%，后续最高机会分别+2.37%、+2.56%；凯达命中后为-12.47%，后续最高仅+1.45%。这是小样本风险筛查，尚不是可自动执行的卖出策略。',
        '**如何理解“换手充分后拉升”**',
        '北交所股票买入当日不得卖出，因此早盘成交可能把一部分当天可卖筹码转移到当日不能再卖的新买方；前期换手充分后，后续上攻面对的潜在当日卖盘可能减少。这给假设提供机制解释，但并未证明每次低换手拉停都是“强拉”，也不能用换手率精确推算剩余可卖筹码。[北交所交易规则第3.1.4条](https://www.bse.cn/jygl_list/200028217.html)',
        '对“14点放量是失望卖出、尾盘大单保不收绿”的解释，分钟缓存只能确认量价变化。它没有逐笔方向、单笔规模、订单和账户信息，无法验证这些交易意图；凯达收盘29.00与开盘29.00相同是价格事实，但“保住”的动机仍需盘口证据。',
        f'本次验证：60只输入CSV的SHA-256与前次快照一致；{checks}个触停前缀/午后特征检查通过。所有输入和正式策略参数未修改。补充数据文件为“拉停前换手.csv”“冲停失败后的午后.csv”“拉停前换手与午后确认.json”。']
    (a.OUT/'补充研究_拉停前换手与午后确认.md').write_text('\n\n'.join(report)+'\n',encoding='utf-8')
    print(json.dumps({'event_bands':events,'pm_rule_history':result['pm_rule_history'],'validation_checks':checks},ensure_ascii=False))


if __name__=='__main__':
    main()
