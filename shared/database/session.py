from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import Session, sessionmaker

from shared.database.models import Base


def make_engine(url: str):
    if url in ("sqlite://", "sqlite:///:memory:"):  # shared in-memory DB across sessions (tests)
        engine = create_engine(url, future=True, poolclass=StaticPool,
                               connect_args={"check_same_thread": False})
    else:
        engine = create_engine(url, future=True)
    Base.metadata.create_all(engine)
    return engine


def make_session_factory(url: str) -> sessionmaker[Session]:
    return sessionmaker(make_engine(url), expire_on_commit=False)
