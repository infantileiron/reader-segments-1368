import os,json
import redis
from fastapi import APIRouter,Depends,HTTPException,Response
from sqlalchemy.exc import IntegrityError
from src.config.database import db
from src.models.entities import User,Reader,Segment,Outreach,OutreachBasis,Review,Audit
from src.schemas.business import Login,ReaderInput,SegmentInput,ReviewInput,OutreachInput
from src.middlewares.auth import current,editor,admin
from src.utils.security import verify,token
from src.services.business import audit,dump
from src.services import review as review_service
from src.controllers.business import ensure_segment
router=APIRouter()
def enqueue(key,item_id):
 try:redis.Redis.from_url(os.environ['REDIS_URL']).rpush(key,item_id)
 except redis.RedisError:raise HTTPException(503,'队列暂不可用，请稍后重试')
@router.post('/auth/login')
def login(data:Login,s=Depends(db)):
 u=s.query(User).filter_by(email=data.email).first()
 if not u or not verify(data.password,u.password):raise HTTPException(401,'账号或密码错误')
 return {'token':token(u),'user':dump(u)}
@router.get('/me')
def me(u=Depends(current)):return dump(u)
@router.get('/readers')
def readers(u=Depends(current),s=Depends(db)):return [dump(r) for r in s.query(Reader).order_by(Reader.id).all()]
@router.post('/readers')
def create(data:ReaderInput,u=Depends(editor),s=Depends(db)):
 r=Reader(**data.model_dump());s.add(r)
 try:s.commit()
 except IntegrityError:s.rollback();raise HTTPException(409,'会员编号重复')
 audit(s,u,'录入阅读档案 '+r.code);return dump(r)
@router.put('/readers/{i}')
def edit(i:int,data:ReaderInput,u=Depends(editor),s=Depends(db)):
 r=s.get(Reader,i)
 if not r:raise HTTPException(404,'会员档案不存在')
 for k,v in data.model_dump().items():setattr(r,k,v)
 try:s.commit()
 except IntegrityError:s.rollback();raise HTTPException(409,'会员编号重复')
 audit(s,u,'修订档案 '+str(i));return dump(r)
@router.get('/segments')
def segments(u=Depends(current),s=Depends(db)):return [dump(r) for r in s.query(Segment).order_by(Segment.id.desc()).all()]
@router.post('/segments')
def train(data:SegmentInput,u=Depends(editor),s=Depends(db)):
 rows=[dump(r) for r in s.query(Reader).order_by(Reader.id).all()];r=Segment(**data.model_dump(),result={'snapshot':rows});s.add(r);s.commit()
 try:redis.Redis.from_url(os.environ['REDIS_URL']).rpush('reader:segments',r.id)
 except redis.RedisError:r.state='failed';r.error='队列暂不可用，请稍后重试';s.commit();raise HTTPException(503,r.error)
 audit(s,u,'提交分群 '+str(r.id));return dump(r)
@router.get('/segments/{i}')
def detail(i:int,u=Depends(current),s=Depends(db)):return dump(ensure_segment(s,i))
@router.get('/segments/{i}/export')
def export(i:int,u=Depends(current),s=Depends(db)):
 r=ensure_segment(s,i)
 if r.state!='done':raise HTTPException(409,'仅成功分群可导出')
 return Response(json.dumps(dump(r),ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':f'attachment; filename="reader-segments-{i}.json"'})
@router.get('/reviews')
def reviews(segment_id:int|None=None,u=Depends(current),s=Depends(db)):
 q=s.query(Review)
 if segment_id is not None:q=q.filter(Review.segment_id==segment_id)
 return [review_service.review_payload(r,full=False) for r in q.order_by(Review.id.desc()).all()]
@router.post('/reviews')
def create_review(data:ReviewInput,u=Depends(editor),s=Depends(db)):
 try:r=review_service.create_review(s,u,data.segment_id,data.rounds,data.threshold)
 except review_service.Conflict as e:raise HTTPException(409,str(e))
 except review_service.Invalid as e:raise HTTPException(422,str(e))
 try:enqueue('reader:reviews',r.id)
 except HTTPException:
  r.state='failed';r.error='队列暂不可用，复核未开始计算，请稍后重新提交';s.commit()
  raise
 return review_service.review_payload(r)
@router.get('/reviews/{i}')
def review_detail(i:int,u=Depends(current),s=Depends(db)):
 r=s.get(Review,i)
 if not r:raise HTTPException(404,'复核记录不存在')
 return review_service.review_payload(r)
@router.get('/reviews/{i}/export')
def export_review(i:int,u=Depends(current),s=Depends(db)):
 r=s.get(Review,i)
 if not r:raise HTTPException(404,'复核记录不存在')
 if r.state!='done':raise HTTPException(409,'仅完成状态可导出复核依据')
 body=json.dumps(review_service.review_payload(r),ensure_ascii=False,indent=2)
 return Response(body,media_type='application/json',headers={'Content-Disposition':f'attachment; filename="reader-review-{i}.json"'})
@router.get('/outreaches')
def outreaches(u=Depends(current),s=Depends(db)):
 rows=s.query(Outreach).order_by(Outreach.id.desc()).all()
 bases={b.outreach_id:b for b in s.query(OutreachBasis).filter(OutreachBasis.outreach_id.in_([r.id for r in rows])).all()} if rows else {}
 return [review_service.outreach_payload(r,bases.get(r.id)) for r in rows]
@router.post('/outreaches')
def outreach(data:OutreachInput,u=Depends(editor),s=Depends(db)):
 try:o,basis=review_service.draft_outreach(s,u,data)
 except review_service.Conflict as e:raise HTTPException(409,str(e))
 except review_service.Invalid as e:raise HTTPException(422,str(e))
 return review_service.outreach_payload(o,basis)
@router.post('/outreaches/{i}/approve')
def approve(i:int,u=Depends(admin),s=Depends(db)):
 try:o,basis=review_service.approve(s,u,i)
 except LookupError as e:raise HTTPException(404,str(e))
 except review_service.Conflict as e:raise HTTPException(409,str(e))
 return review_service.outreach_payload(o,basis)
@router.get('/outreaches/{i}/export')
def export_outreach(i:int,u=Depends(current),s=Depends(db)):
 r=s.get(Outreach,i)
 if not r:raise HTTPException(404,'触达方案不存在')
 basis=s.query(OutreachBasis).filter_by(outreach_id=r.id).first()
 payload=review_service.outreach_payload(r,basis)
 if basis:
  payload['basis_detail']={'review_id':basis.review_id,'review_digest':basis.review_digest,'approved_at':basis.approved_at,'approved_digest':basis.approved_digest,'excluded':basis.excluded,'evidence':basis.evidence}
 return Response(json.dumps(payload,ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':f'attachment; filename="reader-outreach-{i}.json"'})
@router.get('/audits')
def audits(u=Depends(admin),s=Depends(db)):return [dump(r) for r in s.query(Audit).order_by(Audit.id.desc()).limit(100)]
