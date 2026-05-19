"""
Q-HEAT facade-material Hamiltonians and QPU measurement circuits.

Q-HEAT is a proposed material hypothesis:

    Quantum-screened High-Entropy Aerogel Thermal facade coating.

The concept combines a porous high-entropy ceramic/aerogel insulation matrix
with sparse correlated transition-metal dopant-pair centers intended to tune
mid-IR emissivity and solar-thermal response. This module does not claim that
the material has been synthesized or validated. It builds a four-qubit reduced
active-space model for the dopant-pair electronic subproblem that could be run
on a real QPU when a `.qpu.` Azure Quantum target is enabled.

Qubit layout:
    q0 = V0 spin-up orbital
    q1 = V0 spin-down orbital
    q2 = V1 spin-up orbital
    q3 = V1 spin-down orbital

Reduced dopant-pair Hamiltonian:
    H = -t sum_sigma (c0s^dag c1s + h.c.)
        + U sum_i n_i_up n_i_down
        + (delta / 2) [(n0_up+n0_down) - (n1_up+n1_down)]
        - mu sum_p n_p

The model is intentionally small enough to validate exactly and shallow enough
to submit Pauli-term measurement circuits to a real QPU when one is enabled.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
from qiskit import QuantumCircuit
from qiskit.quantum_info import SparsePauliOp, Statevector

try:
    from scipy.optimize import minimize
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False


N_QUBITS = 4


@dataclass(frozen=True)
class QHeatScenario:
    """Candidate dopant-pair parameter point for the Q-HEAT material hypothesis."""

    name: str
    label: str
    t: float
    U: float
    delta: float
    mu: float
    design_hypothesis: str


SCENARIOS: list[QHeatScenario] = [
    QHeatScenario(
        name="v_w_entropy_center",
        label="V-W entropy-screened center",
        t=0.78,
        U=2.70,
        delta=0.18,
        mu=0.00,
        design_hypothesis="V-W dopant pair in high-entropy aluminosilicate aerogel matrix",
    ),
    QHeatScenario(
        name="v_nb_balanced_center",
        label="V-Nb balanced emissivity center",
        t=0.92,
        U=2.95,
        delta=0.08,
        mu=0.00,
        design_hypothesis="V-Nb pair designed to preserve delocalization while adding defect scattering",
    ),
    QHeatScenario(
        name="cr_w_fire_stable_center",
        label="Cr-W fire-stable absorber center",
        t=0.66,
        U=3.35,
        delta=0.32,
        mu=0.00,
        design_hypothesis="Cr-W pair emphasizes oxidation and fire stability over switching amplitude",
    ),
    QHeatScenario(
        name="ti_v_control_center",
        label="Ti-V low-cost control center",
        t=1.05,
        U=3.10,
        delta=0.22,
        mu=0.00,
        design_hypothesis="Ti-V lower-cost control pair for facade-scale manufacturability",
    ),
]


def _pauli(n_qubits: int, ops: dict[int, str]) -> str:
    chars = ["I"] * n_qubits
    for idx, op in ops.items():
        chars[idx] = op
    return "".join(reversed(chars))


def _combine_terms(terms: Iterable[tuple[str, float]]) -> SparsePauliOp:
    accum: dict[str, float] = {}
    for label, coeff in terms:
        if abs(coeff) < 1e-14:
            continue
        accum[label] = accum.get(label, 0.0) + float(coeff)
    return SparsePauliOp.from_list([(k, v) for k, v in accum.items()]).simplify()


def _identity() -> str:
    return "I" * N_QUBITS


def _number_terms(qubit: int, coeff: float) -> list[tuple[str, float]]:
    # coeff * n_q = coeff * (I - Z_q) / 2
    return [
        (_identity(), 0.5 * coeff),
        (_pauli(N_QUBITS, {qubit: "Z"}), -0.5 * coeff),
    ]


def _double_occupancy_terms(q_up: int, q_down: int, coeff: float) -> list[tuple[str, float]]:
    # coeff * n_up n_down = coeff/4 * (I - Z_up - Z_down + Z_up Z_down)
    return [
        (_identity(), 0.25 * coeff),
        (_pauli(N_QUBITS, {q_up: "Z"}), -0.25 * coeff),
        (_pauli(N_QUBITS, {q_down: "Z"}), -0.25 * coeff),
        (_pauli(N_QUBITS, {q_up: "Z", q_down: "Z"}), 0.25 * coeff),
    ]


def _hopping_terms(q_left: int, q_right: int, coeff: float) -> list[tuple[str, float]]:
    # coeff * (a_left^dag a_right + h.c.) under Jordan-Wigner.
    # For q_left < q_right: 0.5 * coeff * (X Z... X + Y Z... Y).
    if q_left > q_right:
        q_left, q_right = q_right, q_left
    z_string = {q: "Z" for q in range(q_left + 1, q_right)}
    return [
        (_pauli(N_QUBITS, {q_left: "X", q_right: "X", **z_string}), 0.5 * coeff),
        (_pauli(N_QUBITS, {q_left: "Y", q_right: "Y", **z_string}), 0.5 * coeff),
    ]


def dopant_pair_hamiltonian(t: float, U: float, delta: float, mu: float = 0.0) -> SparsePauliOp:
    """Return the four-qubit reduced correlated dopant-pair Hamiltonian."""
    terms: list[tuple[str, float]] = []

    # Hopping across the correlated dopant pair for spin up and spin down.
    terms.extend(_hopping_terms(0, 2, -t))
    terms.extend(_hopping_terms(1, 3, -t))

    # On-site correlation.
    terms.extend(_double_occupancy_terms(0, 1, U))
    terms.extend(_double_occupancy_terms(2, 3, U))

    # Local chemistry / vacancy / matrix-asymmetry proxy.
    for q in (0, 1):
        terms.extend(_number_terms(q, +0.5 * delta))
    for q in (2, 3):
        terms.extend(_number_terms(q, -0.5 * delta))

    # Chemical potential, retained for future charged-defect variants.
    for q in range(N_QUBITS):
        terms.extend(_number_terms(q, -mu))

    return _combine_terms(terms)


def double_occupancy_operator() -> SparsePauliOp:
    """Average double occupancy across the two V sites."""
    return _combine_terms(
        _double_occupancy_terms(0, 1, 0.5)
        + _double_occupancy_terms(2, 3, 0.5)
    )


def charge_imbalance_operator() -> SparsePauliOp:
    """Return (n_site0 - n_site1), where each site has two spin orbitals."""
    terms: list[tuple[str, float]] = []
    for q in (0, 1):
        terms.extend(_number_terms(q, +1.0))
    for q in (2, 3):
        terms.extend(_number_terms(q, -1.0))
    return _combine_terms(terms)


def bond_order_operator() -> SparsePauliOp:
    """Return sum_sigma (c0s^dag c1s + h.c.) for the dopant pair."""
    return _combine_terms(
        _hopping_terms(0, 2, 1.0)
        + _hopping_terms(1, 3, 1.0)
    )


def build_ansatz(params: np.ndarray, reps: int = 2, measure: bool = False) -> QuantumCircuit:
    """Hardware-efficient four-qubit ansatz initialized in a two-electron sector."""
    expected = 4 * (reps + 1)
    if len(params) != expected:
        raise ValueError(f"Expected {expected} parameters for reps={reps}, got {len(params)}")

    qc = QuantumCircuit(N_QUBITS)
    # Reference determinant with two electrons distributed over the dimer.
    qc.x(0)
    qc.x(3)

    cursor = 0
    for _ in range(reps):
        for q in range(N_QUBITS):
            qc.ry(float(params[cursor]), q)
            cursor += 1
        for left, right in [(0, 1), (1, 2), (2, 3), (0, 3)]:
            qc.cx(left, right)

    for q in range(N_QUBITS):
        qc.ry(float(params[cursor]), q)
        cursor += 1

    if measure:
        qc.measure_all()
    return qc


def circuit_stats(qc: QuantumCircuit) -> dict:
    """Return simple circuit metrics excluding measurement and barrier."""
    counts: dict[str, int] = {}
    n_gates = 0
    n_two_qubit = 0
    for instruction in qc.data:
        name = instruction.operation.name
        if name in {"measure", "barrier"}:
            continue
        counts[name] = counts.get(name, 0) + 1
        n_gates += 1
        if len(instruction.qubits) == 2:
            n_two_qubit += 1
    return {
        "n_qubits": qc.num_qubits,
        "depth": qc.depth(),
        "n_gates": n_gates,
        "n_two_qubit_gates": n_two_qubit,
        "gate_counts": counts,
        "n_parameters": 4 * 3,
    }


def expectation_from_params(op: SparsePauliOp, params: np.ndarray, reps: int = 2) -> float:
    qc = build_ansatz(params, reps=reps, measure=False)
    sv = Statevector.from_instruction(qc)
    return float(np.real(sv.expectation_value(op)))


def exact_ground_state(op: SparsePauliOp) -> dict:
    matrix = op.to_matrix()
    vals, vecs = np.linalg.eigh(matrix)
    idx = int(np.argmin(vals))
    state = Statevector(np.ascontiguousarray(vecs[:, idx]))
    return {"energy": float(np.real(vals[idx])), "state": state}


def optimize_vqe(op: SparsePauliOp, reps: int = 2, seed: int = 14, maxiter: int = 250) -> dict:
    """Small deterministic multi-start VQE using local statevector expectations."""
    rng = np.random.default_rng(seed)
    n_params = 4 * (reps + 1)

    def objective(x: np.ndarray) -> float:
        return expectation_from_params(op, x, reps=reps)

    starts = [np.zeros(n_params)]
    starts.extend(rng.uniform(-np.pi, np.pi, n_params) for _ in range(4))

    best_x = starts[0]
    best_e = objective(best_x)
    history: list[float] = [best_e]

    if _HAS_SCIPY:
        for x0 in starts:
            res = minimize(
                objective,
                x0,
                method="COBYLA",
                options={"maxiter": maxiter, "rhobeg": 0.6, "tol": 1e-5},
            )
            e = float(res.fun)
            history.append(e)
            if e < best_e:
                best_e = e
                best_x = np.asarray(res.x, dtype=float)
    else:
        for _ in range(maxiter):
            x = rng.uniform(-np.pi, np.pi, n_params)
            e = objective(x)
            history.append(e)
            if e < best_e:
                best_e = e
                best_x = x

    return {
        "energy": float(best_e),
        "params": np.round(best_x, 10).tolist(),
        "history_best": np.round(np.minimum.accumulate(history), 10).tolist(),
        "n_params": n_params,
        "reps": reps,
        "optimizer": "COBYLA" if _HAS_SCIPY else "random_search",
    }


def evaluate_observables(state_or_params, *, reps: int = 2, exact_state: bool = False) -> dict:
    """Evaluate facade-relevant proxy observables."""
    ops = {
        "double_occupancy": double_occupancy_operator(),
        "charge_imbalance": charge_imbalance_operator(),
        "bond_order": bond_order_operator(),
    }
    values: dict[str, float] = {}
    if exact_state:
        state = state_or_params
        for name, op in ops.items():
            values[name] = float(np.real(state.expectation_value(op)))
    else:
        params = np.asarray(state_or_params, dtype=float)
        for name, op in ops.items():
            values[name] = expectation_from_params(op, params, reps=reps)

    # Heuristic design proxy: high dopant-pair response without strong site localization.
    values["qheat_proxy_score"] = float(
        abs(values["bond_order"])
        * max(0.0, 1.0 - 0.5 * abs(values["charge_imbalance"]))
        / (1.0 + abs(values["double_occupancy"]))
    )
    return {k: round(v, 10) for k, v in values.items()}


def pauli_measurement_circuit(params: np.ndarray, pauli_label: str, reps: int = 2) -> QuantumCircuit:
    """Build a basis-rotated measurement circuit for one Pauli string."""
    qc = build_ansatz(params, reps=reps, measure=False)
    # label is in Qiskit order, rightmost character is qubit 0.
    little_endian = list(reversed(pauli_label))
    for q, op in enumerate(little_endian):
        if op == "X":
            qc.h(q)
        elif op == "Y":
            qc.sdg(q)
            qc.h(q)
        elif op in {"Z", "I"}:
            pass
        else:
            raise ValueError(f"Unsupported Pauli op {op!r}")
    qc.measure_all()
    return qc


def expectation_from_counts(pauli_label: str, counts: dict[str, int]) -> float:
    """Estimate a Pauli expectation from computational-basis counts."""
    total = sum(counts.values())
    if total == 0:
        return 0.0
    exp = 0.0
    little_endian = list(reversed(pauli_label))
    for bitstring, count in counts.items():
        bits = bitstring.replace(" ", "")[::-1]  # convert display string to qubit-index order
        parity = 0
        for q, op in enumerate(little_endian):
            if op == "I":
                continue
            if q < len(bits) and bits[q] == "1":
                parity ^= 1
        exp += ((-1.0) ** parity) * count / total
    return float(exp)


def top_hamiltonian_terms(op: SparsePauliOp, max_terms: int = 8) -> list[tuple[str, float]]:
    """Return the largest non-identity Hamiltonian terms by coefficient magnitude."""
    terms = []
    for label, coeff in zip(op.paulis.to_labels(), op.coeffs):
        if set(label) == {"I"}:
            continue
        terms.append((label, float(np.real(coeff))))
    return sorted(terms, key=lambda item: abs(item[1]), reverse=True)[:max_terms]
