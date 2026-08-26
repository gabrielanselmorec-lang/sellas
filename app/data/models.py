from sqlalchemy import create_engine, Column, Integer, String, Float, ForeignKey, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker
import uuid
from dotenv import load_dotenv

from app.security import require_env

Base = declarative_base()

class Patient(Base):
    __tablename__ = 'patients'
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name_hash = Column(String, unique=True, index=True) # ⚡ ÍNDICE AQUI
    name = Column(String, nullable=True)
    created_at = Column(String)

class Program(Base):
    __tablename__ = 'programs'
    id = Column(Integer, primary_key=True, autoincrement=True)
    patient_id = Column(String, ForeignKey('patients.id'), index=True) # ⚡ ÍNDICE AQUI
    name = Column(String)
    objective = Column(String, nullable=True)
    records = relationship("ProgramRecord", back_populates="program")

class ProgramRecord(Base):
    __tablename__ = 'program_records'
    id = Column(Integer, primary_key=True, autoincrement=True)
    program_id = Column(Integer, ForeignKey('programs.id'), index=True)
    date = Column(String, index=True)
    therapist = Column(String)
    phase = Column(String)
    success_rate = Column(Float)
    independent_rate = Column(Float)
    prompt_rate = Column(Float)
    evolution = Column(Text, nullable=True)
    program = relationship("Program", back_populates="records")

class ProgramTargetRecord(Base):
    __tablename__ = 'program_target_records'
    id = Column(Integer, primary_key=True, autoincrement=True)
    program_id = Column(Integer, ForeignKey('programs.id'), index=True)
    target_name = Column(String)
    attempts = Column(Integer)
    independent_rate = Column(Float)
    prompt_rate = Column(Float)
    success_rate = Column(Float)
    date = Column(String, index=True)
    prompt_type = Column(String, nullable=True)
    evolution = Column(Text, nullable=True)
    program = relationship("Program")

class InterferingBehavior(Base):
    __tablename__ = 'interfering_behaviors'
    id = Column(Integer, primary_key=True, autoincrement=True)
    patient_id = Column(String, ForeignKey('patients.id'), index=True)
    name = Column(String)
    records = relationship("InterferingRecord", back_populates="behavior")

class InterferingRecord(Base):
    __tablename__ = 'interfering_records'
    id = Column(Integer, primary_key=True, autoincrement=True)
    behavior_id = Column(Integer, ForeignKey('interfering_behaviors.id'), index=True)
    date = Column(String, index=True)
    therapist = Column(String)
    count = Column(Integer)
    rate = Column(Float)
    evolution = Column(Text, nullable=True)
    behavior = relationship("InterferingBehavior", back_populates="records")

# 🔥 CORREÇÃO: Tabela que o Codex esqueceu, mas usou no main.py e api.py
class ProgramLibrary(Base):
    __tablename__ = 'program_library'
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, unique=True, index=True)
    objective_template = Column(String, nullable=True)
    mastery_threshold_percent = Column(Float, default=90.0)
    mastery_days = Column(Integer, default=3)
    suggested_targets = Column(String, nullable=True)

# 1. Carrega as variáveis do arquivo .env
load_dotenv()

# 2. Pega a URL do banco de dados direto do ambiente
DATABASE_URL = require_env("SELLAS_DATABASE_URL", "DATABASE_URL")

# 3. Cria a conexão blindada
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=300
)
SessionLocal = sessionmaker(bind=engine)

def init_db():
    Base.metadata.create_all(engine)
