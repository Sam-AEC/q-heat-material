# Theoretical Background: Q-HEAT Dopant-Pair Hamiltonian

## 1. Model Derivation

### 1.1 Physical System

We model the correlated electronic degrees of freedom of a transition-metal dopant pair (M0, M1) embedded in a high-entropy ceramic/aerogel host. Each metal center contributes two spin orbitals (spin-up and spin-down) to the active space, yielding a four-orbital, four-qubit system.

### 1.2 Second-Quantised Hamiltonian

The minimal Hubbard dimer Hamiltonian in second quantisation is:

$$\hat{H} = -t \sum_{\sigma \in \{\uparrow,\downarrow\}} \left( \hat{c}^\dagger_{0\sigma} \hat{c}_{1\sigma} + \mathrm{h.c.} \right) + U \sum_{i=0,1} \hat{n}_{i\uparrow} \hat{n}_{i\downarrow} + \frac{\delta}{2} \left( \hat{n}_0 - \hat{n}_1 \right) - \mu \sum_{i,\sigma} \hat{n}_{i\sigma}$$

The parameters are defined as follows:

| Symbol | Physical meaning | Typical range for 3d/4d TM pairs |
|---|---|---|
| $t$ | Inter-site hopping integral | 0.5 to 1.2 eV |
| $U$ | On-site Coulomb repulsion (Hubbard $U$) | 2.0 to 4.0 eV |
| $\delta$ | Site-energy asymmetry from local chemistry, vacancy, or strain | 0 to 0.5 eV |
| $\mu$ | Chemical potential for carrier doping | 0 by default |
| $\hat{n}_{i\sigma}$ | Number operator $\hat{c}^\dagger_{i\sigma} \hat{c}_{i\sigma}$ | not applicable |

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

The Hamiltonian conserves total particle number $\hat{N} = \sum_{i,\sigma} \hat{n}_{i\sigma}$ when $\mu = 0$, and total $S_z$ when $\delta = 0$. For the half-filling sector ($N = 2$ particles, $S_z = 0$), the ground state is typically a spin singlet with significant inter-site entanglement. This is the regime where quantum correlations are strongest and where the proxy score is most physically meaningful.

---

## 2. Q-HEAT Proxy Score

The proxy score is a dimensionless figure of merit for ranking dopant-pair electronic motifs:

$$\mathcal{P} = \frac{|\langle \hat{B} \rangle| \cdot \max\left(0,\ 1 - \tfrac{1}{2}|\langle \hat{\Delta n} \rangle|\right)}{1 + |\langle \hat{D} \rangle|}$$

where $\langle \hat{B} \rangle = \sum_\sigma \langle \hat{c}^\dagger_{0\sigma}\hat{c}_{1\sigma} + \mathrm{h.c.} \rangle$ is the bond order (inter-site delocalization), $\langle \hat{\Delta n} \rangle = \langle \hat{n}_0 \rangle - \langle \hat{n}_1 \rangle$ is the charge imbalance (site localization), and $\langle \hat{D} \rangle = \sum_i \langle \hat{n}_{i\uparrow}\hat{n}_{i\downarrow} \rangle$ is the double occupancy (correlation strength).

### Physical Rationale

| Factor | Role | Physical motivation |
|---|---|---|
| $|\langle \hat{B}\rangle|$ | Rewarded | High bond order corresponds to a delocalized ground state with metal-like response, which is the source of emissivity switching amplitude |
| $|\langle \hat{\Delta n}\rangle|$ | Penalized | High charge imbalance means charge density is localized on one site, producing asymmetric switching and poor carrier transport |
| $|\langle \hat{D}\rangle|$ | Discounted | High double occupancy indicates strong correlation approaching the Mott-insulating regime, where switching is arrested |

**Scope limitation.** $\mathcal{P}$ is a reduced-model heuristic. It captures the electronic character of the dopant pair in isolation. Macroscopic emissivity depends on additional factors including phonon coupling, defect concentration, interface geometry, and optical matrix elements, none of which are modelled here.

---

## 3. Variational Quantum Eigensolver

### 3.1 Ansatz

A hardware-efficient ansatz (HEA) with $L = 2$ repetitions is used:

$$|\psi(\boldsymbol{\theta})\rangle = \prod_{l=1}^{L} \left[ \text{CNOT layer} \cdot \bigotimes_{q=0}^{3} R_y(\theta_{l,q}) \right] \cdot \bigotimes_{q=0}^{3} R_y(\theta_{0,q}) \cdot |1001\rangle$$

The initial state $|1001\rangle$ is a reference determinant with one electron on site 0 (spin-up) and one on site 1 (spin-down), consistent with the half-filling sector. The total number of variational parameters is $4 \times (L+1) = 12$.

### 3.2 CNOT Connectivity

```
q0 -- q1 -- q2 -- q3
 \________________________/   (additional long-range CX)
```

The ring CNOT structure ensures all-to-all entanglement generation within 4 qubits.

### 3.3 Optimization

The classical optimizer is COBYLA (Constrained Optimization BY Linear Approximations) with a multi-start strategy using 5 random initializations. The convergence criterion is $\|\boldsymbol{\theta}_{k+1} - \boldsymbol{\theta}_k\| < 10^{-5}$. The achieved VQE error $|E_\mathrm{VQE} - E_0| < 7 \times 10^{-3}\ \mathrm{a.u.}$ for all candidates.

---

## 4. Quantum Hardware Measurement

### 4.1 Energy Estimation via Pauli Decomposition

The ground-state energy estimate from quantum hardware is obtained as:

$$E_\mathrm{QPU} \approx \sum_{k} c_k \langle \psi(\boldsymbol{\theta}^*) | \hat{P}_k | \psi(\boldsymbol{\theta}^*) \rangle$$

where $\hat{H} = \sum_k c_k \hat{P}_k$ is the Pauli decomposition of the Hamiltonian and $\boldsymbol{\theta}^*$ are the VQE-optimised parameters. Each $\langle \hat{P}_k \rangle$ is estimated from a separate measurement circuit with the appropriate basis rotation:

- $\langle Z_q \rangle$: measure qubit $q$ directly in the Z basis
- $\langle X_q \rangle$: apply H gate, then measure in the Z basis
- $\langle Y_q \rangle$: apply $S^\dagger H$, then measure in the Z basis

### 4.2 Shot Noise and Precision

For $M$ measurement shots, the statistical uncertainty on each Pauli expectation scales as $1/\sqrt{M}$. With $M = 200$ shots on the H2-1e target, the per-term precision is approximately $\pm 0.07$, and the total energy precision is $\sum_k |c_k| / \sqrt{M} \approx 0.2\ \mathrm{a.u.}$. This is sufficient for qualitative phase identification but not for quantitative energy benchmarking. A shot count of $M \geq 10{,}000$ combined with zero-noise extrapolation would be needed to approach chemical accuracy in follow-on experiments.

---

## 5. Sign Problem and QPU Advantage Territory

The fermionic sign problem arises in Quantum Monte Carlo when the partition function contains terms of alternating sign:

$$Z = \sum_\mathcal{C} s(\mathcal{C}) |\mathcal{W}(\mathcal{C})| \quad \text{with } s(\mathcal{C}) \in \{+1, -1\}$$

The average sign $\langle s \rangle$ decays to zero exponentially in system volume and inverse temperature, causing a collapse of the signal-to-noise ratio. This occurs for repulsive Hubbard models away from half-filling ($\mu \neq 0$), frustrated geometries such as triangular or Kagomé lattices, and multi-orbital systems with Hund coupling.

**Current status.** The $\mu = 0$, $N = 2$-particle sector used in this experiment is sign-problem-free. QPU-advantage territory is entered at $\mu \neq 0$ (finite carrier density) or in geometries with geometric frustration. The circuit architecture is identical for those cases; only the Hamiltonian parameters change.

---

## 6. Limitations and Future Work

| Limitation | Impact | Resolution pathway |
|---|---|---|
| Hamiltonian parameters not from DFT | Model is qualitative, not quantitatively predictive | DFT plus Wannier downfolding for each candidate pair |
| 4-qubit model is classically trivial | No quantum advantage at current scale | Scale to $N \geq 20$ orbitals (multi-site chains) |
| VQE shot count low at $M = 200$ | Energy precision approximately 0.2 a.u. | Increase to $M = 10{,}000$ and apply zero-noise extrapolation |
| No macroscopic emissivity model | Proxy score does not equal facade performance | Couple to DFT optical matrix elements and radiative transfer calculations |
| Material not synthesized | Hypothesis only | Aerogel synthesis plus XPS/EELS characterization of dopant sites |
| Single-site pair model | Neglects multi-pair correlations | Extend to a Hubbard chain or 2D Hubbard model on a triangular lattice |
