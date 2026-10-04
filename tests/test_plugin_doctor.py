"""Run the exact offline cases used by the plugin's standalone selftest."""
import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "plugins/swarm-doctor/scripts/selftest.py"
_SPEC = importlib.util.spec_from_file_location("swarm_doctor_selftest", _PATH)
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
DoctorTests = _MODULE.DoctorTests
