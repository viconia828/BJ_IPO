"""Render the research report and, with matplotlib installed, its static chart."""
from __future__ import annotations

import sys
import os
from pathlib import Path

import analyze_turnover_halt_paths as a


def f(x, digits=2):
    return '—' if x is None else f'{x:.{digits}f}'


def table(headers, rows):
    return '\n'.join(['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join(['---'] * len(headers)) + ' |'] +
                     ['| ' + ' | '.join(str(v) for v in row) + ' |' for row in rows])


def report(x):
    stocks = {r['code']:r for r in x['stocks']}
    sj, kd = [stocks[c] for c in ['920229','920025']]
    hist = [r for r in x['stocks'] if r['date']<'2026-09-22']
    parts = ['# 新股首日换手、临停与持有参考：世纪数码 vs 凯达重工',
             '数据截止：2026-09-23 收盘。仅使用本地行情缓存；交易规则另经北交所官网核对。',
             '**核心结论：两股早盘换手都很活跃，第二个小时凯达并不弱于世纪。真正明显的差异出现在午后：世纪新增成交推动价格突破，凯达新增成交伴随价格下滑。历史数据不能支持“换手达到某数值就应一直持有”的规则。**',
             '**样本与计算口径**',
             f'本地首日分钟文件共 {x["n_total"]} 只；历史对照 {x["n_history"]} 只，覆盖 {x["range"][0]} 至 {x["range"][1]}。世纪和凯达独立列示，不纳入历史分位数、组别中位数和条件频率。该缓存不是全部北交所上市公司的随机样本。',
             '累计换手 T＝累计成交股数÷首日流通股数×100%。新增换手 ΔT 的单位为百分点。42 只以本地首日日行情“成交股数÷日换手率”反推固定流通分母；其余 18 只以 CSV 总量和 replay 日换手率校准。这个分母是事后校准的静态股数，并非已验证的上市前 PIT 股本快照；实盘应用需要先取得当日准确流通股数。',
             '速度 r30＝最近 30 个可交易分钟的新增换手÷30；续量比 R30＝最近 30 分钟速度÷此前 30 分钟速度。09:30 及更早的首笔/集合竞价计入累计换手，排除于连续竞价速度；13:00 独立边界成交并入午后第一个分钟。午休不计时。临停由触及阈值后约 10 分钟无成交空档推断，只扣除可确认的完整空档分钟；触发和复牌所在分钟仍计入，存在约 1 分钟离散误差。',
             '北交所首日临停以**开盘价**为基准，首次达到 ±30%、±60% 分别触发，每次 10 分钟。分析关注上涨方向；分钟推断不等同于核验每份交易所停牌公告。[北交所交易规则](https://www.bse.cn/jygl_list/200028217.html)',
             '凯达 replay 的 2026-09-14 是申购日占位，实际分钟日期为 2026-09-23。世纪实际流通分母约 1,135 万股，凯达约 5,100 万股，不能直接用发行总股数代替。凯达分钟文件比日线少 393,345 股（0.80%），累计换手少 0.77 个百分点；分钟末价 28.98、最高 37.66，日线收盘 29.00、最高 37.69。本文换手和盘中价使用原分钟缓存，收盘收益用日线收价；不把缺失的尾盘成交摊回早盘。',
             '**两股逐段对比**']
    rows=[]
    for t in ['09:35','09:45','10:00','10:30','11:00','11:30','13:10','13:30','14:00','14:30']:
        ss,ks = sj['nodes'][t],kd['nodes'][t]
        rows.append([t,f(ss['turnover']),f(ks['turnover']),f(ss['bias_vwap']),f(ks['bias_vwap'])])
    parts += [table(['时点','世纪累计换手 %','凯达累计换手 %','世纪高于当时 VWAP %','凯达高于当时 VWAP %'],rows),
              '13:30 世纪仍在二停期间，该价是停牌前最后成交价，不能当作当时可以成交的价格。',
              table(['区间','世纪新增换手 pp','凯达新增换手 pp','世纪速度 pp/分','凯达速度 pp/分'],
                    [[k,f(sj['windows'][k]['turnover']),f(kd['windows'][k]['turnover']),f(sj['windows'][k]['rate'],3),f(kd['windows'][k]['rate'],3)] for k in ['10:00-10:30','10:30-11:00','11:00-11:30','10:30-11:30','11:30-13:30','13:30-14:00','14:00-14:30','14:30-15:00']]),
              '11:30–13:30 一行的成交均在午后，午休不计。世纪该段扣除临停完整空档后只有 14 个可交易分钟，凯达为 30 分钟。',
              f'两股 10:30 累计换手为 {sj["nodes"]["10:30"]["turnover"]:.2f}% / {kd["nodes"]["10:30"]["turnover"]:.2f}%；第二小时新增为 {sj["windows"]["10:30-11:30"]["turnover"]:.2f} / {kd["windows"]["10:30-11:30"]["turnover"]:.2f} pp。世纪全天分钟换手 {sj["csv_turnover"]:.2f}%，凯达 {kd["csv_turnover"]:.2f}%（日线 {kd["daily_turnover"]:.2f}%）。这些差距远小于价格表现差距。',
              '截至 10:30，世纪、凯达的 R30 分别为 0.146、0.145；11:30 为 0.861、1.127。上午并没有稳定、明确的换手续航优势能事先挑出世纪。',
              '午后前 10 分钟（13:00 后至 13:10），世纪新增换手 5.59 pp、价格涨 17.90%；凯达新增 1.06 pp、价格跌 4.66%。这是两股量价表现明显分开的节点，但只能在这些成交已经发生后确认，不能当作早盘预知能力。',
              '凯达在 11:17 收于 37.65，11:18 分钟最高 37.66、收于 34.00，单分钟已出现很大回撤；一停阈值为 37.70。到 11:30，换手继续增至 80.00%，价格仍比已出现的高点低 7.73%。应描述为“冲击临停失败后的价格承接弱”，而非“换手不够”。13:30–14:00 又换手 4.35 pp，价格由 32.28 跌到 30.16，14:00 低于 VWAP 4.56%；此时补量并未换来价格修复。',
              '世纪 13:11 触及 79.17（一停），13:21 复牌；13:23 触及 97.44（二停），13:33 复牌。到 14:00 累计已换手 95.65%，随后半小时仅新增 1.72 pp，价格仍由 115.01 涨至 140.02（+21.75%）。这段行情恰恰是**缩量上涨**，不能说“全天换手持续很强”。成交量不能直接辨认主动买卖方向；“卖盘变少”是与现象一致的解释，不是已经证明的资金因果。',
              '世纪按开盘价计算的流通市值约 6.91 亿元，凯达约 14.79 亿元。盘子、题材、估值、市场注意力也可能影响结果，本研究没有把这些混杂因素全部控制。',
              '还可以用 ΔT÷(100%−期初累计换手) 归一化，衡量新增成交相对“尚未换手份额”的大小。第二小时世纪/凯达为 31.83%/32.83%；午后至 13:30 为 39.34%/14.61%；14:00–14:30 为 39.51%/26.98%。这进一步说明明显差异集中在午后初段，晚段绝对缩量也不一定意味着相对于剩余供给很弱。该指标只是 T+1 背景下的近似，不能等同于真实待卖筹码或持有人比例；接近 100% 时分母极小、对缓存误差敏感，不宜作单独阈值。',
              '**历史路径：大换手不能保证二停后起飞**',
              '为使分组可复核，预先写清描述性边界：①二停后大涨＝最高和收盘均至少达到二停价的 1.20 倍；②临停回落＝排除①后，曾触及一停，且收盘比最高低至少 20%，或收在已触及的最高临停阈值之下；③一停失败＝最高在开盘价 +20% 至 +30% 之间且收盘较最高回落至少 10%。其余样本保留为其他路径，不强行归类。这些都是收盘后标签，不能拿来盘中选样。']
    parts.append(table(['历史组别','只数','10:30累计 %','第二小时新增 pp','午后新增 pp','全天累计 %'],
                       [[a.LABELS[g],x['groups'][g]['n'],*[f(x['groups'][g][k]['median']) for k in ['first_hour','second_hour','afternoon','total_turnover']]] for g in a.LABELS]))
    parts += ['表内为中位数。起飞组只有 2 只：振宏股份、维琪科技，不能据此拟合可靠胜率。它们从最高点到收盘也分别回撤 27.68%、22.50%，所以“起飞”并不表示没有大幅回撤。若把成功额外限定为最高点回撤不超过 20%，历史成功组变成 0 只；定义敏感性必须保留。',
              '重点案例如下。涨幅均相对各自当日开盘价，新增换手以百分点计。',
              table(['股票','路径','10:30累计 %','第二小时 pp','午后 pp','最高涨幅 %','收盘涨幅 %'],
                    [[r['name'],a.LABELS[r['group']],f(r['nodes']['10:30']['turnover']),f(r['windows']['10:30-11:30']['turnover']),f(r['windows']['11:30-15:00']['turnover']),f(r['high_gain']),f(r['close_gain'])] for c in ['920200','920176','920065','920059','920086','920078','920229','920025'] for r in [stocks[c]]]),
              '**二停复牌后，重点看“成交能否推高价格”**',
              '从二停复牌起观察 10 个可交易分钟；下表时点已经晚于触发临停，不能作为第一次冲停前的信号。']
    eventrows=[]
    for c in ['920200','920176','920065','920059','920229']:
        r=stocks[c]; s=next(s for s in r['post_halt'] if s['event']=='up60')
        eventrows.append([r['name'],s['resume']+'–'+s['node'],f(s['reopen10_turnover']),f(s['price_vs_trigger']),f(s['reopen10_peak_vs_trigger']),f(s['close_return'])])
    parts += [table(['股票','观察窗口','新增换手 pp','末价较二停价 %','窗口最高较二停价 %','此时持有至收盘 %'],eventrows),
              '振宏、维琪用 2.86–3.73 pp 换手，窗口末价仍在二停价上方 23%–26%；千岸、双英用了 9.41–18.21 pp，末价却仅 +1.25% 或已跌破二停价。世纪为 5.84 pp、末价 +4.68%，随后仍有明显上行。这个对照更接近“价格对成交量的响应”，而非“量越大越好”。',
              '可以把“复牌 10 分钟新增约 3–6 pp、价格能够抬升”记作待验证的有效推进案例；“新增 8–10 pp 以上、价格仍回到二停位附近或下方”记作警觉模式。**这只是 4 个历史案例的观察区间，不是经验证的买卖阈值。** 世纪该窗口最低也曾低于二停价 2.33%，机械地一跌破就判断失败会误判。',
              '**换手活跃程度的参考值：使用分时段分位数**',
              '下表仅衡量成交活跃程度。“偏强”取历史第 75 百分位，不能读成上涨胜率 75%。累计和速度应分别看：早盘放量可以让累计很高，但最近半小时速度仍低。']
    rows=[]
    for t in ['09:35','09:45','10:00','10:30','11:00','11:30','13:30','14:00','14:30']:
        rs=[r['nodes'][t] for r in hist if r['nodes'][t] and r['nodes'][t]['active']]
        cs=a.stats([s['turnover'] for s in rs]);rspeed=a.stats([s['rate30'] for s in rs]);rt=a.stats([s['retention30'] for s in rs])
        rows.append([t,f(cs['median'],1),f(cs['p75'],1),f(rspeed['p25'],3)+'–'+f(rspeed['p75'],3),f(rt['median'],2)])
    parts += [table(['时点','累计换手中位 %','累计活跃前25%门槛 %','速度中央50%区间 pp/分','R30中位'],rows),
              '可简化记为：09:35 累计 30%、09:45 40%、10:00 52%、10:30 65%、11:30 76%，属于较活跃一档。世纪和凯达早盘都符合。这些数值适用本样本，不保证跨市场环境稳定。',
              '10:30 后每半小时新增 3–6 pp（约 0.10–0.20 pp/分）是常见量级；午后若低于 3 pp/半小时，可称较薄，但必须结合价格。R30 在 10:30 自然降至约 0.26、11:00 约 0.56、11:30 约 0.69；不能要求开盘后全天保持 R30≥1。午后 R30 回到 1.2 以上且价格同步创新高，可作为重新关注的条件，尚无足够样本支持其为盈利规则。',
              '**是否值得持股待涨：本次检验没有找到可靠的单一换手门槛**',
              '按盘中可见特征，检验一个朴素条件：11:00 时 r30≥0.15 pp/分、R30≥0.5，并且现价同时高于开盘价与当时 VWAP。历史只有 6 只满足；两只新股在该时点也都满足，说明它无法在上午区分世纪和凯达。']
    v=x['node_outcomes']['11:00']['volume_price_good'];v2=x['node_outcomes']['11:30']['volume_price_good']
    parts += [table(['观察时点与条件','历史只数','持有到收盘上涨','持有到收盘收益中位','之后曾涨≥10%','后30交易分钟末价收益中位'],
                    [['11:00 量价条件',v['n'],f'{v["positive_close_n"]}/{v["n"]}',f(v['close_return']['median'])+'%',f'{v["peak10_n"]}/{v["n"]}',f(v['future30_close_return']['median'])+'%'],
                     ['11:30 同一条件',v2['n'],f'{v2["positive_close_n"]}/{v2["n"]}',f(v2['close_return']['median'])+'%',f'{v2["peak10_n"]}/{v2["n"]}',f(v2['future30_close_return']['median'])+'%']]),
              '11:00 条件的 1/6 是观测频率，95% Wilson 区间约 3.0%–56.4%；7 月 14 日前为 1/4，之后为 0/2。样本既小又有时间差异，不能把 16.7% 当作未来精确胜率。条件为本轮探索，并非预注册规则，也没有独立未来样本的策略验证。',
              '“之后曾涨 10%”使用未来最高价，只说明有过机会，不能当作能实现的收益。相反，11:00 的后 30 分钟末价收益中位数为 +8.75%，而持有至收盘为 -4.03%，说明等待一段上涨和无条件拿到收盘是不同的问题；这仍是 6 例描述，不是扣除滑点费用后的交易策略回测。',
              '另检验“一停失败观察”：先达到开盘价 +27%，至少经过 10 个可交易分钟仍未触及 +30%，且较期间高点回撤至少 5%。历史 6 只触发，4 只此后至收盘下跌，收益中位 -3.22%；但振宏早上 10:03 也触发，之后却大涨。因此这只能降低短期信心，不能一票否决全天行情。凯达在 11:26 触发，累计换手仍高达 79.57%，此后至收盘 -16.62%。',
              '较合适的执行参考是：\n\n- 先用累计换手判断是否活跃；10:30 到 65% 只是进入观察范围。\n- 再看新增换手是否推动价格：价格能否抬高、回撤是否修复、是否守住当时 VWAP。缩量但价格继续抬升，不单凭缩量否定；放量却走低，也不因量足而继续提高信心。\n- 冲击临停失败后若继续回撤，将“等临停”理由降级，同时保留后续重新放量突破时更新判断的空间；一次失败不是全天必败。\n- 二停之后，比较新增换手和价格推进，持续回到二停价附近且放量，并不优于缩量抬价；已在高位时还要单独管理从峰值回撤。\n- 以上针对已有、当日可卖出的持仓。当天新买的股票不能靠同日退出实现这些路径；北交所股票买入当日不得卖出。[北交所交易规则第 3.1.4 条](https://www.bse.cn/jygl_list/200028217.html)',
              '**首日等待与隔夜持有必须分开**',
              '本地日线显示：世纪 9 月 23 日收盘 92.40，较首日 132.00 跌 30%；历史起飞样本振宏、维琪的次日收盘也均较首日跌约 30%。这里只是 3 个特定案例，不能外推为普遍概率，但足以说明“首日换手优秀”不能直接支持隔夜持有。',
              '**校验、局限与复现**',
              f'逐一检查 {x["validation"]["prefix_node_checks"]} 个可用时点：将分钟数据截断到当时，累计换手、近期速度、续量比、VWAP、相对开盘涨幅和当时回撤与全日回放计算一致。处于临停的节点不进入可执行时点统计。未来峰值、最低价和收盘收益只作结果列，未进入特征。',
              '42 只使用日线流通分母，18 只依赖 replay 的日换手率，因此仍有旧缓存口径风险。三个文件存在少量“分钟金额÷股数”超分钟高低价的记录（瑞尔竞达 1 条、振宏 4 条、吉和昌 1 条），均保留原值；对应换手分别约 0.008、0.159、0.036 pp，主要量级比较不依赖这些异常。所有原始 CSV 文件、正式参数均未修改。',
              '原始分钟缺口、尾盘竞价、临停分钟离散化、股本分母事后校准、仅 2 个历史起飞案例和未控制的题材/环境/流通市值，限制了推断力度。本报告支持的是“活跃程度参考”和“量价结合观察”，不支持自动满仓持有或精确胜率。',
              '复现：在项目根目录运行 `.venv/Scripts/python.exe tools/analyze_turnover_halt_paths.py`，再运行 `tools/render_turnover_halt_study.py`。图形生成需要 matplotlib；研究计算只使用标准库。',
              '文件：`analysis.json` 包含全部样本、组别、节点、条件统计及原分钟 SHA-256；`stock_summary.csv` 为逐股审计表，`node_metrics.csv` 为逐时点计算明细。',
              '**全部历史样本及归类**',
              table(['代码','名称','上市日','归类','分钟累计换手 %','分钟量较日线偏差 %'],
                    [[r['code'],r['name'],r['date'],a.LABELS[r['group']],f(r['csv_turnover']),f(r['daily_vol_diff_pct'])] for r in hist])]
    if (a.OUT/'换手与临停对比.png').exists():
        parts.insert(3,'![换手与临停对比]('+str((a.OUT/'换手与临停对比.png').as_posix())+')')
    (a.OUT/'研究报告.md').write_text('\n\n'.join(parts)+'\n',encoding='utf-8')


def chart(x):
    os.environ.setdefault('MPLCONFIGDIR',str(a.ROOT/'.tmp/turnover_mpl_cache'))
    local_deps=a.ROOT/'.tmp/turnover_plot_deps'
    if local_deps.exists():
        sys.path.insert(0,str(local_deps))
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        print('Report written. Matplotlib unavailable; chart skipped.')
        return
    plt.rcParams.update({'font.family':'Microsoft YaHei','axes.unicode_minus':False,'font.size':10,
                         'axes.spines.top':False,'axes.spines.right':False,'axes.titleweight':'bold'})
    fig,axs=plt.subplots(2,2,figsize=(15,10),layout='constrained')
    stocks={r['code']:r for r in x['stocks']}
    colors={'920229':'#147D92','920025':'#D46B3B'}
    for c in ['920229','920025']:
        r=stocks[c];bars,_=a.read_bars(a.ROOT/r['bar_source']); cum=0;ts=[];tov=[];returns=[];rates=[];rts=[]
        for b in bars:
            cum+=b['volume'];ts.append(b['slot']);tov.append(cum/r['shares']*100);returns.append(a.pct(b['close'],r['open']))
            if b['slot']>=60:
                s=a.snapshot(bars,r['events'],b['t'],r['shares'],r['close'],r['open'])
                if s['active']:rates.append(s['rate30']);rts.append(b['slot'])
        label=r['name']+' '+r['date'][5:]
        axs[0,0].plot(ts,tov,color=colors[c],lw=2.2,label=label)
        axs[0,1].plot(ts,returns,color=colors[c],lw=2.2,label=label)
        axs[1,0].plot(rts,rates,color=colors[c],lw=2.2,label=label)
    axs[0,0].set(title='累计换手接近，不能解释收益差距',ylabel='累计换手（%）',ylim=(0,105))
    axs[0,0].legend(loc='lower right',frameon=False)
    axs[0,1].set(title='价格差异在午后显著扩大',ylabel='相对开盘涨幅（%）')
    for n in [30,60]:axs[0,1].axhline(n,color='#99A4AA',ls='--',lw=.8)
    axs[1,0].set(title='最近30个可交易分钟：世纪后段也明显缩量',ylabel='换手速度（百分点 / 分钟）',ylim=(0,.65))
    for ax in [axs[0,0],axs[0,1],axs[1,0]]:
        ax.set_xticks([0,60,120,180,240],['09:30','10:30','午休','14:00','15:00'])
        ax.axvline(120,color='#BBC3C8',ls=':',lw=1)
        ax.grid(axis='y',color='#DFE4E6',lw=.6);ax.set_xlim(0,240)
    ax=axs[1,1]
    for c in ['920200','920176','920065','920059','920229']:
        r=stocks[c];s=next(s for s in r['post_halt'] if s['event']=='up60')
        color=colors.get(c,'#357D60' if r['group']=='double_fly' else '#A66565')
        ax.scatter(s['reopen10_turnover'],s['price_vs_trigger'],s=90,color=color,zorder=3)
        ax.annotate(r['name'],(s['reopen10_turnover'],s['price_vs_trigger']),xytext=(7,7),textcoords='offset points',fontsize=10)
    ax.axhline(0,color='#99A4AA',ls='--',lw=.8)
    ax.set(title='二停复牌后10分钟：量大不等于推进有效',xlabel='10分钟新增换手（百分点）',ylabel='窗口末价相对二停价（%）',xlim=(0,23),ylim=(-17,35))
    ax.grid(color='#DFE4E6',lw=.6)
    fig.suptitle('世纪数码 vs 凯达重工｜换手活跃 ≠ 上涨持续',fontsize=20,color='#20323B')
    fig.supxlabel('本地首日分钟缓存；历史对照58只（其中二停后大涨2只）。临停空档不计入速度；最新两股不参与历史统计。',fontsize=10,color='#53626A')
    fig.savefig(a.OUT/'换手与临停对比.png',dpi=160,facecolor='white')
    fig.savefig(a.OUT/'换手与临停对比.svg',facecolor='white')
    plt.close(fig)


if __name__=='__main__':
    data=a.read_json(a.OUT/'analysis.json')
    report(data)
    chart(data)
    print(a.OUT/'研究报告.md')
