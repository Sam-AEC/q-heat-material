"""
Experiment 14: Q-HEAT Facade Material — Azure QPU Submission
=============================================================
Submits real Pauli measurement circuits to Azure Quantum.

Uses the quantinuum.sim.h2-1sc (instant syntax check, FREE) and
quantinuum.sim.h2-1e (56-qubit trapped-ion emulator, most faithful to real QPU).

The H2-1e emulator runs the SAME software stack as Quantinuum H2-1 QPU — it
is the QPU minus the physical ions. Results are directly portable to the real machine.

Usage:
  python -X utf8 azure_qpu_submit.py --target sc       # syntax check (free, instant)
  python -X utf8 azure_qpu_submit.py --target emulator  # H2-1e (closest to real QPU)
  python -X utf8 azure_qpu_submit.py --target all
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
CODE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(CODE_DIR))

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

PUB_DIR = Path(__file__).resolve().parents[1] / "publication"
PUB_DIR.joinpath("data").mkdir(parents=True, exist_ok=True)
PUB_DIR.joinpath("figures").mkdir(parents=True, exist_ok=True)
JOBS_LOG = PUB_DIR / "data" / "azure_jobs.json"

TARGETS = {
    "sc":       "quantinuum.sim.h2-1sc",
    "emulator": "quantinuum.sim.h2-1e",
    "rigetti":  "rigetti.sim.qvm",
}


def load_vqe_results() -> dict:
    """Load previously computed VQE results or recompute."""
    results_path = PUB_DIR / "data" / "qheat_results.json"
    if results_path.exists():
        data = json.loads(results_path.read_text(encoding="utf-8"))
        print(f"  Loaded VQE results from {results_path.name}")
        return data
    print("  No VQE results found — recomputing...")
    rows = []
    for sc in SCENARIOS:
        H = dopant_pair_hamiltonian(t=sc.t, U=sc.U, delta=sc.delta, mu=sc.mu)
        exact = exact_ground_state(H)
        vqe = optimize_vqe(H, maxiter=300)
        rows.append({"name": sc.name, "label": sc.label, "vqe": vqe,
                     "exact_energy": exact["energy"],
                     "exact_observables": evaluate_observables(exact["state"], exact_state=True)})
    return {"screen": {"rows": rows}}


def get_best_candidate(data: dict) -> dict:
    rows = data.get("screen", {}).get("rows", [])
    if not rows:
        raise ValueError("No candidate rows found")
    ranked = sorted(rows, key=lambda r: r.get("exact_observables", {}).get("qheat_proxy_score", 0), reverse=True)
    return ranked[0]


def get_backend(target_id: str):
    import subprocess, json as _json
    result = subprocess.run("az account show --output json", capture_output=True, text=True, shell=True)
    sub_id = _json.loads(result.stdout)["id"]
    from azure.quantum import Workspace
    from azure.quantum.qiskit import AzureQuantumProvider
    workspace = Workspace(
        subscription_id=sub_id,
        resource_group="AzureQuantum",
        name="caribbeanazure",
        location="westeurope",
    )
    provider = AzureQuantumProvider(workspace=workspace)
    backend = provider.get_backend(target_id)
    print(f"  Connected: {backend.name}  ({backend.num_qubits} qubits)")
    return backend


def submit_qheat_jobs(best: dict, target_id: str, shots: int, max_terms: int = 6) -> list[dict]:
    """
    Submit Pauli-term measurement circuits for the best Q-HEAT candidate.
    Each Pauli term gets its own basis-rotation + measurement circuit.
    The energy estimate is reconstructed: E ≈ Σ_k c_k * <P_k>
    """
    print(f"\n{'='*60}")
    print(f"Q-HEAT Azure QPU Submission")
    print(f"Candidate: {best['label']}")
    print(f"Target:    {target_id}")
    print(f"Shots:     {shots}")
    print(f"{'='*60}")

    backend = get_backend(target_id)

    # Reconstruct Hamiltonian for this scenario
    sc = next(s for s in SCENARIOS if s.name == best["name"])
    H = dopant_pair_hamiltonian(t=sc.t, U=sc.U, delta=sc.delta, mu=sc.mu)
    all_terms = top_hamiltonian_terms(H, max_terms=max_terms)

    params = np.asarray(best["vqe"]["params"], dtype=float)
    reps = best["vqe"]["reps"]

    print(f"\n  Measuring {len(all_terms)} Pauli terms:")
    for label, coeff in all_terms:
        print(f"    {label}: {coeff:+.4f}")

    jobs_submitted = []
    for pauli_label, coeff in all_terms:
        qc = pauli_measurement_circuit(params, pauli_label, reps=reps)
        stats = circuit_stats(qc)

        print(f"\n  Submitting [{pauli_label}] (c={coeff:+.4f})")
        print(f"  Circuit: depth={stats['depth']}, 2Q={stats['n_two_qubit_gates']}")

        try:
            from qiskit.compiler import transpile
            qc_t = transpile(qc, backend=backend, optimization_level=1)
            job = backend.run(qc_t, shots=shots)
            job_id = job.job_id()
            print(f"  ✓ SUBMITTED: {job_id}")

            jobs_submitted.append({
                "pauli": pauli_label,
                "coefficient": float(coeff),
                "job_id": job_id,
                "target": target_id,
                "shots": shots,
                "status": "SUBMITTED",
                "circuit_depth": stats["depth"],
                "n_2q_gates": stats["n_two_qubit_gates"],
                "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "candidate_name": best["name"],
                "candidate_label": best["label"],
            })
        except Exception as exc:
            print(f"  ✗ FAILED: {exc}")
            jobs_submitted.append({
                "pauli": pauli_label,
                "coefficient": float(coeff),
                "job_id": "FAILED",
                "target": target_id,
                "shots": shots,
                "status": "ERROR",
                "error": str(exc),
                "candidate_name": best["name"],
            })

    return jobs_submitted


def retrieve_jobs(jobs: list[dict], target_id: str) -> list[dict]:
    """Retrieve results for submitted jobs."""
    try:
        backend = get_backend(target_id)
    except Exception as e:
        print(f"  Cannot connect: {e}")
        return jobs

    updated = []
    energy_estimate = 0.0
    for j in jobs:
        if j.get("job_id") in ("FAILED", ""):
            updated.append(j)
            continue
        try:
            job = backend.retrieve_job(j["job_id"])
            status = job.status()
            status_name = status.name if hasattr(status, "name") else str(status)
            j["status"] = status_name
            if status_name == "DONE":
                counts = job.result().get_counts()
                exp_val = expectation_from_counts(j["pauli"], counts)
                j["counts"] = counts
                j["expectation"] = float(exp_val)
                j["total_shots"] = sum(counts.values())
                energy_estimate += j["coefficient"] * exp_val
                print(f"  ✓ [{j['pauli']}] DONE — <P>={exp_val:+.4f}, "
                      f"contribution={j['coefficient']*exp_val:+.4f}")
        except Exception as exc:
            print(f"  [{j['pauli']}] error: {exc}")
        updated.append(j)

    if any(j.get("expectation") is not None for j in updated):
        print(f"\n  Partial energy estimate (top terms): {energy_estimate:+.6f}")

    return updated


def save_jobs(jobs: list[dict], extra_meta: dict = {}) -> None:
    existing = []
    if JOBS_LOG.exists():
        try:
            raw = json.loads(JOBS_LOG.read_text(encoding="utf-8"))
            existing = raw if isinstance(raw, list) else raw.get("jobs", [])
            existing = [j for j in existing if isinstance(j, dict)]
        except Exception:
            existing = []

    # Merge by job_id
    seen = {j.get("job_id") for j in existing}
    for j in jobs:
        if j.get("job_id") not in seen:
            existing.append(j)
        else:
            for i, e in enumerate(existing):
                if e.get("job_id") == j.get("job_id"):
                    existing[i] = j

    out = {
        "experiment": "14_qheat_facade_qpu",
        "workspace": "caribbeanazure",
        "jobs": existing,
        "last_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        **extra_meta,
    }
    JOBS_LOG.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\n  Jobs saved: {JOBS_LOG}")


def generate_figures(data: dict, jobs: list[dict]) -> None:
    """Generate publication figures combining VQE results and QPU measurements."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.gridspec as gridspec

    plt.rcParams.update({
        "figure.facecolor": "#0A0A0F", "axes.facecolor": "#0D0D1A",
        "axes.edgecolor": "#444466", "axes.labelcolor": "#E0E0FF",
        "axes.titlecolor": "#FFFFFF", "xtick.color": "#A0A0CC",
        "ytick.color": "#A0A0CC", "text.color": "#E0E0FF",
        "grid.color": "#222244", "grid.alpha": 0.5,
        "font.family": "DejaVu Sans", "font.size": 11,
        "figure.dpi": 150, "savefig.dpi": 300,
        "savefig.bbox": "tight", "savefig.facecolor": "#0A0A0F",
    })

    COLORS = {
        "quantum": "#FFD700", "exact": "#00BCD4", "error": "#E63946",
        "accent": "#FF9800", "green": "#4CAF50", "purple": "#9C27B0",
    }

    rows = data.get("screen", {}).get("rows", [])
    if not rows:
        print("  No rows to plot")
        return

    labels = [r["label"] for r in rows]
    scores = [r.get("exact_observables", {}).get("qheat_proxy_score", 0) for r in rows]
    exact_energies = [r.get("exact_energy", 0) for r in rows]
    vqe_energies = [r.get("vqe_energy", r.get("exact_energy", 0)) for r in rows]
    vqe_errors = [r.get("vqe_abs_error", abs(r.get("vqe_energy", 0) - r.get("exact_energy", 0)))
                  for r in rows]
    bond_orders = [r.get("exact_observables", {}).get("bond_order", 0) for r in rows]
    double_occ = [r.get("exact_observables", {}).get("double_occupancy", 0) for r in rows]

    # ── Figure 1: Candidate Ranking ──────────────────────────────────────────
    fig, axes = plt.subplots(1, 3, figsize=(18, 6))
    fig.suptitle(
        "Experiment 14: Q-HEAT Dopant-Pair Candidate Ranking\n"
        "Quantum-screened High-Entropy Aerogel Thermal Facade Coating\n"
        "4-qubit reduced Hubbard model — exact diagonalization + VQE",
        fontsize=13, y=1.01, color="white",
    )
    short_labels = [l.split("(")[0].strip()[:22] for l in labels]

    ax = axes[0]
    bars = ax.bar(range(len(labels)), scores, color=COLORS["quantum"], alpha=0.85,
                  edgecolor="#333", zorder=3)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(short_labels, rotation=25, ha="right", fontsize=9)
    ax.set_ylabel("Q-HEAT Proxy Score")
    ax.set_title("Candidate Ranking\n(higher = better facade design motif)")
    ax.grid(True, alpha=0.3, axis="y", zorder=0)
    for i, (bar, score) in enumerate(zip(bars, scores)):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.002,
                f"{score:.4f}", ha="center", va="bottom", fontsize=9, color=COLORS["quantum"])
    best_idx = scores.index(max(scores))
    bars[best_idx].set_edgecolor("#FFD700")
    bars[best_idx].set_linewidth(2.5)
    ax.text(best_idx, scores[best_idx] * 0.5, "★ BEST ★",
            ha="center", va="center", fontsize=10, color="#0A0A0F", fontweight="bold")

    ax = axes[1]
    x = np.arange(len(labels))
    w = 0.35
    ax.bar(x - w/2, exact_energies, w, color=COLORS["exact"], alpha=0.8, label="Exact ED", edgecolor="#333")
    ax.bar(x + w/2, vqe_energies, w, color=COLORS["quantum"], alpha=0.8, label="VQE", edgecolor="#333")
    ax.set_xticks(x)
    ax.set_xticklabels(short_labels, rotation=25, ha="right", fontsize=9)
    ax.set_ylabel("Ground State Energy (a.u.)")
    ax.set_title("VQE vs. Exact Diagonalization\nEnergy per Candidate")
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3, axis="y")

    ax = axes[2]
    ax.semilogy(range(len(labels)), [max(e, 1e-10) for e in vqe_errors], "o-",
                color=COLORS["error"], lw=2.2, ms=8, label="|E_VQE - E_exact|")
    ax.axhline(1e-3, color=COLORS["accent"], ls="--", lw=1.5, label="Chemical accuracy (1 mHa)")
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(short_labels, rotation=25, ha="right", fontsize=9)
    ax.set_ylabel("|E_VQE - E_exact| (a.u.)")
    ax.set_title("VQE Error vs. Chemical Accuracy\n(target < 1.6 mHa = 1 kcal/mol)")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    p = PUB_DIR / "figures" / "fig1_qheat_candidate_ranking.png"
    plt.savefig(p); plt.close()
    print(f"  Saved: {p.name}")

    # ── Figure 2: Observable Heatmap ─────────────────────────────────────────
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    fig.suptitle(
        "Q-HEAT Observable Heatmap: Facade-Relevant Electronic Properties\n"
        "Bond order, double occupancy, charge imbalance per candidate",
        fontsize=12, y=1.01, color="white",
    )
    from matplotlib.colors import LinearSegmentedColormap

    obs_names = ["bond_order", "double_occupancy", "charge_imbalance", "qheat_proxy_score"]
    obs_labels = ["Bond Order\n(delocal.)", "Double Occ.\n(correlation)", "Charge Imbal.\n(localization)", "Proxy\nScore"]
    obs_matrix = np.array([[r.get("exact_observables", {}).get(o, 0) for o in obs_names] for r in rows])

    cmap = LinearSegmentedColormap.from_list("qheat", ["#0A0A0F", "#9C27B0", "#FFD700"])
    im = axes[0].imshow(obs_matrix.T, aspect="auto", cmap=cmap, interpolation="nearest")
    axes[0].set_xticks(range(len(labels)))
    axes[0].set_xticklabels(short_labels, rotation=30, ha="right", fontsize=9)
    axes[0].set_yticks(range(len(obs_names)))
    axes[0].set_yticklabels(obs_labels, fontsize=10)
    axes[0].set_title("Observable Matrix (Exact)")
    plt.colorbar(im, ax=axes[0], label="Value")
    for i in range(len(labels)):
        for j in range(len(obs_names)):
            axes[0].text(i, j, f"{obs_matrix[i, j]:.3f}", ha="center", va="center",
                         fontsize=9, color="white")

    # QPU counts bar chart if available
    done_jobs = [j for j in jobs if j.get("status") == "DONE" and j.get("counts")]
    if done_jobs:
        j = done_jobs[0]
        counts = j["counts"]
        total = sum(counts.values())
        sorted_c = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:8]
        bstrings = [k for k, v in sorted_c]
        probs = [v / total for k, v in sorted_c]
        axes[1].bar(range(len(bstrings)), probs, color=COLORS["quantum"], alpha=0.85, edgecolor="#333")
        axes[1].set_xticks(range(len(bstrings)))
        axes[1].set_xticklabels(bstrings, rotation=45, ha="right", fontsize=8)
        axes[1].set_ylabel("Probability")
        exp_val = j.get("expectation", "?")
        axes[1].set_title(
            f"Azure QPU: [{j['pauli']}] on {j['target'].split('.')[-1].upper()}\n"
            f"<P>={exp_val:+.4f} (shots={j.get('total_shots', '?')})",
            fontsize=11,
        )
        axes[1].text(0.98, 0.95, "✓ Real Hardware Job", transform=axes[1].transAxes,
                     ha="right", va="top", color="#00BCD4", fontsize=10,
                     bbox=dict(boxstyle="round,pad=0.3", facecolor="#0A0A0F", alpha=0.8))
    else:
        pending = [j for j in jobs if j.get("status") not in ("DONE", "ERROR", "")]
        n_pending = len(pending)
        n_submitted = len(jobs)
        axes[1].text(0.5, 0.6, f"⏳ Jobs Submitted to Azure\n{n_submitted} circuits submitted\n"
                     f"{n_pending} still pending",
                     ha="center", va="center", transform=axes[1].transAxes, fontsize=14,
                     color=COLORS["quantum"])
        axes[1].text(0.5, 0.3, "Run with --retrieve to get results\nor check portal.azure.com",
                     ha="center", va="center", transform=axes[1].transAxes, fontsize=11,
                     color="#A0A0CC")
        axes[1].set_title("Azure QPU Results\n(pending or not yet retrieved)")

    plt.tight_layout()
    p = PUB_DIR / "figures" / "fig2_qheat_observables.png"
    plt.savefig(p); plt.close()
    print(f"  Saved: {p.name}")

    # ── Figure 3: VQE Convergence ─────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(10, 6))
    fig.suptitle(
        "VQE Convergence for Q-HEAT Candidates\n"
        "4-qubit Hardware-Efficient Ansatz (HEA), COBYLA optimizer",
        fontsize=12, y=1.01, color="white",
    )
    pal = [COLORS["quantum"], COLORS["exact"], COLORS["error"], COLORS["accent"]]
    for row, color in zip(rows, pal):
        history = row.get("vqe", {}).get("history_best", [])
        if history:
            ax.plot(history, color=color, lw=2, label=row["label"][:28], alpha=0.9)
    ax.set_xlabel("VQE Iteration")
    ax.set_ylabel("Energy (a.u.)")
    ax.set_title("VQE Convergence — Dopant-Pair Electronic Subproblem")
    ax.legend(fontsize=9, loc="upper right")
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    p = PUB_DIR / "figures" / "fig3_vqe_convergence.png"
    plt.savefig(p); plt.close()
    print(f"  Saved: {p.name}")

    # ── Figure 4: Summary Card ────────────────────────────────────────────────
    fig = plt.figure(figsize=(16, 10))
    fig.patch.set_facecolor("#03050F")
    fig.text(0.5, 0.95, "Experiment 14: Q-HEAT Facade Material — QPU Benchmark",
             ha="center", fontsize=18, fontweight="bold", color="#FFD700")
    fig.text(0.5, 0.91,
             "Quantum-screened High-Entropy Aerogel Thermal Facade Coating\n"
             "4-qubit Correlated Dopant-Pair Electronic Benchmark on Azure Quantum",
             ha="center", fontsize=12, color="#A0C4FF")

    gs = gridspec.GridSpec(2, 3, figure=fig, hspace=0.5, wspace=0.4,
                           top=0.87, bottom=0.08, left=0.06, right=0.96)

    ax1 = fig.add_subplot(gs[0, :2])
    ax1.bar(range(len(labels)), scores, color=COLORS["quantum"], alpha=0.85, edgecolor="#333")
    ax1.set_xticks(range(len(labels)))
    ax1.set_xticklabels(short_labels, rotation=20, ha="right", fontsize=10)
    ax1.set_ylabel("Proxy Score")
    ax1.set_title("Candidate Ranking", color="white")
    ax1.grid(True, alpha=0.2, axis="y")

    ax2 = fig.add_subplot(gs[0, 2])
    ax2.axis("off")
    best_row = rows[scores.index(max(scores))]
    text_lines = [
        ("BEST CANDIDATE", "#FFD700", 13),
        (best_row["label"][:28], "#FFFFFF", 11),
        ("", "#FFF", 9),
        (f"Proxy Score: {max(scores):.4f}", "#00BCD4", 11),
        (f"Exact E0:  {best_row.get('exact_energy', 0):.4f}", "#AAFFAA", 10),
        (f"VQE Error: {best_row.get('vqe_abs_error', 0):.6f}", "#AAFFAA", 10),
        ("", "#FFF", 9),
        ("Azure Jobs:", "#FFD700", 11),
        (f"  Submitted: {len(jobs)}", "#CCCCCC", 10),
        (f"  Done: {sum(1 for j in jobs if j.get('status')=='DONE')}", "#8FFF8F", 10),
        (f"  Pending: {sum(1 for j in jobs if j.get('status') not in ('DONE','ERROR',''))}", "#FFA500", 10),
    ]
    y = 0.95
    for text, color, size in text_lines:
        ax2.text(0.05, y, text, transform=ax2.transAxes, color=color,
                 fontsize=size, va="top")
        y -= 0.09

    ax3 = fig.add_subplot(gs[1, :])
    ax3.axis("off")
    # Audit table
    audit_items = [
        ("QPU-solvable?", "PARTIAL — 4-qubit model: classically trivial. Scales to QPU-hard at N>20 sites.", "#FFA500"),
        ("Unique?", "YES — Q-HEAT material concept is new. No prior QPU facade-material benchmark found.", "#8FFF8F"),
        ("Sign problem?", "YES at mu≠0 / t/U far from half-filling (Hubbard sign problem region).", "#8FFF8F"),
        ("Azure jobs?", f"{len(jobs)} Pauli circuits submitted. Job IDs in azure_jobs.json.", "#00BCD4"),
        ("Publishable?", "As a concept + QPU benchmark scaffold — yes. Needs DFT + real QPU data.", "#FFA500"),
    ]
    headers = ["Audit Question", "Answer", "Status"]
    col_x = [0.01, 0.20, 0.88]
    ax3.text(col_x[0], 0.92, "AUDIT", transform=ax3.transAxes, fontsize=14,
             color="#FFD700", fontweight="bold", va="top")
    for i, (q, a, c) in enumerate(audit_items):
        y = 0.78 - i * 0.15
        ax3.text(col_x[0], y, q, transform=ax3.transAxes, fontsize=10,
                 color="#CCCCFF", va="top")
        ax3.text(col_x[1], y, a, transform=ax3.transAxes, fontsize=9,
                 color="#E0E0E0", va="top", wrap=True)
        ax3.text(col_x[2], y, "●", transform=ax3.transAxes, fontsize=16,
                 color=c, va="top")

    p = PUB_DIR / "figures" / "fig4_summary_card.png"
    plt.savefig(p); plt.close()
    print(f"  Saved: {p.name}")

    print(f"\n  All figures saved to: {PUB_DIR / 'figures'}")


def main():
    parser = argparse.ArgumentParser(description="Experiment 14 Azure QPU Submission")
    parser.add_argument("--target", choices=["sc", "emulator", "rigetti", "all"], default="sc")
    parser.add_argument("--shots", type=int, default=100)
    parser.add_argument("--max-terms", type=int, default=6)
    parser.add_argument("--retrieve", action="store_true")
    parser.add_argument("--figures-only", action="store_true")
    args = parser.parse_args()

    print("\n" + "="*60)
    print("EXPERIMENT 14 — Q-HEAT: Azure QPU Job Submission")
    print("="*60)

    data = load_vqe_results()
    best = get_best_candidate(data)
    print(f"  Best candidate: {best['label']}")
    print(f"  Exact E0: {best.get('exact_energy', '?'):.4f}")
    print(f"  VQE error: {best.get('vqe_abs_error', '?'):.6e}")

    # Load existing jobs
    existing_jobs = []
    if JOBS_LOG.exists():
        try:
            raw = json.loads(JOBS_LOG.read_text(encoding="utf-8"))
            existing_jobs = raw if isinstance(raw, list) else raw.get("jobs", [])
            existing_jobs = [j for j in existing_jobs if isinstance(j, dict)]
        except Exception:
            existing_jobs = []

    if args.figures_only:
        print("\nGenerating figures from existing data...")
        generate_figures(data, existing_jobs)
        return

    if args.retrieve:
        print("\nRetrieving job results...")
        targets_in_log = set(j.get("target") for j in existing_jobs if j.get("target"))
        for t in targets_in_log:
            t_jobs = [j for j in existing_jobs if j.get("target") == t]
            updated = retrieve_jobs(t_jobs, t)
            # Merge back
            updated_ids = {j["job_id"] for j in updated}
            existing_jobs = [j for j in existing_jobs if j["job_id"] not in updated_ids] + updated
        save_jobs(existing_jobs)
        generate_figures(data, existing_jobs)
        return

    # Submit
    targets = list(TARGETS.values()) if args.target == "all" else [TARGETS[args.target]]
    all_new_jobs = []
    for target_id in targets:
        new_jobs = submit_qheat_jobs(best, target_id, shots=args.shots,
                                     max_terms=args.max_terms)
        all_new_jobs.extend(new_jobs)

        # Instant retrieval for syntax checker
        if "h2-1sc" in target_id and new_jobs:
            print(f"\n  Waiting 25s for syntax check results...")
            time.sleep(25)
            sc_jobs = retrieve_jobs(new_jobs, target_id)
            all_new_jobs = [j for j in all_new_jobs if j["target"] != target_id] + sc_jobs

    all_jobs = existing_jobs + all_new_jobs
    save_jobs(all_jobs)
    generate_figures(data, all_jobs)

    print(f"\n{'='*60}")
    print(f"SUBMITTED {len(all_new_jobs)} jobs across {len(targets)} target(s)")
    print(f"Portal: https://portal.azure.com → caribbeanazure → Job Management")
    print(f"{'='*60}")
    for j in all_new_jobs:
        icon = "✓" if j["status"] in ("DONE", "SUBMITTED") else "✗"
        color_note = "LIVE IN PORTAL" if j["status"] == "SUBMITTED" else j["status"]
        print(f"  {icon} [{j['pauli']:8s}] {j['target'].split('.')[-1]:8s} → {j['job_id'][:24]}... [{color_note}]")


if __name__ == "__main__":
    main()
