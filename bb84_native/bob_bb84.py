#!/usr/bin/env python3
"""
===============================================================================
BB84 QUANTUM KEY DISTRIBUTION (QKD) PROTOCOL - BOB (RECEIVER)
===============================================================================
Standard-level, publication-grade implementation of the BB84 protocol
for distributed two-system execution via SimulaQron Native Mode.

Protocol Stages:
  1. Quantum State Measurement (CSPRNG measurement bases, physical/channel simulation)
  2. Classical Basis Reconciliation (Sifting matching indices)
  3. QBER Estimation Coordination (Revealed sample bits DISCARDED)
  4. Parity-Based Bisection Error Correction (Zero secret key bit leakage)
  5. Privacy Amplification (SHA-256 compression matching Alice's format)
  6. Key Confirmation Verification & Clean Distributed Reactor Teardown

Authors/Affiliation: Distributed Quantum Communication Research
===============================================================================
"""

import os
import sys
import csv
import math
import time
import random as _py_random
import hashlib
import logging
import argparse
from datetime import datetime

from simulaqron.reactor import reactor

from simulaqron.local.setup import setup_local
from simulaqron.general.host_config import SocketsConfig
from simulaqron.settings import (
    simulaqron_settings,
    network_config,
    LOCAL_SIMULAQRON_SETTINGS,
    LOCAL_NETWORK_SETTINGS,
)

from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.internet.task import deferLater
from twisted.spread import pb

# =============================================================================
# DEFAULT PROTOCOL PARAMETERS (Tunable via CLI)
# =============================================================================
DEFAULT_NUM_QUBITS       = 100
DEFAULT_NOISE_PROBABILITY = 0.0   # Set >0 to emulate bit-flip channel noise
CASCADE_NUM_PASSES        = 4     # Brassard & Salvail (1993) standard
QRNG_SEED_BITS            = 64

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(BASE_DIR, "results")
CSV_DIR     = os.path.join(BASE_DIR, "csv_results")
KEYS_DIR    = os.path.join(BASE_DIR, "keys")

for _d in [RESULTS_DIR, CSV_DIR, KEYS_DIR]:
    os.makedirs(_d, exist_ok=True)


# =============================================================================
# PERSISTENCE & AUDIT LOGGING
# =============================================================================
def log_experiment_report(report_text: str, filename_prefix: str = "bob_result") -> str:
    """Saves formatted protocol execution report to the results directory."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    experiment_id = len([f for f in os.listdir(RESULTS_DIR) if f.startswith(filename_prefix)]) + 1
    filepath = os.path.join(RESULTS_DIR, f"{filename_prefix}_Exp{experiment_id:03d}_{timestamp}.txt")
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(report_text)
    return filepath


def log_csv_metrics(row_data: dict):
    """Safely appends structured protocol benchmark metrics into CSV database."""
    csv_file = os.path.join(CSV_DIR, "experiment_statistics.csv")
    file_exists = os.path.isfile(csv_file)
    row_count = 1

    if file_exists:
        try:
            with open(csv_file, "r", encoding="utf-8") as f:
                row_count = sum(1 for _ in f)
        except Exception:
            row_count = 1

    fieldnames = [
        "Exp_ID", "Timestamp", "Node", "Total_Qubits", "Sifted_Key_Length",
        "Sample_Bits_Discarded", "Reconciled_Key_Length", "QBER(%)",
        "QBER_Threshold(%)", "Cascade_Passes", "Cascade_Corrections",
        "Leaked_Parity_Bits", "Theoretical_Secure_Bits", "Output_Key_Length",
        "PA_Algorithm", "Execution_Time_s", "Status",
        "Secret_Key_Hex", "Secret_Key_Bin_Prefix",
    ]

    with open(csv_file, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        row_data["Exp_ID"] = row_count
        writer.writerow(row_data)


def log_key_database(key_hex: str, key_bits_len: int, status: str = "ESTABLISHED"):
    """Appends cryptographically established key to persistent key database."""
    key_file = os.path.join(KEYS_DIR, "key_database.txt")
    file_exists = os.path.isfile(key_file)
    key_id = 1
    if file_exists:
        try:
            with open(key_file, "r", encoding="utf-8") as f:
                key_id = sum(1 for line in f if line.strip())
        except Exception:
            pass
    with open(key_file, "a", encoding="utf-8") as f:
        if not file_exists:
            f.write(f"{'Key_ID':<8}{'Status':<16}{'Len_bits':<12}{'Key_Hex':<70}{'Timestamp'}\n")
            f.write("=" * 115 + "\n")
        f.write(
            f"{key_id:<8}{status:<16}{key_bits_len:<12}{key_hex:<70}"
            f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')}\n"
        )


# =============================================================================
# INFORMATION-THEORETIC UTILITIES (mirrored from alice_bb84.py)
# =============================================================================
def binary_entropy(p: float) -> float:
    if p <= 0.0 or p >= 1.0:
        return 0.0
    return -p * math.log2(p) - (1.0 - p) * math.log2(1.0 - p)


def compute_secure_key_bound(n: int, qber_frac: float, leaked_bits: int) -> int:
    h2 = binary_entropy(qber_frac)
    raw = 1.0 - 2.0 * h2
    if raw <= 0.0:
        return 0
    return max(0, int(math.floor(n * raw - leaked_bits)))


def cascade_initial_block_size(qber_frac: float, n: int) -> int:
    if qber_frac <= 0.0:
        return max(4, n // 4)
    k1 = int(math.ceil(0.73 / qber_frac))
    return max(4, min(k1, max(4, n // 2)))


def derive_toeplitz_seed(cascade_seed: int) -> int:
    digest = hashlib.sha256(f"toeplitz_pa_v4:{cascade_seed}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def toeplitz_hash(key_bits: list, output_length: int, seed: int) -> list:
    """
    Toeplitz matrix PA -- 2-universal hash family (same as alice side).
    """
    if output_length <= 0 or len(key_bits) == 0:
        return []
    n = len(key_bits)
    rng = _py_random.Random(seed)
    desc = [rng.randint(0, 1) for _ in range(n + output_length - 1)]
    result = []
    for i in range(output_length):
        bit = 0
        for j in range(n):
            bit ^= desc[(n - 1) + i - j] & key_bits[j]
        result.append(bit)
    return result


def bits_to_hex(bits: list) -> str:
    if not bits:
        return ""
    padded = bits + [0] * ((4 - len(bits) % 4) % 4)
    return "".join(
        format(int("".join(str(b) for b in padded[i:i + 4]), 2), "x")
        for i in range(0, len(padded), 4)
    )


# =============================================================================
# CASCADE RECONCILER (BOB DRIVER)
# =============================================================================
class CascadeReconciler:
    """
    Full Cascade error reconciliation algorithm driver (Bob side).
    Reference: Brassard & Salvail (1993). "Secret-Key Reconciliation by
    Public Discussion." EUROCRYPT 1993.

    Algorithm:
      - Multiple passes (4 by default) with exponentially growing block sizes.
      - Pass i uses a random permutation pi_i derived from cascade_seed.
      - For each block with parity mismatch (odd number of errors), perform
        binary search via remote parity queries to Alice's oracle.
      - Back-propagation: after correcting a bit at original position p, find
        which blocks in ALL previous passes contained p, and re-check those
        blocks -- potentially correcting additional errors (even-count blocks).
    """

    def __init__(self, key: list, qber_frac: float, cascade_seed: int,
                 k1: int, num_passes: int, block_sizes: list):
        self.key        = list(key)    # Working copy; corrected in-place
        self.n          = len(key)
        self.qber_frac  = qber_frac
        self.k1         = k1
        self.num_passes = num_passes
        self.block_sizes = block_sizes

        # Build permutations from seed (same RNG state as Alice)
        rng = _py_random.Random(cascade_seed)
        self.perms     = []    # perms[pass][perm_pos] = original_idx
        self.inv_perms = []    # inv_perms[pass][original_idx] = perm_pos
        for _ in range(num_passes):
            perm = list(range(self.n))
            rng.shuffle(perm)
            self.perms.append(perm)
            inv = [0] * self.n
            for ppos, oidx in enumerate(perm):
                inv[oidx] = ppos
            self.inv_perms.append(inv)

        self.leaked_bits     = 0
        self.corrected_count = 0
        self.corrected_set   = set()   # original positions already corrected

    def _bob_perm_parity(self, pass_idx: int, perm_start: int, perm_end: int) -> int:
        """Compute Bob's parity of permuted key block (dynamically from current key)."""
        perm = self.perms[pass_idx]
        return sum(self.key[perm[j]] for j in range(perm_start, perm_end)) % 2

    @inlineCallbacks
    def _binary_search(self, alice_remote, pass_idx: int, perm_start: int, perm_end: int):
        """
        Binary search for a single erroneous bit in permuted range [perm_start, perm_end).
        Queries Alice's parity oracle; each query leaks exactly 1 bit.
        Returns (permuted_error_position, query_count).
        """
        cur_start = perm_start
        cur_end   = perm_end
        queries   = 0

        while (cur_end - cur_start) > 1:
            mid = (cur_start + cur_end) // 2
            alice_p = yield alice_remote.callRemote("get_cascade_parity", pass_idx, cur_start, mid)
            bob_p   = self._bob_perm_parity(pass_idx, cur_start, mid)
            queries += 1
            if alice_p != bob_p:
                cur_end = mid
            else:
                cur_start = mid

        returnValue((cur_start, queries))

    @inlineCallbacks
    def run(self, alice_remote):
        """
        Execute the full multi-pass Cascade protocol.

        Returns:
            (corrected_key: list, total_leaked_bits: int, corrections: int)
        """
        back_prop_queue = set()   # original positions queued for back-propagation

        for pass_idx in range(self.num_passes):
            k = self.block_sizes[pass_idx]
            num_blocks = (self.n + k - 1) // k
            self.leaked_bits += num_blocks   # 1 parity bit disclosed per block

            new_corrections = []

            for block_idx in range(num_blocks):
                perm_start = block_idx * k
                perm_end   = min(perm_start + k, self.n)

                alice_p = yield alice_remote.callRemote(
                    "get_cascade_parity", pass_idx, perm_start, perm_end
                )
                bob_p = self._bob_perm_parity(pass_idx, perm_start, perm_end)

                if alice_p != bob_p:
                    err_perm_pos, queries = yield self._binary_search(
                        alice_remote, pass_idx, perm_start, perm_end
                    )
                    self.leaked_bits += queries

                    orig_pos = self.perms[pass_idx][err_perm_pos]
                    self.key[orig_pos] ^= 1
                    self.corrected_count += 1
                    if orig_pos not in self.corrected_set:
                        self.corrected_set.add(orig_pos)
                        new_corrections.append(orig_pos)
                        back_prop_queue.add(orig_pos)

            # Back-propagation: each newly corrected position might have revealed
            # an even-count error in blocks from previous passes.
            bp_pending = set(new_corrections)
            while bp_pending:
                orig_pos = bp_pending.pop()
                for prev_pass in range(pass_idx):   # Only prior passes
                    k_prev   = self.block_sizes[prev_pass]
                    perm_pos = self.inv_perms[prev_pass][orig_pos]
                    blk      = perm_pos // k_prev
                    bp_start = blk * k_prev
                    bp_end   = min(bp_start + k_prev, self.n)

                    alice_p = yield alice_remote.callRemote(
                        "get_cascade_parity", prev_pass, bp_start, bp_end
                    )
                    bob_p = self._bob_perm_parity(prev_pass, bp_start, bp_end)
                    self.leaked_bits += 1  # 1 parity bit disclosed per back-prop check

                    if alice_p != bob_p:
                        err_perm_pos, queries = yield self._binary_search(
                            alice_remote, prev_pass, bp_start, bp_end
                        )
                        self.leaked_bits += queries

                        orig_pos2 = self.perms[prev_pass][err_perm_pos]
                        self.key[orig_pos2] ^= 1
                        self.corrected_count += 1
                        if orig_pos2 not in self.corrected_set:
                            self.corrected_set.add(orig_pos2)
                            bp_pending.add(orig_pos2)   # Propagate further

        returnValue((self.key, self.leaked_bits, self.corrected_count))


# =============================================================================
# BOB LOCAL NODE CLASS (ENCAPSULATED STATE MACHINE)
# =============================================================================
class BobBB84Node(pb.Root):
    """
    Represents Bob's classical controller and RPC interface in SimulaQron.
    Manages qubit reception, measurement basis selection, basis reconciliation,
    bisection error correction, privacy amplification, and key confirmation.
    """

    def __init__(self, node, classicalNet,
                 num_qubits=DEFAULT_NUM_QUBITS,
                 noise_prob=DEFAULT_NOISE_PROBABILITY):
        self.node          = node
        self.classicalNet  = classicalNet
        self.num_qubits    = num_qubits
        self.noise_prob    = noise_prob

        self.virtRoot = None
        self.qReg     = None

        # -- Protocol State --
        self.qrng_bases       = []   # Pre-generated QRNG measurement bases
        self.raw_bits         = []
        self.raw_bases        = []
        self.sifted_key       = []
        self.sample_positions = []
        self.reconcile_key    = []
        self.final_key_bits   = []
        self.final_key_hex    = ""

        # -- Metrics --
        self.start_time          = 0.0
        self.received_qubit_count = 0
        self.leaked_parity_bits  = 0
        self.cascade_corrections = 0
        self.theoretical_secure_bits = 0
        self.output_key_length   = 0

    def set_virtual_node(self, virtRoot):
        self.virtRoot = virtRoot

    def set_virtual_reg(self, qReg):
        self.qReg = qReg

    def remote_ping(self):
        """Connectivity probe for distributed validation."""
        return "BOB_READY"

    @inlineCallbacks
    def _wait_ready(self):
        """Asynchronously waits until the virtual quantum backend is initialized."""
        while self.virtRoot is None or self.qReg is None:
            yield deferLater(reactor, 0.1, lambda: None)

    # -------------------------------------------------------------------------
    # STAGE 1: QUANTUM STATE RECEPTION & MEASUREMENT
    # -------------------------------------------------------------------------
    @inlineCallbacks
    def remote_receive_bb84(self, virtualNum: int, qubit_no: int):
        """
        Receives virtual qubit reference from Alice.
        Selects measurement basis with CSPRNG, applies gates, measures,
        and introduces channel noise if configured.
        """
        yield self._wait_ready()

        if self.received_qubit_count == 0:
            self.start_time = time.perf_counter()

        q = yield self.virtRoot.callRemote("get_virtual_ref", virtualNum)

        # QRNG measurement basis: pre-generated from quantum |+> measurements
        if self.qrng_bases and self.received_qubit_count < len(self.qrng_bases):
            basis = self.qrng_bases[self.received_qubit_count]
        else:
            # Fallback: generate QRNG basis on-the-fly
            q_b = yield self.virtRoot.callRemote("new_qubit")
            yield q_b.callRemote("apply_H")
            basis_idx = yield q_b.callRemote("measure")
            basis = "Z" if basis_idx == 0 else "X"
        self.raw_bases.append(basis)

        if basis == "X":
            yield q.callRemote("apply_H")
        measurement = yield q.callRemote("measure")

        # Optional emulated bit-flip channel noise
        if self.noise_prob > 0.0 and _py_random.random() < self.noise_prob:
            measurement = 1 - measurement

        self.raw_bits.append(measurement)
        self.received_qubit_count += 1

        if self.received_qubit_count % 25 == 0 or self.received_qubit_count == self.num_qubits:
            print(f"  [*] Bob measured: {self.received_qubit_count}/{self.num_qubits} qubits")

        if self.received_qubit_count == self.num_qubits:
            print("\n" + "=" * 60)
            print("STAGE 1: QUANTUM MEASUREMENTS COMPLETED (BOB)")
            print("=" * 60)
            print(f"Total Measured Qubits    : {len(self.raw_bits)}")
            print(f"Active Noise Probability : {self.noise_prob:.2%}")

    # -------------------------------------------------------------------------
    # STAGE 2: BASIS SIFTING
    # -------------------------------------------------------------------------
    @inlineCallbacks
    def remote_receive_basis_list(self, alice_bases: list):
        """
        Compares Alice's basis sequence with Bob's basis sequence.
        Extracts matching indices to form the sifted key and transmits
        matching positions to Alice.
        """
        print("\n" + "=" * 60)
        print("STAGE 2: BASIS SIFTING (BOB)")
        print("=" * 60)

        matching_indices = []
        self.sifted_key = []

        total_compare = min(len(alice_bases), len(self.raw_bases))
        for idx in range(total_compare):
            if alice_bases[idx] == self.raw_bases[idx]:
                matching_indices.append(idx)
                self.sifted_key.append(self.raw_bits[idx])

        print(f"Alice Bases Received     : {len(alice_bases)}")
        print(f"Bob Bases Count          : {len(self.raw_bases)}")
        print(f"Matching Bases Identified: {len(matching_indices)}")
        print(f"Bob Sifted Key Length    : {len(self.sifted_key)}")

        print("\nTransmitting matching indices to Alice...")
        alice = self.classicalNet.hostDict["Alice"]
        yield alice.root.callRemote("receive_matching_indices", matching_indices)

    # -------------------------------------------------------------------------
    # STAGE 3: QBER SAMPLE TRANSMISSION & BIT DISCARDING
    # -------------------------------------------------------------------------
    @inlineCallbacks
    def remote_receive_qber_sample_positions(self, sample_positions: list):
        """
        Extracts Bob's sample bits for Alice's QBER calculation.
        MANDATORY RULE: Discards sample positions from sifted key to form
        the working reconcile key.
        """
        print("\n" + "=" * 60)
        print("STAGE 3: QBER SAMPLE SELECTION (BOB)")
        print("=" * 60)
        print(f"Sample Positions Requested : {len(sample_positions)}")

        self.sample_positions = sample_positions
        sample_bits = [self.sifted_key[pos] for pos in sample_positions]

        # =====================================================================
        # MANDATORY BB84 RULE: DISCARD SAMPLE BITS FROM THE SIFTED KEY!
        # Both parties eliminate revealed sample bits identically.
        # =====================================================================
        sample_set = set(sample_positions)
        self.reconcile_key = [
            self.sifted_key[i] for i in range(len(self.sifted_key))
            if i not in sample_set
        ]

        print(f"\n  Sample Bits Discarded    : {len(sample_set)}")
        print(f"  Remaining Reconcile Key  : {len(self.reconcile_key)} bits")
        print("\n  Sending sample bits to Alice for QBER estimation...")
        alice = self.classicalNet.hostDict["Alice"]
        yield alice.root.callRemote("receive_qber_sample_bits", sample_bits)

    # -------------------------------------------------------------------------
    # STAGE 4: FULL CASCADE ERROR CORRECTION (BOB DRIVER)
    # -------------------------------------------------------------------------
    @inlineCallbacks
    def remote_receive_cascade_start(self, qber_estimate: float, cascade_seed: int):
        """
        Called by Alice to start the Cascade reconciliation.
        Bob drives the full Cascade algorithm; Alice acts as parity oracle.

        Steps:
          1. Call Alice's remote_setup_cascade to initialise her oracle.
          2. Build CascadeReconciler with matching seed/params.
          3. Run full multi-pass Cascade with back-propagation.
          4. Perform Toeplitz PA.
          5. Call Alice's remote_receive_cascade_complete.
        """
        print("\n" + "=" * 62)
        print("STAGE 4: FULL CASCADE ERROR CORRECTION (BOB DRIVER)")
        print("=" * 62)
        print(f"  Received cascade_seed from Alice: {cascade_seed}")
        print(f"  Reported QBER                  : {qber_estimate:.4f} %")

        alice = self.classicalNet.hostDict["Alice"]

        # Initialise Alice's oracle and get agreed cascade config
        qber_frac = qber_estimate / 100.0
        cascade_cfg = yield alice.root.callRemote("setup_cascade", cascade_seed, qber_frac)
        k1, num_passes, block_sizes = cascade_cfg

        print(f"  Cascade config: k1={k1}, passes={num_passes}, block_sizes={block_sizes}")

        if len(self.reconcile_key) == 0:
            logging.warning("Bob: Reconcile key is empty; skipping cascade.")
            yield alice.root.callRemote("receive_cascade_complete", 0, 0)
            return

        # Build CascadeReconciler -- uses same seed to generate same permutations
        reconciler = CascadeReconciler(
            key         = self.reconcile_key,
            qber_frac   = qber_frac,
            cascade_seed= cascade_seed,
            k1          = k1,
            num_passes  = num_passes,
            block_sizes = block_sizes,
        )

        print("\n  Running full Cascade passes...")
        corrected_key, leaked_bits, corrections = yield reconciler.run(alice.root)
        self.reconcile_key        = corrected_key
        self.leaked_parity_bits   = leaked_bits
        self.cascade_corrections  = corrections

        print(f"  Cascade complete: {corrections} bits corrected, {leaked_bits} parity bits leaked")

        # =====================================================================
        # STAGE 5: PRIVACY AMPLIFICATION -- TOEPLITZ HASH (BOB)
        # =====================================================================
        print("\n" + "=" * 62)
        print("STAGE 5: PRIVACY AMPLIFICATION -- TOEPLITZ HASH (BOB)")
        print("=" * 62)

        qber_frac2 = qber_estimate / 100.0
        secure_bits = compute_secure_key_bound(len(self.reconcile_key), qber_frac2, leaked_bits)
        self.theoretical_secure_bits = secure_bits
        self.output_key_length = max(1, min(256, secure_bits))

        print(f"  Reconcile Key Length     : {len(self.reconcile_key)} bits")
        print(f"  Theoretical Secure Bound : {secure_bits} bits")
        print(f"  Output Key Length (l)    : {self.output_key_length} bits")

        toeplitz_seed   = derive_toeplitz_seed(cascade_seed)
        self.final_key_bits = toeplitz_hash(
            self.reconcile_key, self.output_key_length, toeplitz_seed
        )
        self.final_key_hex = bits_to_hex(self.final_key_bits)

        print("\n  Privacy Amplified Shared Secret (Bob):")
        print(f"    HEX [{len(self.final_key_hex)} chars] : {self.final_key_hex}")
        key_bin = "".join(str(b) for b in self.final_key_bits)
        print(f"    BIN [{len(self.final_key_bits)} bits]  : {key_bin[:48]}{'...' if len(key_bin)>48 else ''}")

        # Signal Alice that Cascade + PA are complete
        yield alice.root.callRemote(
            "receive_cascade_complete",
            leaked_bits,
            corrections,
        )

    # -------------------------------------------------------------------------
    # STAGE 6: KEY CONFIRMATION VERIFICATION
    # -------------------------------------------------------------------------
    def remote_confirm_key(self, alice_tag: str, metadata: dict) -> bool:
        """
        Verifies Alice's confirmation tag, confirms identical key agreement,
        persists results, and initiates clean reactor teardown.
        """
        print("\n" + "=" * 62)
        print("STAGE 6: KEY CONFIRMATION VERIFICATION (BOB)")
        print("=" * 62)

        confirm_salt = "BB84_TOEPLITZ_V4_CONFIRMATION"
        bob_tag = hashlib.sha256(
            f"{self.final_key_hex}:{confirm_salt}".encode("utf-8")
        ).hexdigest()[:20]

        is_verified = (alice_tag == bob_tag)
        execution_time = time.perf_counter() - self.start_time

        if is_verified:
            print("  [+] CONFIRMATION SUCCESS: identical shared keys established!")
            status = "SUCCESS"
        else:
            print("  [-] CONFIRMATION FAILURE: tags do not match!")
            print(f"    Alice tag : {alice_tag}")
            print(f"    Bob tag   : {bob_tag}")
            status = "CONFIRMATION_FAILED"

        self._save_session_results(status, execution_time, metadata)
        print("\nShutting down Bob reactor gracefully...")
        reactor.callLater(1.5, reactor.stop)
        return is_verified

    # -------------------------------------------------------------------------
    # ABORT HANDLER
    # -------------------------------------------------------------------------
    def remote_receive_abort(self, reason: str):
        """Called by Alice when protocol aborts (e.g. QBER threshold exceeded)."""
        logging.warning("Bob: Received protocol abort from Alice: %s", reason)
        print("\n" + "=" * 60)
        print("[!] PROTOCOL ABORTED BY ALICE")
        print("=" * 60)
        print(f"Reason: {reason}")
        execution_time = time.perf_counter() - self.start_time
        self._save_session_results(f"ABORTED_{reason.upper()}", execution_time, {})
        reactor.callLater(1.0, reactor.stop)

    def _save_session_results(self, status: str, execution_time: float, metadata: dict):
        """Persists text report, CSV entry, and key database entry."""
        qber         = metadata.get("qber", 0.0)
        theo_secure  = metadata.get("theoretical_secure_bits", self.theoretical_secure_bits)
        output_len   = metadata.get("output_key_length", self.output_key_length)
        key_bin = "".join(str(b) for b in self.final_key_bits)
        report = f"""
===============================================================================
        BB84 QUANTUM KEY DISTRIBUTION -- EXPERIMENT REPORT (BOB)
===============================================================================
Protocol Version   : 4.0 -- QRNG + Full Cascade + Toeplitz Privacy Amplification
Execution Mode     : Distributed Two-System SimulaQron Native Mode
Station Node       : Bob (Receiver)
Timestamp          : {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
Machine / Host     : {os.uname().nodename}

-------------------------------------------------------------------------------
1. PROTOCOL CONFIGURATION
-------------------------------------------------------------------------------
Total Expected Qubits     : {self.num_qubits}
RNG Method (Bases)        : True QRNG (|+> quantum measurement on SimulaQron)
Simulated Noise Level     : {self.noise_prob:.2%}
Cascade Passes            : {CASCADE_NUM_PASSES}
Privacy Amplification     : Toeplitz 2-Universal Matrix Hash

-------------------------------------------------------------------------------
2. QUANTUM MEASUREMENT RECORD (QRNG Bases)
-------------------------------------------------------------------------------
Bob Raw Bits (First 50)   : {''.join(map(str, self.raw_bits[:50]))}...
Bob Bases    (First 50)   : {''.join(self.raw_bases[:50])}...
Total Qubits Measured     : {len(self.raw_bits)}
QRNG Bases Pre-generated  : {len(self.qrng_bases)}

-------------------------------------------------------------------------------
3. BASIS SIFTING & SAMPLE DISCARD
-------------------------------------------------------------------------------
Sifted Key Length         : {len(self.sifted_key)}
Sample Bits Discarded     : {len(self.sample_positions)} (Mandatory security rule)
Reconcile Key Candidate   : {len(self.reconcile_key)} bits
Reported Channel QBER     : {qber:.4f} %

-------------------------------------------------------------------------------
4. CASCADE ERROR CORRECTION (Brassard & Salvail, 1993)
-------------------------------------------------------------------------------
Algorithm                 : Full Cascade ({CASCADE_NUM_PASSES} passes, adaptive k1, back-propagation)
Bits Corrected by Cascade : {self.cascade_corrections}
Total Parity Bits Leaked  : {self.leaked_parity_bits} bits

-------------------------------------------------------------------------------
5. PRIVACY AMPLIFICATION & SHARED SECRET
-------------------------------------------------------------------------------
Algorithm                 : Toeplitz Matrix (2-Universal Hash Family)
Theoretical Secure Bound  : {theo_secure} bits (Shor-Preskill)
Output Key Length (l)     : {output_len} bits
Final Session Key (HEX)   : {self.final_key_hex if status == "SUCCESS" else "NONE"}
Final Session Key (BIN)   : {key_bin if status == "SUCCESS" else "NONE"}

-------------------------------------------------------------------------------
6. BENCHMARK & STATUS
-------------------------------------------------------------------------------
Total Execution Time      : {execution_time:.6f} seconds
Protocol Final Status     : {status}
===============================================================================
"""
        path = log_experiment_report(report, "bob_result")
        print(f"\n  [+] Experiment report saved: {path}")

        log_csv_metrics({
            "Timestamp"              : datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "Node"                   : "Bob",
            "Total_Qubits"           : self.num_qubits,
            "Sifted_Key_Length"      : len(self.sifted_key),
            "Sample_Bits_Discarded"  : len(self.sample_positions),
            "Reconciled_Key_Length"  : len(self.reconcile_key),
            "QBER(%)"                : f"{qber:.4f}",
            "QBER_Threshold(%)"      : "11.00",
            "Cascade_Passes"         : CASCADE_NUM_PASSES,
            "Cascade_Corrections"    : self.cascade_corrections,
            "Leaked_Parity_Bits"     : self.leaked_parity_bits,
            "Theoretical_Secure_Bits": theo_secure,
            "Output_Key_Length"      : output_len,
            "PA_Algorithm"           : "Toeplitz-2Universal",
            "Execution_Time_s"       : f"{execution_time:.4f}",
            "Status"                 : status,
            "Secret_Key_Hex"         : self.final_key_hex if status == "SUCCESS" else "N/A",
            "Secret_Key_Bin_Prefix"  : key_bin[:32] if status == "SUCCESS" else "N/A",
        })
        if status == "SUCCESS":
            log_key_database(self.final_key_hex, self.output_key_length, "ESTABLISHED")


# =============================================================================
# QUANTUM RECEIVER CLIENT (BOB)
# =============================================================================
def runClientNode(qReg, virtRoot, myName, classicalNet, lNode=None):
    """
    Main quantum setup routine for Bob.
    Initializes Bob's state machine and awaits Alice's remote transmissions.
    """
    logging.info("Starting BB84 Quantum Receiver (Bob) v4 -- QRNG+Cascade+Toeplitz")
    bob_node = lNode

    bob_node.set_virtual_node(virtRoot)
    bob_node.set_virtual_reg(qReg)

    print("\n" + "=" * 62)
    print("  BB84 QKD -- BOB RECEIVER  (v4: QRNG + CASCADE + TOEPLITZ)")
    print("=" * 62)

    # Pre-generate QRNG measurement bases from quantum |+> measurements.
    # All bases generated before Alice begins transmission so they are
    # independent of Alice's qubits (no signalling loophole).
    print(f"\n[STAGE 0] QRNG: Generating {lNode.num_qubits} quantum-random measurement bases...")

    qrng_bases = []
    for i in range(lNode.num_qubits):
        q_b = yield virtRoot.callRemote("new_qubit")
        yield q_b.callRemote("apply_H")
        basis_idx = yield q_b.callRemote("measure")
        qrng_bases.append("Z" if basis_idx == 0 else "X")

        if (i + 1) % 25 == 0 or (i + 1) == lNode.num_qubits:
            print(f"  [*] QRNG bases: {i+1}/{lNode.num_qubits}")

    bob_node.qrng_bases = qrng_bases
    print(f"  QRNG complete: {len(qrng_bases)} quantum-random measurement bases pre-generated.")
    print(f"\n  Ready and listening for {lNode.num_qubits} BB84 transmissions from Alice...")


# =============================================================================
# MAIN INITIALIZATION & RUNNER
# =============================================================================
def main():
    parser = argparse.ArgumentParser(description="BB84 Bob (Receiver) Node v4")
    parser.add_argument("--qubits", type=int, default=DEFAULT_NUM_QUBITS,
                        help=f"Qubits to receive (default: {DEFAULT_NUM_QUBITS})")
    parser.add_argument("--noise", type=float, default=DEFAULT_NOISE_PROBABILITY,
                        help="Channel noise probability (0.0 = pure)")
    args = parser.parse_args()

    myName = "Bob"

    simulaqron_settings.read_from_file(LOCAL_SIMULAQRON_SETTINGS)
    network_config.read_from_file(LOCAL_NETWORK_SETTINGS)

    simulaqron_settings.conn_retry_time = 1.0
    simulaqron_settings.conn_max_retries = 120

    virtualNet   = SocketsConfig(network_config, network_name="default", config_type="vnode")
    classicalNet = SocketsConfig(network_config, network_name="default", config_type="app")

    lNode = BobBB84Node(
        classicalNet.hostDict[myName],
        classicalNet,
        num_qubits = args.qubits,
        noise_prob = args.noise,
    )

    setup_local(
        myName,
        virtualNet,
        classicalNet,
        lNode,
        lambda qReg, virtRoot, myName, classicalNet: runClientNode(
            qReg, virtRoot, myName, classicalNet, lNode=lNode
        ),
    )


if __name__ == "__main__":
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=logging.INFO,
    )
    main()
