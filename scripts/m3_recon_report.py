r"""M3 v2 -- INFORME OFFLINE de la sesion final: lee `m3_recon_v2.json` (y, si existen, los reconocimientos v1 como llegada previa) y escribe la matriz de
evidencias y los CANDIDATOS de contrato. NUNCA habilita nada y no abre navegador ni red.

    .\.venv-win-backend\Scripts\python.exe scripts\m3_recon_report.py "$env:USERPROFILE\m3-recon-v2" [--prior "$env:USERPROFILE\m3-recon-principal"]"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m3_recon_core as core  # noqa: E402
import m3_sol_recon as rec  # noqa: E402


def build(out_dir: Path, prior_dirs: list[Path]) -> dict:
    store = core.Store(out_dir / "m3_recon_v2.json")
    prior: dict = {}
    for d in prior_dirs:
        f = Path(d) / "m2_sol_recon.json"
        if f.exists():
            prior.update(core.prior_arrivals_from_v1(json.loads(f.read_text(encoding="utf-8"))))
    mx = core.matrix(store, prior)
    return {"matrix": mx, "candidates": core.candidate_contracts(mx), "checkpoints": len(store.checkpoints),
            "enabled_by_this_tool": [], "note": "Ningun destino se habilita aqui: un humano revisa los candidatos, escribe el contrato y las pruebas positivas/negativas."}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", help="carpeta con m3_recon_v2.json")
    ap.add_argument("--prior", action="append", default=[])
    args = ap.parse_args()
    report = build(Path(args.out), [Path(p) for p in args.prior])
    print(rec.render_matrix(report["matrix"]))
    print(json.dumps({"candidates": report["candidates"], "enabled_by_this_tool": []}, ensure_ascii=False, indent=1))
    (Path(args.out) / "m3_recon_matrix.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
