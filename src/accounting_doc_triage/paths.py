from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import yaml


@dataclass(frozen=True)
class Paths:
    data_root: Path
    inbox: Path
    moved: Path
    analysis: Path
    artifacts: Path


def load_paths(config_path: str | Path) -> Paths:
    p = Path(config_path).expanduser().resolve()
    d = yaml.safe_load(p.read_text())

    data_root = Path(os.path.expanduser(d["data_root"])).resolve()
    inbox = data_root / d.get("inbox_rel", "1_Input_Raw/00_inbox")
    moved = data_root / d.get("moved_rel", "moved")
    analysis = data_root / d.get("analysis_rel", "4_Analysis_Workflows")
    artifacts = data_root / d.get("artifacts_rel", "artifacts")

    return Paths(
        data_root=data_root,
        inbox=inbox,
        moved=moved,
        analysis=analysis,
        artifacts=artifacts,
    )
