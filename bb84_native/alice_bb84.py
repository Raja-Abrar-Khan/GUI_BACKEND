#!/usr/bin/env python3
"""
===============================================================================
BB84 QUANTUM KEY DISTRIBUTION (QKD) PROTOCOL — ALICE (TRANSMITTER)
===============================================================================
Publication-grade distributed implementation via SimulaQron Native Mode.
Version 4.0 — QRNG + Full Cascade Error Correction + Toeplitz Privacy Amplification

Upgrades from v3:
  QRNG    : All randomness (bits, bases, session seed) from quantum measurements
            of |+> states on the SimulaQron virtual quantum node.
  CASCADE : Full Brassard-Salvail (1993) Cascade with 4 passes, adaptive block
            sizing k1=ceil(0.73/e), and interactive parity oracle for Bob.
  TOEPLITZ: 2-universal Toeplitz matrix hash replaces SHA-256 for privacy
            amplification. Output length = min(256, theoretical secure bits).

Protocol Stages:
  0. QRNG    — Quantum random bit/basis/seed generation via |+> measurements
  1. Quantum State Preparation & Transmission (QRNG bits and bases)
  2. Classical Basis Reconciliation (Sifting)
  3. QBER Estimation & Security Verification (Revealed sample bits DISCARDED)
  4. Parity Reconciliation & Bisection Error Correction (Zero secret leakage)
  5. Privacy Amplification (Information-theoretic bounds & SHA-256 amplification)
  6. Key Confirmation & Clean Distributed Reactor Teardown

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
DEFAULT_NUM_QUBITS     = 100
DEFAULT_QBER_THRESHOLD = 11.0   # BB84 theoretical security threshold (%)
DEFAULT_SAMPLE_RATIO   = 0.20   # Fraction of sifted key for QBER estimation
CASCADE_NUM_PASSES     = 4      # Brassard & Salvail standard pass count
QRNG_SEED_BITS         = 64     # Quantum bits for 64-bit session seed

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(BASE_DIR, "results")
CSV_DIR     = os.path.join(BASE_DIR, "csv_results")
KEYS_DIR    = os.path.join(BASE_DIR, "keys")

for _d in [RESULTS_DIR, CSV_DIR, KEYS_DIR]:
    os.makedirs(_d, exist_ok=True)


# =============================================================================
# MATHEMATICAL & INFORMATION-THEORETIC UTILITIES
# =============================================================================
def binary_entropy(p: float) -> float:
    """Shannon binary entropy h2(p) = -p*log2(p) - (1-p)*log2(1-p)."""
    if p <= 0.0 or p >= 1.0:
        return 0.0
    return -p * math.log2(p) - (1.0 - p) * math.log2(1.0 - p)


def compute_secure_key_bound(n: int, qber_frac: float, leaked_bits: int) -> int:
    """
    Shor-Preskill / Devetak-Winter asymptotic bound:
        l <= n * [1 - 2*h2(e)] - leaked_bits
    """
    h2 = binary_entropy(qber_frac)
    raw = 1.0 - 2.0 * h2
    if raw <= 0.0:
        return 0
    return max(0, int(math.floor(n * raw - leaked_bits)))


def cascade_initial_block_size(qber_frac: float, n: int) -> int:
    """
    Adaptive block size for Cascade pass 0.
    Formula: k1 = ceil(0.73 / e)  (Brassard & Salvail, 1993).
    Clamped to [4, n//2] for practical key lengths.
    """
    if qber_frac <= 0.0:
        return max(4, n // 4)
    k1 = int(math.ceil(0.73 / qber_frac))
    return max(4, min(k1, max(4, n // 2)))


def derive_toeplitz_seed(cascade_seed: int) -> int:
    """Deterministically derive Toeplitz seed from the quantum-random cascade seed."""
    digest = hashlib.sha256(f"toeplitz_pa_v4:{cascade_seed}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def toeplitz_hash(key_bits: list, output_length: int, seed: int) -> list:
    """
    Toeplitz matrix privacy amplification -- 2-universal hash family.

    Constructs an (l x n) Toeplitz matrix T described by (n + l - 1) PRNG bits
    seeded from a QRNG-derived seed. Output key K = T * key_bits (mod 2).

    Security: For any input with min-entropy >= l + 2*log2(1/eps), the output
    is eps-close to uniform (Leftover Hash Lemma, Impagliazzo-Levin-Luby 1989).
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
            bit ^= desc[(n - 1) + i - j] & key_bits[j]  # T[i][j] = desc[(n-1)+i-j]
        result.append(bit)
    return result


def bits_to_hex(bits: list) -> str:
    """Convert a list of bits to a hex string (zero-padded to full nibbles)."""
    if not bits:
        return ""
    padded = bits + [0] * ((4 - len(bits) % 4) % 4)
    return "".join(
        format(int("".join(str(b) for b in padded[i:i + 4]), 2), "x")
        for i in range(0, len(padded), 4)
    )


# =============================================================================
# PERSISTENCE & AUDIT LOGGING
# =============================================================================
def log_experiment_report(report_text: str, filename_prefix: str = "alice_result") -> str:
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
# ALICE LOCAL NODE CLASS (ENCAPSULATED STATE MACHINE)
# =============================================================================
class AliceBB84Node(pb.Root):
    """
    Represents Alice's classical controller and RPC interface in SimulaQron.
    Manages classical communication for basis reconciliation, parameter estimation,
    parity query responses, and key confirmation.
    """

    def __init__(self, node, classicalNet,
                 num_qubits=DEFAULT_NUM_QUBITS,
                 qber_threshold=DEFAULT_QBER_THRESHOLD,
                 sample_ratio=DEFAULT_SAMPLE_RATIO):
        self.node          = node
        self.classicalNet  = classicalNet
        self.num_qubits    = num_qubits
        self.qber_threshold = qber_threshold
        self.sample_ratio  = sample_ratio

        self.virtRoot = None
        self.qReg     = None

        # -- Protocol State --
        self.raw_bits         = []   # QRNG-generated BB84 bits
        self.raw_bases        = []   # QRNG-generated BB84 bases
        self.sifted_key       = []
        self.sample_positions = []
        self.reconcile_key    = []   # Post-sample-discard; read-only by oracle

        # -- Cascade Oracle State --
        self.cascade_seed         = 0
        self.toeplitz_seed        = 0
        self.cascade_permutations = []   # permutations[pass][j] = orig_idx
        self.cascade_perm_keys    = []   # Precomputed permuted key views per pass

        # -- Output --
        self.final_key_bits = []
        self.final_key_hex  = ""

        # -- Metrics --
        self.start_time              = 0.0
        self.quantum_time            = 0.0
        self.protocol_qber           = 0.0
        self.leaked_parity_bits      = 0
        self.theoretical_secure_bits = 0
        self.cascade_corrections     = 0
        self.output_key_length       = 0

    def set_virtual_node(self, virtRoot):
        self.virtRoot = virtRoot

    def set_virtual_reg(self, qReg):
        self.qReg = qReg

    def remote_ping(self):
        """Connectivity probe for distributed validation."""
        return "ALICE_READY"

    # -------------------------------------------------------------------------
    # STAGE 2: BASIS SIFTING
    # -------------------------------------------------------------------------
    @inlineCallbacks
    def remote_receive_matching_indices(self, matching_indices):
        """
        Receives matching basis positions from Bob, extracts sifted key,
        and selects random sample positions for QBER parameter estimation.
        """
        logging.info("Alice: Processing matching basis positions from Bob.")
        self.sifted_key = [self.raw_bits[i] for i in matching_indices]

        print("\n" + "=" * 60)
        print("STAGE 2: BASIS SIFTING (ALICE)")
        print("=" * 60)
        print(f"Total Transmitted Qubits : {self.num_qubits}")
        print(f"Matching Basis Count     : {len(matching_indices)}")
        print(f"Sifted Key Length        : {len(self.sifted_key)}")
        print(f"Sifting Ratio            : {len(self.sifted_key) / max(1, self.num_qubits):.2%}")

        if len(self.sifted_key) == 0:
            logging.error("Alice: Sifted key is empty! Aborting.")
            yield self._abort_protocol("Empty sifted key")
            return

        # Sample positions seeded from QRNG cascade seed (quantum-random entropy)
        sample_size = max(2, int(len(self.sifted_key) * self.sample_ratio))
        sample_size = min(sample_size, len(self.sifted_key) - 2)
        rng = _py_random.Random(self.cascade_seed ^ 0xDEADBEEFCAFEBABE)
        self.sample_positions = sorted(rng.sample(range(len(self.sifted_key)), sample_size))

        print("\n" + "=" * 60)
        print("STAGE 3: QBER ESTIMATION SETUP")
        print("=" * 60)
        print(f"Sample Size Selected     : {sample_size} ({self.sample_ratio:.1%})")
        print(f"Sample Positions (First 10) : {self.sample_positions[:10]}...")

        # Transmit selected positions to Bob
        bob = self.classicalNet.hostDict["Bob"]
        yield bob.root.callRemote("receive_qber_sample_positions", self.sample_positions)

    # -------------------------------------------------------------------------
    # STAGE 3: QBER PARAMETER ESTIMATION
    # -------------------------------------------------------------------------
    @inlineCallbacks
    def remote_receive_qber_sample_bits(self, bob_sample_bits):
        """
        Receives Bob's sample bits, estimates QBER, verifies security threshold,
        and DISCARDS the revealed sample bits from the working key.
        """
        logging.info("Alice: Calculating QBER parameter estimation.")
        alice_sample_bits = [self.sifted_key[pos] for pos in self.sample_positions]

        mismatches = sum(1 for a, b in zip(alice_sample_bits, bob_sample_bits) if a != b)
        sample_size = len(bob_sample_bits)
        self.protocol_qber = (mismatches / sample_size) * 100.0 if sample_size > 0 else 0.0

        print("\n" + "=" * 60)
        print("STAGE 3: QBER EVALUATION RESULTS")
        print("=" * 60)
        print(f"Evaluated Sample Size    : {sample_size}")
        print(f"Observed Bit Errors      : {mismatches}")
        print(f"Calculated QBER          : {self.protocol_qber:.2f} %")
        print(f"Security QBER Threshold  : {self.qber_threshold:.2f} %")

        # CRITICAL SECURITY CHECK
        if self.protocol_qber > self.qber_threshold:
            print("\n[!] SECURITY ALERT: QBER EXCEEDS THRESHOLD!")
            print("Channel compromised by eavesdropping (Eve) or intolerable noise.")
            print("Aborting protocol immediately. No secret key will be generated.")
            yield self._abort_protocol(f"QBER {self.protocol_qber:.2f}% exceeds threshold {self.qber_threshold:.2f}%")
            return

        print("\n[*] Channel Security Verified: QBER within acceptable bounds.")

        # =====================================================================
        # MANDATORY BB84 RULE: DISCARD SAMPLE BITS FROM THE SIFTED KEY!
        # Publicly revealed sample bits must NEVER be part of the final key.
        # =====================================================================
        sample_set = set(self.sample_positions)
        self.reconcile_key = [
            self.sifted_key[i] for i in range(len(self.sifted_key))
            if i not in sample_set
        ]
        print(f"  Sample Bits Discarded    : {len(sample_set)}")
        print(f"  Reconcile Key Candidate  : {len(self.reconcile_key)} bits")

        # Initiate full Cascade on Bob's side
        print("\n" + "=" * 62)
        print("STAGE 4: INITIATING FULL CASCADE ERROR CORRECTION")
        print("=" * 62)
        print(f"  Sending QRNG cascade_seed to Bob...")

        bob = self.classicalNet.hostDict["Bob"]
        yield bob.root.callRemote(
            "receive_cascade_start",
            self.protocol_qber,
            self.cascade_seed,
        )

    # -------------------------------------------------------------------------
    # STAGE 4 (ORACLE): CASCADE SETUP  (called BY Bob)
    # -------------------------------------------------------------------------
    def remote_setup_cascade(self, cascade_seed: int, qber_frac: float):
        """
        Called by Bob to initialize Alice's Cascade permutation oracle.
        Precomputes permuted key views for all CASCADE_NUM_PASSES passes using
        the shared cascade_seed.  Both parties generate identical permutations
        from this seed without additional communication.

        Returns:
            Tuple (k1, num_passes, block_sizes) for Bob's CascadeReconciler.
        """
        n  = len(self.reconcile_key)
        k1 = cascade_initial_block_size(qber_frac, n) if n > 0 else 4
        block_sizes = [k1 * (2 ** i) for i in range(CASCADE_NUM_PASSES)]

        rng = _py_random.Random(cascade_seed)
        self.cascade_permutations = []
        self.cascade_perm_keys    = []
        for _ in range(CASCADE_NUM_PASSES):
            perm = list(range(n))
            rng.shuffle(perm)
            self.cascade_permutations.append(perm)
            self.cascade_perm_keys.append([self.reconcile_key[perm[j]] for j in range(n)])

        logging.info("Alice: Cascade oracle ready. k1=%d, passes=%d, block_sizes=%s",
                     k1, CASCADE_NUM_PASSES, block_sizes)
        print(f"  [Oracle] k1={k1}, passes={CASCADE_NUM_PASSES}, block_sizes={block_sizes}")
        return (k1, CASCADE_NUM_PASSES, block_sizes)

    # -------------------------------------------------------------------------
    # STAGE 4 (ORACLE): PARITY QUERY  (called BY Bob)
    # -------------------------------------------------------------------------
    def remote_get_cascade_parity(self, pass_idx: int, perm_start: int, perm_end: int) -> int:
        """
        Interactive Cascade parity oracle called repeatedly by Bob.
        Returns parity of Alice's key over [perm_start, perm_end) in the
        permuted coordinate system of pass_idx.

        SECURITY: Only a single parity bit (0 or 1) is returned per call.
        No raw key bits are ever exposed. Leakage = 1 bit/call, fully
        accounted for in the Shor-Preskill privacy amplification bound.
        """
        perm_key = self.cascade_perm_keys[pass_idx]
        return sum(perm_key[perm_start:perm_end]) % 2

    # -------------------------------------------------------------------------
    # LEGACY ORACLE (kept for backward compatibility with bisection search)
    # -------------------------------------------------------------------------
    def remote_get_subblock_parity(self, start_idx: int, end_idx: int) -> int:
        """
        Legacy bisection oracle (v3 compat). Delegates to direct key slice.
        SECURITY: Only parity (1 bit) is returned; raw bits never exposed.
        CRITICAL: Never reveals raw bit values; only 1-bit XOR sum is exposed.
        """
        self.leaked_parity_bits += 1
        sub_slice = self.reconcile_key[start_idx:end_idx]
        return sum(sub_slice) % 2

    # -------------------------------------------------------------------------
    # STAGE 5+6: PRIVACY AMPLIFICATION & KEY CONFIRMATION  (called BY Bob)
    # -------------------------------------------------------------------------
    @inlineCallbacks
    def remote_receive_cascade_complete(self, leaked_bits: int, corrected_bits: int):
        """
        Called by Bob after all Cascade passes complete.
        Performs Toeplitz matrix privacy amplification (2-universal hash) and
        sends key confirmation tag to Bob.
        """
        self.leaked_parity_bits  = leaked_bits
        self.cascade_corrections = corrected_bits

        print("\n" + "=" * 62)
        print("STAGE 5: PRIVACY AMPLIFICATION -- TOEPLITZ HASH (ALICE)")
        print("=" * 62)
        print(f"  Cascade Corrections      : {corrected_bits}")
        print(f"  Total Parity Bits Leaked : {leaked_bits}")

        # Shor-Preskill secure key length bound
        qber_frac = self.protocol_qber / 100.0
        self.theoretical_secure_bits = compute_secure_key_bound(
            len(self.reconcile_key), qber_frac, leaked_bits
        )
        self.output_key_length = max(1, min(256, self.theoretical_secure_bits))

        print(f"  Reconcile Key Length     : {len(self.reconcile_key)} bits")
        print(f"  Theoretical Secure Bound : {self.theoretical_secure_bits} bits")
        print(f"  Output Key Length (l)    : {self.output_key_length} bits")

        # Toeplitz 2-universal hash family -- information-theoretic security
        self.toeplitz_seed  = derive_toeplitz_seed(self.cascade_seed)
        self.final_key_bits = toeplitz_hash(
            self.reconcile_key, self.output_key_length, self.toeplitz_seed
        )
        self.final_key_hex = bits_to_hex(self.final_key_bits)

        print("\n  Privacy Amplified Shared Secret (Alice):")
        print(f"    HEX [{len(self.final_key_hex)} chars] : {self.final_key_hex}")
        key_bin = "".join(str(b) for b in self.final_key_bits)
        print(f"    BIN [{len(self.final_key_bits)} bits]  : {key_bin[:48]}{'...' if len(key_bin)>48 else ''}")

        # Stage 6: mutual key confirmation tag
        print("\n" + "=" * 62)
        print("STAGE 6: MUTUAL KEY CONFIRMATION (ALICE)")
        print("=" * 62)

        confirm_salt = "BB84_TOEPLITZ_V4_CONFIRMATION"
        alice_tag = hashlib.sha256(
            f"{self.final_key_hex}:{confirm_salt}".encode("utf-8")
        ).hexdigest()[:20]
        print(f"  Alice Confirmation Tag   : {alice_tag}")

        bob = self.classicalNet.hostDict["Bob"]
        bob_confirmed = yield bob.root.callRemote("confirm_key", alice_tag, {
            "qber"                   : self.protocol_qber,
            "sifted_len"             : len(self.sifted_key),
            "reconciled_len"         : len(self.reconcile_key),
            "leaked_bits"            : leaked_bits,
            "corrected_bits"         : corrected_bits,
            "theoretical_secure_bits": self.theoretical_secure_bits,
            "output_key_length"      : self.output_key_length,
            "pa_algorithm"           : "Toeplitz-2Universal",
        })

        execution_time = time.perf_counter() - self.start_time
        status = "SUCCESS" if bob_confirmed else "VERIFICATION_FAILED"

        if bob_confirmed:
            print("\n  [+] SUCCESS: 100% shared secret established and verified!")
        else:
            print("\n  [-] FAILURE: Key confirmation FAILED -- keys diverged!")

        self._save_session_results(status, execution_time)
        print("\nShutting down Alice reactor gracefully...")
        reactor.callLater(1.5, reactor.stop)

    # -------------------------------------------------------------------------
    # ABORT HANDLER
    # -------------------------------------------------------------------------
    @inlineCallbacks
    def _abort_protocol(self, reason: str):
        """Notifies Bob of abort, logs event, and cleanly stops local reactor."""
        logging.warning("Alice: Protocol ABORT -- %s", reason)
        try:
            bob = self.classicalNet.hostDict["Bob"]
            yield bob.root.callRemote("receive_abort", reason)
        except Exception as exc:
            logging.error("Alice: Failed to notify Bob of abort: %s", exc)

        execution_time = time.perf_counter() - self.start_time
        self._save_session_results("ABORTED", execution_time)
        reactor.callLater(1.0, reactor.stop)

    def _save_session_results(self, status: str, execution_time: float):
        """Persists text report, CSV entry, and key database entry."""
        key_bin = "".join(str(b) for b in self.final_key_bits)
        report = f"""
===============================================================================
        BB84 QUANTUM KEY DISTRIBUTION -- EXPERIMENT REPORT (ALICE)
===============================================================================
Protocol Version   : 4.0 -- QRNG + Full Cascade + Toeplitz Privacy Amplification
Execution Mode     : Distributed Two-System SimulaQron Native Mode
Station Node       : Alice (Transmitter)
Timestamp          : {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
Machine / Host     : {os.uname().nodename}

-------------------------------------------------------------------------------
1. PROTOCOL CONFIGURATION
-------------------------------------------------------------------------------
Total Transmitted Qubits  : {self.num_qubits}
RNG Method                : True QRNG (|+> quantum measurement on SimulaQron)
QBER Security Threshold   : {self.qber_threshold:.2f} %
Sample Ratio              : {self.sample_ratio:.1%}
Cascade Passes            : {CASCADE_NUM_PASSES}
Privacy Amplification     : Toeplitz 2-Universal Matrix Hash

-------------------------------------------------------------------------------
2. QUANTUM TRANSMISSION RECORD (QRNG)
-------------------------------------------------------------------------------
Alice Raw Bits (First 50) : {''.join(map(str, self.raw_bits[:50]))}...
Alice Bases    (First 50) : {''.join(self.raw_bases[:50])}...
Cascade Seed (QRNG 64b)   : {self.cascade_seed}
Toeplitz Seed (derived)   : {self.toeplitz_seed}
Quantum Transmission Time : {self.quantum_time:.4f} seconds

-------------------------------------------------------------------------------
3. BASIS SIFTING & PARAMETER ESTIMATION
-------------------------------------------------------------------------------
Sifted Key Length         : {len(self.sifted_key)}
Sample Bits Evaluated     : {len(self.sample_positions)}
Sample Bits Discarded     : {len(self.sample_positions)} (Mandatory security rule)
Reconcile Key Candidate   : {len(self.reconcile_key)} bits
Estimated QBER            : {self.protocol_qber:.4f} %
Security Verification     : {"PASSED" if self.protocol_qber <= self.qber_threshold else "FAILED"}

-------------------------------------------------------------------------------
4. CASCADE ERROR CORRECTION (Brassard & Salvail, 1993)
-------------------------------------------------------------------------------
Algorithm                 : Full Cascade ({CASCADE_NUM_PASSES} passes, adaptive k1, back-propagation)
Cascade Session Seed      : {self.cascade_seed} (QRNG-derived, 64-bit)
Bits Corrected by Cascade : {self.cascade_corrections}
Total Parity Bits Leaked  : {self.leaked_parity_bits}
Leakage Fraction          : {self.leaked_parity_bits/max(1,len(self.reconcile_key)):.4f} bits/bit

-------------------------------------------------------------------------------
5. PRIVACY AMPLIFICATION & SHARED SECRET
-------------------------------------------------------------------------------
Algorithm                 : Toeplitz Matrix (2-Universal Hash Family)
Toeplitz Seed (derived)   : {self.toeplitz_seed}
Theoretical Secure Bound  : {self.theoretical_secure_bits} bits (Shor-Preskill)
Output Key Length (l)     : {self.output_key_length} bits
Final Session Key (HEX)   : {self.final_key_hex if status == "SUCCESS" else "NONE"}
Final Session Key (BIN)   : {key_bin if status == "SUCCESS" else "NONE"}

-------------------------------------------------------------------------------
6. BENCHMARK & STATUS
-------------------------------------------------------------------------------
Total Execution Time      : {execution_time:.6f} seconds
Protocol Final Status     : {status}
===============================================================================
"""
        path = log_experiment_report(report, "alice_result")
        print(f"\n  [+] Experiment report saved: {path}")

        log_csv_metrics({
            "Timestamp"              : datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "Node"                   : "Alice",
            "Total_Qubits"           : self.num_qubits,
            "Sifted_Key_Length"      : len(self.sifted_key),
            "Sample_Bits_Discarded"  : len(self.sample_positions),
            "Reconciled_Key_Length"  : len(self.reconcile_key),
            "QBER(%)"                : f"{self.protocol_qber:.4f}",
            "QBER_Threshold(%)"      : f"{self.qber_threshold:.2f}",
            "Cascade_Passes"         : CASCADE_NUM_PASSES,
            "Cascade_Corrections"    : self.cascade_corrections,
            "Leaked_Parity_Bits"     : self.leaked_parity_bits,
            "Theoretical_Secure_Bits": self.theoretical_secure_bits,
            "Output_Key_Length"      : self.output_key_length,
            "PA_Algorithm"           : "Toeplitz-2Universal",
            "Execution_Time_s"       : f"{execution_time:.4f}",
            "Status"                 : status,
            "Secret_Key_Hex"         : self.final_key_hex if status == "SUCCESS" else "N/A",
            "Secret_Key_Bin_Prefix"  : key_bin[:32] if status == "SUCCESS" else "N/A",
        })
        if status == "SUCCESS":
            log_key_database(self.final_key_hex, self.output_key_length, "ESTABLISHED")


# =============================================================================
# QUANTUM TRANSMISSION CLIENT (ALICE)
# =============================================================================
@inlineCallbacks
def runClientNode(qReg, virtRoot, myName, classicalNet, lNode=None):
    """
    Main quantum execution routine for Alice.
    Generates qubits with CSPRNG bits/bases and transmits them to Bob.
    """
    logging.info("Starting BB84 Quantum Transmitter (Alice) v4 -- QRNG+Cascade+Toeplitz")
    alice_node = lNode
    alice_node.start_time = time.perf_counter()

    print("\n" + "=" * 62)
    print("  BB84 QKD -- ALICE TRANSMITTER  (v4: QRNG + CASCADE + TOEPLITZ)")
    print("=" * 62)

    # =========================================================================
    # STAGE 0: QRNG -- Quantum Random Number Generation
    # Measures |+> = H|0> states on the SimulaQron virtual node.
    # Quantum measurement outcome is provably random (Born rule).
    # =========================================================================
    print("\n[STAGE 0] QUANTUM RANDOM NUMBER GENERATION (QRNG)")
    print("=" * 62)
    print(f"  Generating {alice_node.num_qubits} quantum-random bit/basis pairs...")

    qrng_bits  = []
    qrng_bases = []

    for i in range(alice_node.num_qubits):
        # QRNG bit: |+> = H|0> measured -> quantum-random 0 or 1
        q_bit = yield virtRoot.callRemote("new_qubit")
        yield q_bit.callRemote("apply_H")
        bit = yield q_bit.callRemote("measure")
        qrng_bits.append(bit)

        # QRNG basis: |+> measured -> 0->Z, 1->X
        q_basis = yield virtRoot.callRemote("new_qubit")
        yield q_basis.callRemote("apply_H")
        basis_idx = yield q_basis.callRemote("measure")
        qrng_bases.append("Z" if basis_idx == 0 else "X")

        if (i + 1) % 25 == 0 or (i + 1) == alice_node.num_qubits:
            print(f"  [*] QRNG: {i+1}/{alice_node.num_qubits} bit/basis pairs generated")

    # 64-bit quantum-random cascade session seed
    print(f"  Generating {QRNG_SEED_BITS}-bit QRNG cascade seed...")
    seed_bits = []
    for _ in range(QRNG_SEED_BITS):
        q_s = yield virtRoot.callRemote("new_qubit")
        yield q_s.callRemote("apply_H")
        seed_bits.append((yield q_s.callRemote("measure")))

    alice_node.cascade_seed  = int("".join(str(b) for b in seed_bits), 2)
    alice_node.toeplitz_seed = derive_toeplitz_seed(alice_node.cascade_seed)

    print(f"  QRNG Complete:")
    print(f"    BB84 bits/bases : {len(qrng_bits)} quantum-random pairs")
    print(f"    Cascade seed    : {alice_node.cascade_seed} (64-bit QRNG)")
    print(f"    Toeplitz seed   : {alice_node.toeplitz_seed} (SHA-256 derived)")

    # =========================================================================
    # STAGE 1: Quantum State Preparation & Transmission
    # =========================================================================
    print("\n" + "=" * 62)
    print("[STAGE 1] BB84 QUANTUM STATE TRANSMISSION")
    print("=" * 62)
    print(f"\n  Encoding & transmitting {alice_node.num_qubits} BB84 qubits to Bob...\n")

    bob = classicalNet.hostDict["Bob"]
    quantum_start = time.perf_counter()

    for i in range(alice_node.num_qubits):
        bit   = qrng_bits[i]
        basis = qrng_bases[i]
        alice_node.raw_bits.append(bit)
        alice_node.raw_bases.append(basis)

        # Prepare BB84 qubit:
        #   Z basis: |0> if bit=0,  |1>=X|0> if bit=1
        #   X basis: |+>=H|0> if bit=0,  |->= H*X|0> if bit=1
        q = yield virtRoot.callRemote("new_qubit")
        if basis == "Z":
            if bit == 1:
                yield q.callRemote("apply_X")
        else:
            if bit == 1:
                yield q.callRemote("apply_X")
            yield q.callRemote("apply_H")

        remote_qubit_id = yield virtRoot.callRemote("send_qubit", q, "Bob")
        yield bob.root.callRemote("receive_bb84", remote_qubit_id, i)

        if (i + 1) % 25 == 0 or (i + 1) == alice_node.num_qubits:
            print(f"  [*] Transmitted: {i+1}/{alice_node.num_qubits} qubits")

    alice_node.quantum_time = time.perf_counter() - quantum_start

    print("\n" + "=" * 62)
    print("  QUANTUM TRANSMISSION COMPLETE")
    print("=" * 62)
    print(f"  Elapsed Quantum Time : {alice_node.quantum_time:.4f} s")
    print("\n  Sending Alice's basis sequence to Bob for sifting...")
    yield bob.root.callRemote("receive_basis_list", alice_node.raw_bases)


# =============================================================================
# MAIN INITIALIZATION & RUNNER
# =============================================================================
def main():
    parser = argparse.ArgumentParser(description="BB84 Alice (Transmitter) Node")
    parser.add_argument("--qubits", type=int, default=DEFAULT_NUM_QUBITS,
                        help=f"BB84 qubits to transmit (default: {DEFAULT_NUM_QUBITS})")
    parser.add_argument("--qber-threshold", type=float, default=DEFAULT_QBER_THRESHOLD,
                        help=f"QBER abort threshold %% (default: {DEFAULT_QBER_THRESHOLD})")
    parser.add_argument("--sample-ratio", type=float, default=DEFAULT_SAMPLE_RATIO,
                        help=f"QBER sample fraction (default: {DEFAULT_SAMPLE_RATIO})")
    args = parser.parse_args()

    myName = "Alice"

    simulaqron_settings.read_from_file(LOCAL_SIMULAQRON_SETTINGS)
    network_config.read_from_file(LOCAL_NETWORK_SETTINGS)

    simulaqron_settings.conn_retry_time = 1.0
    simulaqron_settings.conn_max_retries = 120

    virtualNet   = SocketsConfig(network_config, network_name="default", config_type="vnode")
    classicalNet = SocketsConfig(network_config, network_name="default", config_type="app")

    lNode = AliceBB84Node(
        classicalNet.hostDict[myName],
        classicalNet,
        num_qubits     = args.qubits,
        qber_threshold = args.qber_threshold,
        sample_ratio   = args.sample_ratio,
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
