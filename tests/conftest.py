import os
import sys
from pathlib import Path
from typing import Callable

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Ensure repository root is on sys.path for `import wos_redeem`
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def db_sessionmaker(tmp_path) -> Callable[[], object]:
    """Provide an isolated SQLite DB bound to wos_redeem.db for tests.

    Returns a callable that creates sessions (like SessionLocal).
    """
    # Late import so sys.path injection above is in effect
    from wos_redeem import db as _db

    db_path = tmp_path / "test.db"
    url = f"sqlite:///{db_path}"

    # Swap engine/sessionmaker for the duration of the test
    old_engine = _db.engine
    old_sessionlocal = _db.SessionLocal

    engine = create_engine(url, future=True, echo=False)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    _db.engine = engine
    _db.SessionLocal = SessionLocal
    _db.Base.metadata.create_all(bind=engine)

    try:
        yield SessionLocal
    finally:
        try:
            _db.Base.metadata.drop_all(bind=engine)
        except Exception:
            pass
        _db.engine = old_engine
        _db.SessionLocal = old_sessionlocal
