import os
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("edition", ["morning", "evening"])
@pytest.mark.parametrize("marker,expected", [
    (None, "false"),
    ('{"skip_final_report": false}', "false"),
    ('{"skip_final_report": true}', "true"),
    ("invalid json", "true"),
])
def test_workflow_skip_output(edition, marker, expected, tmp_path):
    workflow = yaml.safe_load((ROOT / f".github/workflows/nikkei_{edition}.yml").read_text())
    step = next(s for s in workflow["jobs"][next(iter(workflow["jobs"]))]["steps"] if s.get("id") == "fr_skip")
    if marker is not None:
        (tmp_path / "logs").mkdir()
        (tmp_path / "logs/nikkei_paper_pipeline_skip.json").write_text(marker)
    output = tmp_path / "output"
    subprocess.run(["bash", "-e", "-c", step["run"]], cwd=tmp_path,
                   env={**os.environ, "GITHUB_OUTPUT": str(output)}, check=True)
    assert output.read_text() == f"skip={expected}\n"
    assert not (tmp_path / "${GITHUB_OUTPUT}").exists()
