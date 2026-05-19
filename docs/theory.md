# Theoretical Background: Q-HEAT Dopant-Pair Hamiltonian

## 1. Model Derivation

### 1.1 Physical System

We model the correlated electronic degrees of freedom of a transition-metal dopant pair $(\mathrm{M}_0, \mathrm{M}_1)$ embedded in a high-entropy ceramic/aerogel host. Each metal center contributes two spin orbitals (spin-up $\uparrow$ and spin-down $\downarrow$) to the active space, yielding a four-orbital, four-qubit system.

### 1.2 Second-Quantised Hamiltonian

The minimal Hubbard dimer Hamiltonian in second quantisation is:

$$\hat{H} = -t \sum_{\sigma \in \{\uparrow,\downarrow\}} \left( \hat{c}^\dagger_{0\sigma} \hat{c}_{1\sigma} + \mathrm{h.c.} \right) + U \sum_{i=0,1} \hat{n}_{i\uparrow} \hat{n}_{i\downarrow} + \frac{\delta}{2} \left( \hat{n}_0 - \hat{n}_1 \right) - \mu \sum_{i,\sigma} \hat{n}_{i\sigma}$$

where:

| Symbol | Physical meaning | Typical range for 3d/4d TM pairs |
|---|---|---|
| $t$ | Inter-site hopping integral | 0.5–1.2 eV |
| $U$ | On-site Coulomb repulsion (Hubbard $U$) | 2.0–4.0 eV |
| $\delta$ | Site-energy asymmetry (local chemistry, vacancy, strain) | 0–0.5 eV |
| $\mu$ | Chemical potential (carrier doping) | 0 by default |
| $\hat{n}_{i\sigma}$ | Number operator: $\hat{c}^\dagger_{i\sigma} \hat{c}_{i\sigma}$ | — |

### 1.3 Jordan-Wigner Transformation

The fermionic operators are mapped to Pauli operators via the Jordan-Wigner (JW) transformation. With qubit ordering $(q_0, q_1, q_2, q_3) = (0\uparrow, 0\downarrow, 1\uparrow, 1\downarrow)$:

$$\hat{c}^\dagger_{0\uparrow} = \frac{X_0 - iY_0}{2}, \quad \hat{c}^\dagger_{0\downarrow} = Z_0 \otimes \frac{X_1 - iY_1}{2}$$

$$\hat{c}^\dagger_{1\uparrow} = Z_0 \otimes Z_1 \otimes \frac{X_2 - iY_2}{2}, \quad \hat{c}^\dagger_{1\downarrow} = Z_0 \otimes Z_1 \otimes Z_2 \otimes \frac{X_3 - iY_3}{2}$$

The hopping term $\hat{c}^\dagger_{0\uparrow}\hat{c}_{1\uparrow} + \mathrm{h.c.}$ maps to:

$$\frac{1}{2}\left( X_0 Z_1 X_2 + Y_0 Z_1 Y_2 \right)$$

The double-occupancy term $\hat{n}_{0\uparrow}\hat{n}_{0\downarrow}$ maps to:

$$\frac{1}{4}\left( I - Z_0 - Z_1 + Z_0 Z_1 \right)$$

The full Pauli decomposition is implemented in `src/qheat_facade_circuits.py` via the `dopant_pair_hamiltonian()` function.

### 1.4 Symmetries and Conserved Quantities

The Hamiltonian conserves:
- **Total particle number** $\hat{N} = \sum_{i,\sigma} \hat{n}_{i\sigma}$ when $\mu = 0$.
- **Total $S_z$** when $\delta = 0$.

For the half-filling sector ($N = 2$ particles, $S_z = 0$), the ground state is typically a spin singlet with significant inter-site entanglement — the regime where quantum correlations are strongest and the proxy score is most physically meaningful.

---

## 2. Q-HEAT Proxy Score

The proxy score is a dimensionless figure of merit for ranking dopant-pair electronic motifs:

$$\mathcal{P} = \frac{|\langle \hat{B} \rangle| \cdot \max\left(0,\ 1 - \tfrac{1}{2}|\langle \hat{\Delta n} \rangle|\right)}{1 + |\langle \hat{D} \rangle|}$$

where:
- $\langle \hat{B} \rangle = \sum_\sigma \langle \hat{c}^\dagger_{0\sigma}\hat{c}_{1\sigma} + \mathrm{h.c.} \rangle$ — **bond order** (inter-site delocalization)
- $\langle \hat{\Delta n} \rangle = \langle \hat{n}_0 \rangle - \langle \hat{n}_1 \rangle$ — **charge imbalance** (site localization)
- $\langle \hat{D} \rangle = \sum_i \langle \hat{n}_{i\uparrow}\hat{n}_{i\downarrow} \rangle$ — **double occupancy** (correlation strength)

### Physical Rationale

| Factor | Numerator role | Physical motivation |
|---|---|---|
| $|\langle \hat{B}\rangle|$ | Reward | High bond order → delocalized ground state → metal-like response → emissivity switching amplitude |
| $|\langle \hat{\Delta n}\rangle|$ | Penalise | High charge imbalance → charge-density localised on one site → asymmetric switching, poor carrier transport |
| $|\langle \hat{D}\rangle|$ | Discount | High double occupancy → strongly correlated / Mott-insulating → switching arrested |

**Scope limitation:** $\mathcal{P}$ is a reduced-model heuristic. It captures the electronic character of the dopant pair in isolation. Macroscopic emissivity depends on additional factors including phonon coupling, defect concentration, interface geometry, and optical matrix elements — none of which are modelled here.

---

## 3. Variational Quantum Eigensolver (VQE)

### 3.1 Ansatz

A hardware-efficient ansatz (HEA) with $L = 2$ repetitions is used:

$$|\psi(\boldsymbol{\theta})\rangle = \prod_{l=1}^{L} \left[ \text{CNOT layer} \cdot \bigotimes_{q=0}^{3} R_y(\theta_{l,q}) \right] \cdot \bigotimes_{q=0}^{3} R_y(\theta_{0,q}) \cdot |1001\rangle$$

The initial state $|1001\rangle$ is a reference determinant with one electron on site 0 (spin-up) and one on site 1 (spin-down), consistent with the half-filling sector.

Total variational parameters: $4 \times (L+1) = 12$.

### 3.2 CNOT connectivity

```
q0 — q1 — q2 — q3
 \________________/   (additional long-range CX)
```

The ring CNOT structure ensures all-to-all entanglement generation within 4 qubits.

### 3.3 Optimization

Classical optimizer: COBYLA (Constrained Optimization BY Linear Approximations). Multi-start with 5 random initializations. Convergence criterion: $\|\boldsymbol{\theta}_{k+1} - \boldsymbol{\theta}_k\| < 10^{-5}$.

Achieved VQE error $|E_\mathrm{VQE} - E_0| < 7 \times 10^{-3}\ \mathrm{a.u.}$ for all candidates.

---

## 4. Quantum Hardware Measurement

### 4.1 Energy Estimation via Pauli Decomposition

The ground-state energy estimate from quantum hardware is obtained as:

$$E_\mathrm{QPU} \approx \sum_{k} c_k \langle \psi(\boldsymbol{\theta}^*) | \hat{P}_k | \psi(\boldsymbol{\theta}^*) \rangle$$

where $\hat{H} = \sum_k c_k \hat{P}_k$ is the Pauli decomposition of the Hamiltonian, and $\boldsymbol{\theta}^*$ are the VQE-optimised parameters.

Each $\langle \hat{P}_k \rangle$ is estimated from a separate measurement circuit with appropriate basis rotation:

$$\langle Z_q \rangle \leftarrow \text{measure } q \text{ in Z basis}$$
$$\langle X_q \rangle \leftarrow \text{apply } H \text{ gate, measure in Z basis}$$
$$\langle Y_q \rangle \leftarrow \text{apply } S^\dagger H, \text{measure in Z basis}$$

### 4.2 Shot Noise and Precision

For $M$ measurement shots, the statistical uncertainty on each Pauli expectation is:

$$\sigma(\langle \hat{P}_k \rangle) \sim \frac{1}{\sqrt{M}}$$

With $M = 200$ shots (H2-1e target), precision is $\pm 0.07$ per Pauli term. Energy precision is $\sum_k |c_k| / \sqrt{M} \approx 0.2\ \mathrm{a.u.}$ — sufficient for qualitative phase identification, insufficient for quantitative energy benchmarking. Shot count should be increased to $M \geq 10{,}000$ for chemical accuracy in follow-on experiments.

---

## 5. Sign Problem and QPU Advantage Territory

The fermionic sign problem arises in Quantum Monte Carlo when the partition function contains terms of alternating sign:

$$Z = \sum_\mathcal{C} s(\mathcal{C}) |\mathcal{W}(\mathcal{C})| \quad \text{with } s(\mathcal{C}) \in \{+1, -1\}$$

The average sign $\langle s \rangle \to 0$ exponentially in system volume and inverse temperature, causing signal-to-noise collapse. This occurs for:

- Repulsive Hubbard models away from half-filling ($\mu \neq 0$)
- Frustrated geometries (triangular, Kagomé lattices)
- Multi-orbital systems with Hund coupling

**Current status:** The $\mu = 0$, $N = 2$-particle sector used here is sign-problem-free. QPU-advantage territory is entered at $\mu \neq 0$ (finite carrier density) or in geometries with geometric frustration. The circuit architecture is identical; only the Hamiltonian parameters change.

---

## 6. Limitations and Future Work

| Limitation | Impact | Resolution pathway |
|---|---|---|
| Hamiltonian parameters not from DFT | Model is qualitative, not predictive | DFT + Wannier downfolding for each candidate pair |
| 4-qubit model is classically trivial | No quantum advantage at current scale | Scale to $N \geq 20$ orbitals (multi-site chains) |
| VQE shot count low ($M = 200$) | Energy precision $\sim 0.2$ a.u. | Increase to $M = 10{,}000$; use zero-noise extrapolation |
| No macroscopic emissivity model | Proxy score ≠ facade performance | Couple to DFT optical matrix elements + radiative transfer |
| Material not synthesized | Hypothesis only | Aerogel synthesis + XPS/EELS characterization of dopant sites |
| Single-site pair model | Neglects multi-pair correlations | Extend to Hubbard chain / 2D Hubbard on triangular lattice |
