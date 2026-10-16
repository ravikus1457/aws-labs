import copy
import sys
from pathlib import Path

import pytest

LAB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAB))
sys.path.insert(0, str(LAB / "tests"))

from netaac import generate, intent  # noqa: E402
from netaac.model import Net  # noqa: E402

_BASE = intent.load(LAB / "intent" / "network.yaml")


@pytest.fixture
def lab_dir() -> Path:
    return LAB


@pytest.fixture
def data() -> dict:
    """A fresh, mutable copy of the real source of truth."""
    return copy.deepcopy(_BASE)


@pytest.fixture
def net(data) -> Net:
    return Net(data)


@pytest.fixture
def files(data) -> dict:
    return generate.render(data)


@pytest.fixture
def build(tmp_path, files) -> Path:
    out = tmp_path / "build"
    generate.write(files, out)
    return out
