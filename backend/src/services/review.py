"""稳定性复核与触达冻结依据的业务编排。

关键不变量：
* 复核只读分群版本创建时冻结的原始档案快照，不读取当前 readers 表；
* 触达草案必须绑定一条已完成复核，成员为该群中去掉被剔除者后的交集；
* 签核时把复核依据（轮次、样本、成员证据）整体冻结为快照，并记录摘要；
* 草案保存的复核结论与签核读取的结论必须是同一条，期间若复核状态变化则
  拒绝签核，避免把两次结论混用；已签核方案不再接受改动。
"""
import copy
from datetime import datetime, timezone

from src.controllers.business import ensure_segment
from src.models.entities import Outreach, OutreachBasis, Review
from src.services.business import audit
from src.services.analysis import snapshot_checksum
from src.services.stability import digest_of, new_state, state_digest, verify_digest


class Conflict(Exception):
    pass


class Invalid(Exception):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def create_review(session, user, segment_id, rounds, threshold):
    segment = ensure_segment(session, segment_id)
    if segment.state != 'done':
        raise Conflict('仅成功分群可提交稳定性复核')
    snapshot = (segment.result or {}).get('snapshot') or []
    members = (segment.result or {}).get('members') or []
    if not snapshot or not members:
        raise Conflict('分群结果缺少冻结档案，不能复核')
    if segment.result.get('checksum') != snapshot_checksum(snapshot):
        raise Conflict('分群冻结档案与 checksum 不一致，请重新训练后再复核')
    baseline = {m['code']: int(m['cluster_id']) for m in members}
    checksum = segment.result['checksum']
    state = new_state(copy.deepcopy(snapshot), list(segment.features), int(segment.k), rounds, threshold, baseline, checksum)
    review = Review(segment_id=segment.id, state='queued', rounds=int(rounds), threshold=float(threshold),
                    features=list(segment.features), k=int(segment.k), snapshot=copy.deepcopy(snapshot),
                    checksum=checksum, data=state, error='', created=now(), updated=now())
    session.add(review)
    session.commit()
    audit(session, user, f'提交稳定性复核 #{review.id}（版本 #{segment.id}，{rounds} 轮，门槛 {threshold}）')
    return review


def review_payload(review, full=True):
    data = review.data or {}
    payload = {
        'id': review.id,
        'segment_id': review.segment_id,
        'state': review.state,
        'rounds': review.rounds,
        'threshold': review.threshold,
        'features': review.features,
        'k': review.k,
        'checksum': review.checksum,
        'error': review.error or '',
        'created': review.created,
        'updated': review.updated,
        'summary': data.get('summary') or {},
        'digest': data.get('digest') or '',
        'baseline_sizes': data.get('baseline_sizes') or {},
        'finished_at': data.get('finished_at', ''),
        'sample_count': len(data.get('samples') or []),
        'member_count': len(data.get('members') or []),
    }
    if full:
        payload['samples'] = data.get('samples') or []
        payload['members'] = data.get('members') or []
    return payload


def approved_digest(review, data_digest=''):
    payload = {
        'review_id': review.id,
        'review_digest': data_digest,
        'segment_id': review.segment_id,
        'segment_checksum': review.checksum,
        'rounds': review.rounds,
        'threshold': review.threshold,
        'finished_at': (review.data or {}).get('finished_at', ''),
    }
    return digest_of(payload)


def draft_outreach(session, user, data):
    segment = ensure_segment(session, data.segment_id)
    if segment.state != 'done':
        raise Conflict('分群完成后才能制定触达方案')
    review = session.query(Review).filter_by(id=data.review_id).with_for_update().first()
    if not review or review.segment_id != segment.id:
        raise Invalid('所选稳定性复核不属于该分群版本')
    if review.state != 'done':
        raise Conflict('稳定性复核完成后才能起草触达方案')
    if not verify_digest(review.data):
        raise Conflict('复核依据摘要校验失败，请重新复核')
    segment_checksum = (segment.result or {}).get('checksum') or ''
    if not segment_checksum or segment_checksum != review.checksum:
        raise Conflict('复核冻结的档案与分群版本不一致，请对该版本重新复核')
    payload = review_payload(review)
    cluster_members = [m for m in segment.result['members'] if m['cluster_id'] == data.cluster_id]
    if not cluster_members:
        raise Invalid('该版本没有所选群组')
    stable_codes = {m['code'] for m in payload['members'] if m['stable']}
    keep = [m['code'] for m in cluster_members if m['code'] in stable_codes]
    excluded = []
    evidence = {m['code']: m for m in payload['members']}
    for m in cluster_members:
        if m['code'] not in stable_codes:
            e = evidence.get(m['code'], {})
            excluded.append({'code': m['code'], 'reason': e.get('reason') or '稳定性复核未通过'})
    if not keep:
        raise Conflict('该群成员全部被稳定性复核剔除，请先检查复核结果')
    outreach = Outreach(segment_id=segment.id, cluster_id=data.cluster_id, title=data.title,
                        note=data.note, members=keep, status='draft')
    session.add(outreach)
    session.flush()
    basis = OutreachBasis(outreach_id=outreach.id, review_id=review.id, review_digest=payload['digest'],
                          members=keep, excluded=excluded,
                          evidence={code: evidence[code] for code in keep if code in evidence},
                          created=now())
    session.add(basis)
    session.commit()
    audit(session, user, f'起草触达方案 #{outreach.id}（复核 #{review.id}，保留 {len(keep)} 位，剔除 {len(excluded)} 位）')
    return outreach, basis


def approve(session, user, outreach_id):
    outreach = session.query(Outreach).filter_by(id=outreach_id).with_for_update().first()
    if not outreach:
        raise LookupError('触达方案不存在')
    if outreach.status != 'draft':
        raise Conflict('已签核方案不可重复签核，也不再改动')
    basis = session.query(OutreachBasis).filter_by(outreach_id=outreach.id).with_for_update().first()
    if not basis:
        raise Conflict('草案缺少复核依据，请重新起草')
    review = session.query(Review).filter_by(id=basis.review_id).with_for_update().first()
    # 使用同一条复核记录重新校验：草案时绑定摘要必须仍与当前复核一致
    if not review or review.state != 'done' or not verify_digest(review.data):
        raise Conflict('复核结论已变化或校验失败，请基于最新复核重新起草')
    if state_digest(review.data) != basis.review_digest:
        raise Conflict('复核结论与草案绑定的依据不一致，请重新起草')
    stable_codes = {m['code'] for m in review.data.get('members', []) if m.get('stable')}
    if any(code not in stable_codes for code in outreach.members):
        raise Conflict('草案成员与复核结论不一致，请重新起草')
    basis.approved_at = now()
    basis.approved_digest = approved_digest(review, basis.review_digest)
    outreach.status = 'approved'
    session.commit()
    audit(session, user, f'签核触达方案 #{outreach.id}（冻结复核 #{review.id} 依据）')
    return outreach, basis


def outreach_payload(outreach, basis=None):
    out = {c.name: getattr(outreach, c.name) for c in outreach.__table__.columns}
    if basis is None:
        basis = getattr(outreach, '_basis', None)
    if basis:
        out['basis'] = {c.name: getattr(basis, c.name) for c in basis.__table__.columns}
    return out
