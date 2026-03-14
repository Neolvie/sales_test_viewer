import os
from sqlalchemy import create_engine, Column, Integer, String, Text, Boolean, SmallInteger, DateTime, ForeignKey
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from sqlalchemy.sql import func

Base = declarative_base()


def get_engine():
    return create_engine(
        f"postgresql://{os.getenv('DB_USER', 'salestester')}:{os.getenv('DB_PASSWORD', 'changeme')}"
        f"@{os.getenv('DB_HOST', 'localhost')}:{os.getenv('DB_PORT', '5432')}/{os.getenv('DB_NAME', 'salestester')}",
        pool_pre_ping=True,
    )


engine = get_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Theme(Base):
    __tablename__ = "themes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(500), nullable=False)
    reference_text = Column(Text, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Prompt(Base):
    __tablename__ = "prompts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    version = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    is_active = Column(Boolean, nullable=False, default=False)
    is_draft = Column(Boolean, nullable=False, default=False)
    notes = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Session(Base):
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    answered_at = Column(DateTime(timezone=True))
    state = Column(SmallInteger, nullable=False, default=0)  # 0=awaiting, 1=completed, 2=timeout
    theme_id = Column(Integer, ForeignKey("themes.id"), nullable=False)
    full_name = Column(String(500), nullable=False)
    user_answer = Column(Text)
    result = Column(Text)
    prompt_id = Column(Integer, ForeignKey("prompts.id"))
    score = Column(SmallInteger)

    theme = relationship("Theme", backref="sessions")
    prompt = relationship("Prompt", backref="sessions")


class PromptTest(Base):
    __tablename__ = "prompt_tests"

    id = Column(Integer, primary_key=True, autoincrement=True)
    prompt_id = Column(Integer, ForeignKey("prompts.id"), nullable=False)
    session_id = Column(Integer, ForeignKey("sessions.id"), nullable=False)
    result = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    prompt = relationship("Prompt", backref="prompt_tests")
    session = relationship("Session", backref="prompt_tests")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
