"""
Experiment 14: Q-HEAT facade-material QPU benchmark.

This experiment proposes a new facade-material hypothesis and reduces one
electronic subproblem to a four-qubit Hamiltonian:

    Q-HEAT = Quantum-screened High-Entropy Aerogel Thermal facade coating.

The candidate material concept is a non-combustible, porous high-entropy
ceramic/aerogel insulation matrix with sparse correlated dopant-pair centers
that may tune mid-IR emissivity and solar-thermal response.

Claim boundary:
    This is not a synthesized material and not proof of a superior facade
    coating. It is a QPU-ready reduced active-space benchmark used to rank
    candidate dopant-pair motifs under exact classical validation.
"""
from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
CODE_DIR = Path(__file__).resolve().parent
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

try:
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "buffer"):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

from shared.plotting.style import COLORS, apply_research_style, save_figure
from qheat_facade_circuits import (
    SCENARIOS,
    build_ansatz,
    circuit_stats,
    dopant_pair_hamiltonian,
    evaluate_observables,
    exact_ground_state,
    expectation_from_counts,
    optimize_vqe,
    pauli_measurement_circuit,
    top_hamiltonian_terms,
)


RESULTS_DIR = Path(__file__).resolve().parents[1] / "publication"
RESOURCE_GROUP = "AzureQuantum"
WORKSPACE_NAME = "caribbeanazure"
LOCATION = "westeurope"


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, tuple):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return _jsonable(obj.tolist())
    if isinstance(obj, (np.floating, np.integer)):
        return obj.item()
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj


def list_azure_targets() -> dict:
    """Return current Azure Quantum targets for the configured workspace."""
    commands = [
        [
            "az", "quantum", "target", "list",
            "--resource-group", RESOURCE_GROUP,
            "--workspace-name", WORKSPACE_NAME,
            "-o", "json",
        ],
        [
            "az", "quantum", "target", "list",
            "--resource-group", RESOURCE_GROUP,
            "--workspace-name", WORKSPACE_NAME,
            "--location", LOCATION,
            "-o", "json",
        ],
    ]
    for cmd in commands:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode == 0:
                raw = json.loads(result.stdout)
                targets = []
                for item in raw:
                    targets.append({
                        "provider": item.get("provider_id") or item.get("provider"),
                        "target_id": item.get("id") or item.get("target_id") or item.get("name"),
                        "availability": item.get("current_availability") or item.get("availability"),
                        "average_queue_time_seconds": item.get("average_queue_time_seconds"),
                    })
                return {
                    "succeeded": True,
                    "targets": targets,
                    "qpu_targets": [t for t in targets if ".qpu." in str(t.get("target_id", ""))],
                    "error": None,
                }
        except Exception as exc:
            last_error = str(exc)
    return {"succeeded": False, "targets": [], "qpu_targets": [], "error": last_error}


def run_local_screen(maxiter: int) -> dict:
    """Run exact and VQE screening for each Q-HEAT dopant-pair candidate."""
    rows = []
    for scenario in SCENARIOS:
        print(f"  Screening {scenario.label} ...")
        hamiltonian = dopant_pair_hamiltonian(
            t=scenario.t, U=scenario.U, delta=scenario.delta, mu=scenario.mu
        )
        exact = exact_ground_state(hamiltonian)
        exact_obs = evaluate_observables(exact["state"], exact_state=True)
        vqe = optimize_vqe(hamiltonian, maxiter=maxiter)
        vqe_obs = evaluate_observables(vqe["params"], reps=vqe["reps"], exact_state=False)
        ansatz = build_ansatz(np.asarray(vqe["params"]), reps=vqe["reps"], measure=False)

        row = {
            "name": scenario.name,
            "label": scenario.label,
            "design_hypothesis": scenario.design_hypothesis,
            "parameters": {
                "t": scenario.t,
                "U": scenario.U,
                "delta": scenario.delta,
                "mu": scenario.mu,
            },
            "exact_energy": round(exact["energy"], 10),
            "vqe_energy": round(vqe["energy"], 10),
            "vqe_abs_error": round(abs(vqe["energy"] - exact["energy"]), 10),
            "exact_observables": exact_obs,
            "vqe_observables": vqe_obs,
            "vqe": vqe,
            "hamiltonian_terms": [
                {"pauli": p, "coefficient": round(c, 10)}
                for p, c in zip(hamiltonian.paulis.to_labels(), np.real(hamiltonian.coeffs))
            ],
            "top_measurement_terms": [
                {"pauli": p, "coefficient": round(c, 10)}
                for p, c in top_hamiltonian_terms(hamiltonian, max_terms=8)
            ],
            "ansatz_stats": circuit_stats(ansatz),
        }
        rows.append(row)

    ranked = sorted(
        rows,
        key=lambda r: r["exact_observables"]["qheat_proxy_score"],
        reverse=True,
    )
    return {
        "rows": rows,
        "ranked_candidates": [
            {
                "rank": i + 1,
                "name": row["name"],
                "label": row["label"],
                "qheat_proxy_score": row["exact_observables"]["qheat_proxy_score"],
                "design_hypothesis": row["design_hypothesis"],
            }
            for i, row in enumerate(ranked)
        ],
    }


def run_real_qpu_measurement(
    best_row: dict,
    target: str,
    shots: int,
    max_terms: int,
    confirm_real_qpu_cost: bool,
) -> dict:
    """Submit selected Pauli measurement circuits to a real Azure QPU if enabled."""
    target_snapshot = list_azure_targets()
    available_ids = {str(t.get("target_id")) for t in target_snapshot.get("targets", [])}

    qpu_payload = {
        "requested_target": target,
        "shots": shots,
        "max_terms": max_terms,
        "target_snapshot": target_snapshot,
        "submitted": False,
        "succeeded": False,
        "reason": None,
        "jobs": [],
    }

    if ".qpu." not in target:
        qpu_payload["reason"] = "refused_non_qpu_target"
        return qpu_payload
    if target not in available_ids:
        qpu_payload["reason"] = "qpu_target_not_enabled_in_workspace"
        return qpu_payload
    if not confirm_real_qpu_cost:
        qpu_payload["reason"] = "real_qpu_cost_confirmation_missing"
        return qpu_payload

    from shared.azure_quantum_utils.azure_backend import run_circuit_on_azure

    params = np.asarray(best_row["vqe"]["params"], dtype=float)
    terms = best_row["top_measurement_terms"][:max_terms]
    qpu_payload["submitted"] = True
    energy_estimate = 0.0

    for term in terms:
        pauli = term["pauli"]
        coeff = float(term["coefficient"])
        qc = pauli_measurement_circuit(params, pauli, reps=best_row["vqe"]["reps"])
        result = run_circuit_on_azure(qc, target=target, shots=shots, timeout_s=7200)
        exp_val = expectation_from_counts(pauli, result.counts) if result.succeeded else None
        if exp_val is not None:
            energy_estimate += coeff * exp_val
        qpu_payload["jobs"].append({
            "pauli": pauli,
            "coefficient": coeff,
            "target": result.target,
            "job_id": result.job_id,
            "shots": result.shots,
            "succeeded": result.succeeded,
            "error": result.error,
            "counts": result.counts,
            "expectation": exp_val,
            "circuit_stats": circuit_stats(qc),
        })

    qpu_payload["partial_energy_estimate_top_terms"] = round(float(energy_estimate), 10)
    qpu_payload["succeeded"] = all(job["succeeded"] for job in qpu_payload["jobs"])
    if not qpu_payload["succeeded"]:
        qpu_payload["reason"] = "one_or_more_qpu_jobs_failed"
    return qpu_payload


def plot_results(screen: dict, out_dir: Path) -> None:
    import matplotlib.pyplot as plt

    apply_research_style()
    rows = screen["rows"]
    labels = [r["label"] for r in rows]
    scores = [r["exact_observables"]["qheat_proxy_score"] for r in rows]
    errors = [r["vqe_abs_error"] for r in rows]

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.bar(range(len(labels)), scores, color=COLORS["quantum"], edgecolor="white", zorder=3)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=20, ha="right")
    ax.set_ylabel("Q-HEAT proxy score")
    ax.set_title("Q-HEAT Candidate Dopant-Pair Ranking")
    fig.tight_layout()
    save_figure(fig, out_dir / "figures" / "qheat_candidate_scores.png")

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.semilogy(range(len(labels)), errors, "o-", color=COLORS["accent"], linewidth=2.2)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=20, ha="right")
    ax.set_ylabel("|E_VQE - E_exact|")
    ax.set_title("Local VQE Error Against Exact Reduced Hamiltonian")
    fig.tight_layout()
    save_figure(fig, out_dir / "figures" / "qheat_vqe_error.png")


def write_report(payload: dict, out_dir: Path) -> Path:
    best = payload["screen"]["ranked_candidates"][0]
    qpu = payload.get("azure_qpu", {})
    report = f"""# Experiment 14: Q-HEAT Facade Material QPU Benchmark

Generated: {payload['timestamp']} UTC

## Material Hypothesis

I propose **Q-HEAT**: a Quantum-screened High-Entropy Aerogel Thermal facade coating.

The candidate concept is a non-combustible high-entropy ceramic/aerogel insulation matrix with sparse correlated transition-metal dopant-pair centers. The matrix targets insulation, fire stability, and durability. The dopant-pair centers are intended to tune mid-IR emissivity and solar-thermal response.

This is a new material hypothesis, not a synthesized material.

## Claim Boundary

This experiment does not prove that Q-HEAT exists, outperforms known facade systems, or cannot be studied classically. The result is a reduced four-qubit electronic benchmark that can be run on a real QPU when a `.qpu.` Azure Quantum target is enabled.

The current Azure workspace target check found {len(qpu.get('target_snapshot', {}).get('qpu_targets', [])) if qpu else 0} real `.qpu.` targets. I therefore do not label any simulator or emulator result as QPU evidence.

## Main Result

Best reduced-model candidate:

- Candidate: `{best['label']}`
- Proxy score: `{best['qheat_proxy_score']}`
- Hypothesis: {best['design_hypothesis']}

This score ranks dopant-pair electronic motifs inside the reduced Hamiltonian only. It is not a facade-performance metric.

## Method

1. Define a new facade coating hypothesis: high-entropy ceramic aerogel plus sparse correlated dopant-pair centers.
2. Reduce the dopant-pair electronic subproblem to a four-qubit Hubbard-style Hamiltonian.
3. Evaluate four candidate dopant-pair motifs with exact diagonalization and local VQE.
4. Rank candidates by a bounded Q-HEAT proxy score based on bond order, charge localization, and double occupancy.
5. Check Azure Quantum target availability and refuse to call simulator/emulator targets real QPU evidence.
6. If a `.qpu.` target is enabled later, submit selected Pauli-term measurement circuits for the best candidate.

## Azure QPU Status

- Requested target: `{qpu.get('requested_target', 'not requested')}`
- Submitted: `{qpu.get('submitted', False)}`
- Succeeded: `{qpu.get('succeeded', False)}`
- Reason: `{qpu.get('reason', 'n/a')}`

## Reproducibility

```bash
cd quantum_experiments
python -X utf8 14_qheat_facade_qpu/code/run_experiment.py --mode local

# Real QPU path, only after a .qpu. target is enabled in the workspace:
python -X utf8 14_qheat_facade_qpu/code/run_experiment.py --mode azure-qpu --target quantinuum.qpu.h2-1 --shots 100 --confirm-real-qpu-cost
```

## What Would Make This Publishable

- Replace the heuristic reduced parameters with active-space Hamiltonians derived from quantum chemistry or DFT downfolding.
- Run at least one selected Pauli-term measurement batch on a real `.qpu.` target.
- Synthesize or source a related coating sample and measure emissivity, conductivity, fire behavior, weathering, and thermal performance.
- Compare against classical materials workflows instead of claiming classical impossibility.
"""
    path = out_dir / "report.md"
    path.write_text(report, encoding="utf-8")
    return path


def save_results(payload: dict, out_dir: Path) -> Path:
    out_dir.joinpath("data").mkdir(parents=True, exist_ok=True)
    path = out_dir / "data" / "qheat_results.json"
    path.write_text(json.dumps(_jsonable(payload), indent=2), encoding="utf-8")
    return path


def main(
    mode: str = "local",
    shots: int = 100,
    target: str = "quantinuum.qpu.h2-1",
    max_terms: int = 8,
    maxiter: int = 250,
    confirm_real_qpu_cost: bool = False,
) -> dict:
    print("\n" + "=" * 72)
    print("EXPERIMENT 14: Q-HEAT facade-material QPU benchmark")
    effective_mode = "azure-qpu" if mode == "azure" else mode
    print(f"Mode: {effective_mode} | Target: {target} | Shots: {shots}")
    print("=" * 72)

    out_dir = RESULTS_DIR
    out_dir.joinpath("figures").mkdir(parents=True, exist_ok=True)
    out_dir.joinpath("data").mkdir(parents=True, exist_ok=True)

    print("\n>>> Running local reduced-Hamiltonian screen...")
    screen = run_local_screen(maxiter=maxiter)
    best_name = screen["ranked_candidates"][0]["label"]
    print(f"  Best candidate: {best_name}")

    print("\n>>> Checking Azure target availability...")
    best_row = next(r for r in screen["rows"] if r["name"] == screen["ranked_candidates"][0]["name"])
    azure_qpu = None
    if effective_mode == "azure-qpu":
        azure_qpu = run_real_qpu_measurement(
            best_row,
            target=target,
            shots=shots,
            max_terms=max_terms,
            confirm_real_qpu_cost=confirm_real_qpu_cost,
        )
        print(f"  Azure QPU status: {azure_qpu.get('reason') or azure_qpu.get('succeeded')}")
    else:
        target_snapshot = list_azure_targets()
        azure_qpu = {
            "requested_target": target,
            "shots": shots,
            "max_terms": max_terms,
            "target_snapshot": target_snapshot,
            "submitted": False,
            "succeeded": False,
            "reason": "local_mode_target_check_only",
            "jobs": [],
        }
        print(f"  Enabled QPU targets: {len(target_snapshot.get('qpu_targets', []))}")

    payload = {
        "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        "experiment": "qheat_facade_material_qpu_benchmark",
        "material_hypothesis": {
            "name": "Q-HEAT",
            "expanded_name": "Quantum-screened High-Entropy Aerogel Thermal facade coating",
            "candidate_architecture": [
                "porous high-entropy ceramic/aerogel insulation matrix",
                "sparse correlated transition-metal dopant-pair centers",
                "targeted mid-IR emissivity and solar-thermal response tuning",
            ],
            "claim_boundary": "new material hypothesis only; not synthesized or validated",
        },
        "screen": screen,
        "azure_qpu": azure_qpu,
    }

    print("\n>>> Generating figures and report...")
    try:
        plot_results(screen, out_dir)
    except Exception as exc:
        print(f"  Figure warning: {exc}")
    data_path = save_results(payload, out_dir)
    report_path = write_report(payload, out_dir)
    print(f"  Data:   {data_path}")
    print(f"  Report: {report_path}")
    print(">>> Experiment 14 complete.")
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["local", "azure", "azure-qpu"], default="local")
    parser.add_argument("--shots", type=int, default=100)
    parser.add_argument("--target", type=str, default="quantinuum.qpu.h2-1")
    parser.add_argument("--max-terms", type=int, default=8)
    parser.add_argument("--maxiter", type=int, default=250)
    parser.add_argument("--confirm-real-qpu-cost", action="store_true")
    args = parser.parse_args()
    main(
        mode=args.mode,
        shots=args.shots,
        target=args.target,
        max_terms=args.max_terms,
        maxiter=args.maxiter,
        confirm_real_qpu_cost=args.confirm_real_qpu_cost,
    )
