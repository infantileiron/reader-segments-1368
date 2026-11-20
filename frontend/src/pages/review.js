import {state} from '../stores/session.js';import {esc} from '../utils/html.js';import {badge} from '../components/common/status.js';import {table} from '../components/common/table.js';import {field,select} from '../components/common/fields.js';

const fmt=x=>x===null||x===undefined?'—':Number(x).toFixed(2);

function evidence(review){
 return table(['会员编号','原群','成功入样','共同入样','邻域交集/并集','稳定性','结论','依据'],review.members.map(m=>[m.code,'群'+(m.cluster_id+1),m.rounds_sampled,m.rounds_co_sampled,`${m.neighbor_intersection}/${m.neighbor_union}`,fmt(m.stability),m.stable?'稳定':'剔除',m.reason||'—']));
}

function samples(review){
 return table(['轮次','seed','样本数','状态','重算群规模 / 失败依据'],review.samples.map(s=>[s.round,s.seed,s.size,s.status==='ok'?'成功':'失败',s.status==='ok'?(s.cluster_sizes||[]).join(' / '):(s.error||'未知')]));
}

function card(r){
 const sm=r.summary||{},detail=state.reviewDetail&&state.reviewDetail.id===r.id?state.reviewDetail:null;
 const loaded=detail||(r.members?r:null);
 return `<article class="rec"><div class="result-head"><h3>复核 #${r.id} · 分群版本 #${r.segment_id} ${badge(r.state)}</h3><div>${r.state==='done'?`<button data-review-open="${r.id}">${detail?'收起证据':'查看每人证据'}</button> <button data-review-export="${r.id}">导出复核依据</button>`:''}</div></div><p>${r.rounds} 轮抽样 · 邻域稳定性门槛 ${r.threshold} · ${r.sample_count??r.samples?.length??0} 条轮次记录 · ${r.member_count??r.members?.length??0} 位成员${r.k?' · k='+r.k:''}</p>${r.state==='done'?`<p><b>保留 ${sm.stable??0} 位 / 剔除 ${sm.excluded??0} 位</b> · 证据不足 ${sm.insufficient_evidence??0} 位 · 低于门槛 ${sm.below_threshold??0} 位 · 成功轮次 ${sm.completed_rounds??0} / 失败轮次 ${sm.failed_rounds??0}</p>`:`<p class="notice">${esc(r.error||'复核排队中，worker 会从上次完成的轮次继续，已完成轮次不会重复累计。')}</p>`}${loaded?`<details open><summary>每人证据与剔除原因（${loaded.members.length} 人）</summary>${evidence(loaded)}</details><details><summary>抽样轮次、seed 与失败依据（${loaded.samples.length} 条）</summary>${samples(loaded)}</details>`:''}</article>`;
}

export function render(){
 const successful=state.segments.filter(s=>s.state==='done');
 const pending=state.reviews.filter(r=>r.state==='queued'||r.state==='running').length;
 return `<div class="split"><section class="panel"><h2>成功版本也要复核稳定性</h2><p class="notice">复核锁定分群创建时的原始档案快照，按固定 seed 反复抽取子样本重算，比较共同入样成员的邻域关系（同群同伴交集/并集），不按群号硬比。共同入样不足或重算失败的成员不会被当作稳定。${pending?` 当前 ${pending} 条复核待完成，可点击下方刷新查看。`:''}</p>${state.reviews.map(card).join('')||'<p>尚无复核记录。右侧选择成功版本，设置抽样次数与门槛后提交。</p>'}<button id="refresh">刷新复核状态</button></section><aside class="side"><h2>提交稳定性复核</h2><form id="review">${select('成功分群版本','segment_id',successful.map(s=>[s.id,`#${s.id} ${s.name}`]))}${field('抽样次数（2–20）','rounds','number','min=2 max=20 value=5 required')}${field('邻域稳定性门槛（0–1）','threshold','number','min=0.01 max=1 step=0.01 value=0.6 required')}<button class="primary">提交复核</button></form><p>每次复核独立留痕；已完成结论不会被新复核改写。中断续跑按轮号去重，不重复累计。</p></aside></div>`;
}
