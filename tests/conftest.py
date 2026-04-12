import pytest
import tempfile
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base, get_db


@pytest.fixture
def db_engine():
    """创建临时数据库引擎"""
    db_fd, db_path = tempfile.mkstemp(suffix='.db')
    engine = create_engine(f'sqlite:///{db_path}')
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()
    os.close(db_fd)
    os.unlink(db_path)


@pytest.fixture
def db_session(db_engine):
    """创建数据库会话"""
    Session = sessionmaker(bind=db_engine)
    session = Session()
    yield session
    session.close()
