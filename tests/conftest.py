from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_bytes():
    """Legge una fixture registrata dalla fotocamera reale (Task 0)."""
    def _read(name: str) -> bytes:
        path = FIXTURES / name
        if not path.exists():
            pytest.skip(f"fixture {name} assente: eseguire diag.py con la fotocamera")
        return path.read_bytes()
    return _read
