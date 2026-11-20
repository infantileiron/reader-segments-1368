from sqlalchemy import Column,Integer,String,Float,JSON,Text,ForeignKey
from src.config.database import Base
class User(Base):
 __tablename__='users'
 id=Column(Integer,primary_key=True);email=Column(String(100),unique=True);password=Column(String(200));role=Column(String(20))
class Reader(Base):
 __tablename__='readers'
 id=Column(Integer,primary_key=True);code=Column(String(60),unique=True,nullable=False);weekly_reading=Column(Float);completion=Column(Float);night_share=Column(Float);note=Column(Text,default='')
class Segment(Base):
 __tablename__='segments'
 id=Column(Integer,primary_key=True);name=Column(String(100));k=Column(Integer);features=Column(JSON);state=Column(String(20),default='queued');result=Column(JSON);error=Column(Text,default='')
class Review(Base):
 __tablename__='reviews'
 id=Column(Integer,primary_key=True);segment_id=Column(Integer,ForeignKey('segments.id'),nullable=False);state=Column(String(20),default='pending');rounds=Column(Integer);threshold=Column(Float);features=Column(JSON);k=Column(Integer);snapshot=Column(JSON);checksum=Column(String(64));data=Column(JSON);error=Column(Text,default='');created=Column(String(40));updated=Column(String(40))
class Outreach(Base):
 __tablename__='outreaches'
 id=Column(Integer,primary_key=True);segment_id=Column(Integer,ForeignKey('segments.id'));cluster_id=Column(Integer);title=Column(String(100));note=Column(Text);members=Column(JSON);status=Column(String(20),default='draft')
class OutreachBasis(Base):
 __tablename__='outreach_bases'
 id=Column(Integer,primary_key=True);outreach_id=Column(Integer,ForeignKey('outreaches.id'),unique=True,nullable=False);review_id=Column(Integer,ForeignKey('reviews.id'),nullable=False);review_digest=Column(String(64));members=Column(JSON);excluded=Column(JSON);evidence=Column(JSON);created=Column(String(40));approved_at=Column(String(40));approved_digest=Column(String(64))
class Audit(Base):
 __tablename__='audits'
 id=Column(Integer,primary_key=True);actor=Column(String(100));action=Column(String(200));created=Column(String(40))
