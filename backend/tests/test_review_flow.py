import json, os, sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('JWT_SECRET', 'test-secret')

from src.config import database
from src.models import entities
from src.models.entities import Base, Review, Segment, Outreach, OutreachBasis, User
from src.services import review as review_service
from src.services.analysis import cluster
from src.schemas.business import OutreachInput
from src.services.review_run import ReviewInterrupted, run_review_job
from src.services.stability import state_digest, verify_digest
from tests.test_stability import make_rows


@pytest.fixture()
def session():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    s = factory()
    yield s
    s.close()
    engine.dispose()


@pytest.fixture()
def user_and_segment(session):
    user = User(email='analyst@demo.local', role='analyst', password='x')
    session.add(user)
    rows = make_rows()
    result = cluster(rows, ['weekly_reading', 'completion', 'night_share'], 3)
    seg = Segment(name='测试版本', k=3, features=result['features'], state='done',
                  result={**result, 'snapshot': rows}, error='')
    session.add(seg)
    session.commit()
    return user, seg


def run_ready_review(session, user, seg, rounds=4, threshold=0.6):
    review = review_service.create_review(session, user, seg.id, rounds, threshold)
    run_review_job(session, review.id)
    session.refresh(review)
    assert review.state == 'done', review.error
    return review


def test_review_snapshot_locked_against_reader_edits(session, user_and_segment, tmp_path):
    user, seg = user_and_segment
    original_codes = {r['code'] for r in seg.result['snapshot']}
    review = review_service.create_review(session, user, seg.id, 3, 0.6)
    # 创建后修改版本快照与 live readers，不影响复核的冻结副本
    seg.result = {**seg.result, 'snapshot': [dict(r, weekly_reading=999) for r in seg.result['snapshot']]}
    session.commit()
    run_review_job(session, review.id)
    session.refresh(review)
    assert review.state == 'done'
    assert {m['code'] for m in review.data['members']} == original_codes
    assert {r['code'] for r in review.snapshot} == original_codes


def test_resume_does_not_double_count(session, user_and_segment):
    user, seg = user_and_segment
    review = review_service.create_review(session, user, seg.id, 5, 0.6)
    with pytest.raises(ReviewInterrupted):
        run_review_job(session, review.id, max_new_rounds=2)
    session.refresh(review)
    assert review.state == 'running'
    assert len(review.data['samples']) == 2
    assert review.data['completed_rounds'] == 2
    first_stats = json.loads(json.dumps(review.data['stats']))

    run_review_job(session, review.id)  # 续跑剩余 3 轮
    session.refresh(review)
    assert review.state == 'done'
    assert review.data['completed_rounds'] == 5
    assert [s['round'] for s in review.data['samples']] == [1, 2, 3, 4, 5]
    assert len(review.data['stats']) == len(first_stats)
    # 中断前统计已提交，续跑后不会重复累计前两轮
    for code, st in review.data['stats'].items():
        assert st['rounds_sampled'] >= first_stats[code]['rounds_sampled']
    total_sampled = sum(st['rounds_sampled'] for st in review.data['stats'].values())
    expected = sum(s['size'] for s in review.data['samples'] if s['status'] == 'ok')
    assert total_sampled == expected
    assert verify_digest(review.data)


def test_snapshot_stability_is_independent_of_group_number(session, user_and_segment):
    user, seg = user_and_segment
    a = run_ready_review(session, user, seg, 4, 0.5)
    stable_a = {m['code']: m['stable'] for m in a.data['members']}
    # 用置换后的群号再建一个版本，复核结论应一致
    mapping = {0: 2, 1: 0, 2: 1}
    permuted_members = [dict(m, cluster_id=mapping[int(m['cluster_id'])]) for m in seg.result['members']]
    seg2 = Segment(name='置换版本', k=3, features=seg.features, state='done',
                   result={**seg.result, 'members': permuted_members}, error='')
    session.add(seg2)
    session.commit()
    b = run_ready_review(session, user, seg2, 4, 0.5)
    assert {m['code']: m['stable'] for m in b.data['members']} == stable_a
    assert [s['codes'] for s in a.data['samples']] == [s['codes'] for s in b.data['samples']]


def test_outreach_drops_unstable_members_and_records_reasons(session, user_and_segment):
    user, seg = user_and_segment
    review = run_ready_review(session, user, seg, 4, 0.9)
    evidence = {m['code']: m for m in review.data['members']}
    cluster_members = [m['code'] for m in seg.result['members'] if m['cluster_id'] == 0]
    draft = OutreachInput(segment_id=seg.id, cluster_id=0, review_id=review.id,
                                         title='低频读者轻阅读试点', note='每周一次短篇精选，站内触达')
    outreach, basis = review_service.draft_outreach(session, user, draft)
    expected_keep = [c for c in cluster_members if evidence[c]['stable']]
    expected_drop = [c for c in cluster_members if not evidence[c]['stable']]
    assert outreach.members == expected_keep
    assert {e['code'] for e in basis.excluded} == set(expected_drop)
    for e in basis.excluded:
        assert e['reason']
    assert set(basis.evidence) == set(expected_keep)
    assert basis.review_digest == state_digest(review.data)


def test_draft_rejects_unfinished_or_foreign_review(session, user_and_segment):
    user, seg = user_and_segment
    pending = review_service.create_review(session, user, seg.id, 3, 0.6)
    draft = OutreachInput(segment_id=seg.id, cluster_id=0, review_id=pending.id,
                                         title='未完成复核', note='不应允许基于未完成复核起草')
    with pytest.raises(review_service.Conflict):
        review_service.draft_outreach(session, user, draft)

    other = Segment(name='其他版本', k=3, features=seg.features, state='done', result=seg.result, error='')
    session.add(other)
    session.commit()
    done = run_ready_review(session, user, other, 3, 0.6)
    draft2 = OutreachInput(segment_id=seg.id, cluster_id=0, review_id=done.id,
                                          title='跨版本复核', note='复核不属于该分群版本应拒绝')
    with pytest.raises(review_service.Invalid):
        review_service.draft_outreach(session, user, draft2)


def test_approve_freezes_basis_and_blocks_changes(session, user_and_segment):
    user, seg = user_and_segment
    review = run_ready_review(session, user, seg, 4, 0.6)
    draft = OutreachInput(segment_id=seg.id, cluster_id=0, review_id=review.id,
                                         title='签核冻结测试', note='签核应冻结复核依据并禁止再改')
    outreach, basis = review_service.draft_outreach(session, user, draft)
    admin_user = User(email='admin@demo.local', role='admin', password='x')
    session.add(admin_user)
    session.commit()
    approved, approved_basis = review_service.approve(session, admin_user, outreach.id)
    assert approved.status == 'approved'
    assert approved_basis.approved_digest and approved_basis.approved_at
    frozen = json.dumps(approved_basis.members)
    # 再次签核或尝试重写成员都应拒绝
    with pytest.raises(review_service.Conflict):
        review_service.approve(session, admin_user, outreach.id)
    assert json.dumps(approved_basis.members) == frozen
    payload = review_service.outreach_payload(approved, approved_basis)
    assert payload['basis']['review_id'] == review.id
    assert payload['basis']['review_digest'] == state_digest(review.data)


def test_review_tampering_blocks_approve(session, user_and_segment):
    user, seg = user_and_segment
    review = run_ready_review(session, user, seg, 3, 0.5)
    draft = OutreachInput(segment_id=seg.id, cluster_id=0, review_id=review.id,
                                         title='摘要校验', note='篡改复核依据后不能签核通过')
    outreach, basis = review_service.draft_outreach(session, user, draft)
    tampered = json.loads(json.dumps(review.data))
    tampered['members'][0]['stable'] = not tampered['members'][0]['stable']
    review.data = tampered
    session.commit()
    with pytest.raises(review_service.Conflict):
        review_service.approve(session, user, outreach.id)


def test_new_review_after_draft_does_not_mix_conclusions(session, user_and_segment):
    """重算发布与签核并发：草案绑定的结论与签核读取的结论必须是同一条。"""
    user, seg = user_and_segment
    first = run_ready_review(session, user, seg, 3, 0.5)
    draft = OutreachInput(segment_id=seg.id, cluster_id=0, review_id=first.id,
                                         title='并发结论测试', note='新的复核完成后签核仍用原绑定结论')
    outreach, basis = review_service.draft_outreach(session, user, draft)
    bound_codes = list(outreach.members)
    second = run_ready_review(session, user, seg, 5, 0.95)
    assert second.id != first.id
    approved, approved_basis = review_service.approve(session, user, outreach.id)
    assert approved_basis.review_id == first.id
    assert approved_basis.review_digest == state_digest(first.data)
    assert approved.members == bound_codes
    # 第二次结论独立存在且未被篡改
    session.refresh(second)
    assert second.state == 'done' and second.data['digest']


def test_create_review_rejects_snapshot_tampering(session, user_and_segment):
    user, seg = user_and_segment
    seg.result = {**seg.result, 'snapshot': [dict(r, weekly_reading=r['weekly_reading'] + 1) for r in seg.result['snapshot']]}
    session.commit()
    with pytest.raises(review_service.Conflict):
        review_service.create_review(session, user, seg.id, 3, 0.6)


def test_all_rounds_failed_marks_review_failed_with_reasons(session, user_and_segment, monkeypatch):
    user, seg = user_and_segment
    review = review_service.create_review(session, user, seg.id, 3, 0.6)

    def broken(rows_, features_, k_):
        raise ValueError('重算样本常量列')

    run_review_job(session, review.id, cluster_fn=broken)
    session.refresh(review)
    assert review.state == 'failed'
    assert '重算样本常量列' in review.error
    assert len(review.data['samples']) == 3
    assert all(s['status'] == 'failed' and s['error'] for s in review.data['samples'])
    assert review.data.get('members') in ([], None)
    assert review.data['summary'] == {}


def test_resume_after_partial_failure_keeps_failed_rounds(session, user_and_segment):
    user, seg = user_and_segment
    review = review_service.create_review(session, user, seg.id, 4, 0.6)
    calls = {'n': 0}

    def flaky(rows_, features_, k_):
        calls['n'] += 1
        if calls['n'] == 2:
            raise ValueError('第2轮临时失败')
        return cluster(rows_, features_, k_)

    run_review_job(session, review.id, cluster_fn=flaky)
    session.refresh(review)
    assert review.state == 'done'
    statuses = [s['status'] for s in review.data['samples']]
    assert statuses.count('failed') == 1
    assert review.data['summary']['failed_rounds'] == 1
    assert review.data['summary']['completed_rounds'] == 3
    failed = [s for s in review.data['samples'] if s['status'] == 'failed'][0]
    assert '第2轮临时失败' in failed['error'] and failed['round'] == 2
    # 失败轮次不计为稳定：所有成员仍按成功轮次证据判定
    assert all(m['stable'] or '共同入样不足' in m['reason'] or '低于门槛' in m['reason'] for m in review.data['members'])
