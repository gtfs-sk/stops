"""
Smoke test: run every exporter on an empty database and validate the
output with scripts/validate_exports.py.

Catches broken imports, exporter wiring and schema drift on every push
without downloading any source data (the full build needs the OSM PBF,
the GKÚ boundaries and the ISCP registry, so it only runs in the
release workflow).

    python3 scripts/smoke_test.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from build import EXPORTERS
from models import Base
from pipeline.export import run_exporters

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        export_dir = Path(tmp) / "export"
        engine = create_engine(f"sqlite:///{Path(tmp) / 'stops.sqlite'}")
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.commit()
            run_exporters(session, EXPORTERS, export_dir)

        result = subprocess.run(
            [
                sys.executable,
                str(REPO / "scripts" / "validate_exports.py"),
                str(export_dir),
                "--min-stops",
                "0",
            ],
            check=False,
        )
        if result.returncode != 0:
            return result.returncode

    print("smoke_test: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
