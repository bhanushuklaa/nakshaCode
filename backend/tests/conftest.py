import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def zip_fixture(tmp_path):
    """Return a function that zips a fixture project and returns the zip path."""
    def make(name: str) -> Path:
        return Path(shutil.make_archive(str(tmp_path / name), "zip", FIXTURES, name))
    return make
