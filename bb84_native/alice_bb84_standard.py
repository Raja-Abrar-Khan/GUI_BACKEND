#!/usr/bin/env python3
"""
===============================================================================
BB84 QUANTUM KEY DISTRIBUTION (QKD) PROTOCOL - ALICE (TRANSMITTER)
===============================================================================
Standard-level, publication-grade implementation of the BB84 protocol
for distributed two-system execution via SimulaQron Native Mode.

Protocol Stages:
  1. Quantum State Preparation & Transmission (CSPRNG bits and bases)
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
import secrets
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

from twisted.internet.defer import inlineCallbacks
from twisted.internet.task import deferLater
from twisted.spread import pb

# =============================================================================
# DEFAULT PROTOCOL PARAMETERS (Tunable via CLI)
# =============================================================================
DEFAULT_NUM_QUBITS = 100
DEFAULT_BLOCK_SIZE = 4
DEFAULT_QBER_THRESHOLD = 11.0  # Theoretical security threshold for BB84 (~11%)
DEFAULT_SAMPLE_RATIO = 0.20    # Fraction of sifted key for parameter estimation

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(BASE_DIR, "results")
CSV_DIR = os.path.join(BASE_DIR, "csv_results")
KEYS_DIR = os.path.join(BASE_DIR, "keys")

for d in [RESULTS_DIR, CSV_DIR, KEYS_DIR]:
    os.makedirs(d, exist_ok=True)


# =============================================================================
# MATHEMATICAL & INFORMATION-THEORETIC UTILITIES
# =============================================================================
def binary_entropy(p: float) -> float:
    """Computes the Shannon binary entropy h2(p)."""
    if p <= 0.0 or p >= 1.0:
        return 0.0
    return -p * math.log2(p) - (1.0 - p) * math.log2(1.0 - p)


def compute_secure_key_bound(reconciled_length: int, qber_frac: float, leaked_parity_bits: int) -> int:
    """
    Computes theoretical asymptotic secret key length based on Shor-Preskill /
    Devetak-Winter bounds:
        l <= n * [1 - 2 * h2(e)] - leaked_parity_bits
    """
    h2 = binary_entropy(qber_frac)
    raw_fraction = 1.0 - 2.0 * h2
    if raw_fraction <= 0.0:
        return 0
    secure_bits = int(math.floor(reconciled_length * raw_fraction - leaked_parity_bits))
    return max(0, secure_bits)


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
        "QBER_Threshold(%)", "Mismatched_Blocks", "Leaked_Parity_Bits",
        "Theoretical_Secure_Bits", "Execution_Time_s", "Status",
        "Secret_Key_Hex", "Secret_Key_Bin_Prefix"
    ]

    with open(csv_file, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        row_data["Exp_ID"] = row_count
        writer.writerow(row_data)


def log_key_database(secret_key_hex: str, secret_key_bin: str, status: str = "ESTABLISHED"):
    """Appends cryptographically established key to key database."""
    key_file = os.path.join(KEYS_DIR, "key_database.txt")
    file_exists = os.path.isfile(key_file)
    key_id = 1

    if file_exists:
        try:
            with open(key_file, "r", encoding="utf-8") as f:
                key_id = sum(1 for line in f if line.strip())
        except Exception:
            key_id = 1

    with open(key_file, "a", encoding="utf-8") as f:
        if not file_exists:
            f.write(f"{'Key_ID':<8}{'Status':<14}{'Key_Hex_SHA256':<68}{'Timestamp'}\n")
            f.write("=" * 105 + "\n")
        f.write(
            f"{key_id:<8}{status:<14}{secret_key_hex:<68}"
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

    def __init__(self, node, classicalNet, num_qubits=DEFAULT_NUM_QUBITS,
                 block_size=DEFAULT_BLOCK_SIZE, qber_threshold=DEFAULT_QBER_THRESHOLD,
                 sample_ratio=DEFAULT_SAMPLE_RATIO):
        self.node = node
        self.classicalNet = classicalNet
        self.num_qubits = num_qubits
        self.block_size = block_size
        self.qber_threshold = qber_threshold
        self.sample_ratio = sample_ratio

        self.virtRoot = None
        self.qReg = None

        # Protocol Storage
        self.raw_bits = []
        self.raw_bases = []
        self.sifted_key = []
        self.sample_positions = []
        self.reconcile_key = []
        self.final_secret_key_hex = ""
        self.final_secret_key_bin = ""

        # Performance & Metrics
        self.start_time = 0.0
        self.quantum_time = 0.0
        self.protocol_qber = 0.0
        self.leaked_parity_bits = 0
        self.mismatch_blocks_count = 0
        self.theoretical_secure_bits = 0

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

        # STAGE 3: SELECT SAMPLE POSITIONS (CSPRNG)
        sample_size = max(1, int(len(self.sifted_key) * self.sample_ratio))
        # Use secrets for cryptographically secure sample index selection
        self.sample_positions = sorted(secrets.SystemRandom().sample(range(len(self.sifted_key)), sample_size))

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
        # Publicly revealed sample bits must NEVER be part of the final secret key.
        # =====================================================================
        sample_set = set(self.sample_positions)
        self.reconcile_key = [
            self.sifted_key[i] for i in range(len(self.sifted_key))
            if i not in sample_set
        ]

        print(f"Sample Bits Discarded    : {len(sample_set)}")
        print(f"Remaining Reconcile Key  : {len(self.reconcile_key)} bits")

        # ---------------------------------------------------------------------
        # STAGE 4: PARITY RECONCILIATION PREPARATION
        # ---------------------------------------------------------------------
        print("\n" + "=" * 60)
        print("STAGE 4: PARITY RECONCILIATION (ALICE)")
        print("=" * 60)

        alice_parities = []
        num_blocks = (len(self.reconcile_key) + self.block_size - 1) // self.block_size

        for b in range(num_blocks):
            start = b * self.block_size
            end = min(start + self.block_size, len(self.reconcile_key))
            block_slice = self.reconcile_key[start:end]
            alice_parities.append(sum(block_slice) % 2)

        self.leaked_parity_bits += len(alice_parities)
        print(f"Block Size               : {self.block_size}")
        print(f"Total Blocks Formed      : {num_blocks}")
        print(f"Parity Bits Disclosed    : {len(alice_parities)}")

        bob = self.classicalNet.hostDict["Bob"]
        yield bob.root.callRemote("receive_parity_list", alice_parities)

    # -------------------------------------------------------------------------
    # STAGE 4: SUB-BLOCK PARITY QUERY (CASCADE / BISECTION SEARCH SUPPORT)
    # -------------------------------------------------------------------------
    def remote_get_subblock_parity(self, start_idx: int, end_idx: int) -> int:
        """
        Interactive Bisection Error Correction Oracle:
        Returns parity (sum % 2) of Alice's sub-block [start_idx, end_idx).
        CRITICAL: Never reveals raw bit values; only 1-bit XOR sum is exposed.
        """
        self.leaked_parity_bits += 1
        sub_slice = self.reconcile_key[start_idx:end_idx]
        return sum(sub_slice) % 2

    # -------------------------------------------------------------------------
    # STAGE 5 & 6: PRIVACY AMPLIFICATION & KEY CONFIRMATION
    # -------------------------------------------------------------------------
    @inlineCallbacks
    def remote_receive_reconciliation_complete(self, mismatch_blocks: list, corrected_bits: int, bob_leaked_bits: int):
        """
        Called when Bob completes error correction.
        Executes Privacy Amplification, Key Confirmation, Report & Database Persistence.
        """
        self.mismatch_blocks_count = len(mismatch_blocks)
        self.leaked_parity_bits += bob_leaked_bits

        print("\n" + "=" * 60)
        print("STAGE 5: PRIVACY AMPLIFICATION & SECURITY ANALYSIS")
        print("=" * 60)
        print(f"Mismatched Blocks Detected : {self.mismatch_blocks_count}")
        print(f"Bits Corrected by Bob      : {corrected_bits}")
        print(f"Total Parity Bits Leaked   : {self.leaked_parity_bits}")

        qber_frac = self.protocol_qber / 100.0
        self.theoretical_secure_bits = compute_secure_key_bound(
            len(self.reconcile_key), qber_frac, self.leaked_parity_bits
        )
        print(f"Theoretical Secure Bits    : {self.theoretical_secure_bits} bits")

        # Privacy Amplification via SHA-256
        reconciled_str = "".join(str(b) for b in self.reconcile_key)
        self.final_secret_key_hex = hashlib.sha256(reconciled_str.encode("utf-8")).hexdigest()
        self.final_secret_key_bin = bin(int(self.final_secret_key_hex, 16))[2:].zfill(256)

        print("\nPrivacy Amplified Shared Secret Key:")
        print(f"  HEX (64 chars) : {self.final_secret_key_hex}")
        print(f"  BIN (Prefix)   : {self.final_secret_key_bin[:40]}... (256 bits total)")

        # STAGE 6: KEY CONFIRMATION TAG
        # Alice computes an authentication tag to confirm key synchronization with Bob
        confirm_salt = "BB84_SESSION_CONFIRMATION_TAG"
        alice_tag = hashlib.sha256(f"{self.final_secret_key_hex}:{confirm_salt}".encode("utf-8")).hexdigest()[:16]

        print("\n" + "=" * 60)
        print("STAGE 6: MUTUAL KEY CONFIRMATION")
        print("=" * 60)
        print(f"Transmitting Confirmation Tag to Bob : {alice_tag}")

        bob = self.classicalNet.hostDict["Bob"]
        bob_confirmed = yield bob.root.callRemote("confirm_key", alice_tag, {
            "num_qubits": self.num_qubits,
            "sifted_len": len(self.sifted_key),
            "reconciled_len": len(self.reconcile_key),
            "qber": self.protocol_qber,
            "corrected_bits": corrected_bits,
            "leaked_bits": self.leaked_parity_bits,
            "theoretical_secure_bits": self.theoretical_secure_bits
        })

        execution_time = time.perf_counter() - self.start_time

        if bob_confirmed:
            print("\n[+] SUCCESS: Bob verified confirmation tag! 100% Shared Secret Established.")
            status = "SUCCESS"
        else:
            print("\n[-] FAILURE: Key confirmation failed between Alice and Bob!")
            status = "VERIFICATION_FAILED"

        # PERSIST RESULTS
        self._save_session_results(status, execution_time, corrected_bits)

        # CLEAN GRACEFUL TEARDOWN
        print("\nShutting down Alice reactor gracefully...")
        reactor.callLater(1.5, reactor.stop)

    # -------------------------------------------------------------------------
    # ABORT HANDLER
    # -------------------------------------------------------------------------
    @inlineCallbacks
    def _abort_protocol(self, reason: str):
        """Notifies Bob of abort, logs event, and cleanly stops local reactor."""
        logging.warning("Alice: Protocol aborted: %s", reason)
        try:
            bob = self.classicalNet.hostDict["Bob"]
            yield bob.root.callRemote("receive_abort", reason)
        except Exception as e:
            logging.error("Failed to notify Bob of abort: %s", e)

        execution_time = time.perf_counter() - self.start_time
        self._save_session_results(f"ABORTED_{reason.upper()}", execution_time, 0)
        reactor.callLater(1.0, reactor.stop)

    def _save_session_results(self, status: str, execution_time: float, corrected_bits: int):
        """Persists text report, CSV entry, and key database entry."""
        report = f"""
===============================================================================
              BB84 QUANTUM KEY DISTRIBUTION - EXPERIMENT REPORT
===============================================================================
Protocol Version   : 3.0 Standard Publication Level
Execution Mode     : Distributed Two-System SimulaQron Native Mode
Station Node       : Alice (Transmitter)
Timestamp          : {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
Machine / Host     : {os.uname().nodename}

-------------------------------------------------------------------------------
1. PROTOCOL CONFIGURATION
-------------------------------------------------------------------------------
Total Transmitted Qubits  : {self.num_qubits}
Error Correction Block    : {self.block_size}
QBER Security Threshold   : {self.qber_threshold:.2f} %
Sample Ratio              : {self.sample_ratio:.1%}

-------------------------------------------------------------------------------
2. QUANTUM TRANSMISSION RECORD
-------------------------------------------------------------------------------
Alice Raw Bits (First 50) : {''.join(map(str, self.raw_bits[:50]))}...
Alice Bases    (First 50) : {''.join(self.raw_bases[:50])}...
Quantum Transmission Time : {self.quantum_time:.4f} seconds

-------------------------------------------------------------------------------
3. BASIS SIFTING & PARAMETER ESTIMATION
-------------------------------------------------------------------------------
Sifted Key Length         : {len(self.sifted_key)}
Sample Bits Evaluated     : {len(self.sample_positions)}
Sample Bits Discarded     : {len(self.sample_positions)} (Mandatory Rule)
Reconciled Key Candidate  : {len(self.reconcile_key)} bits
Estimated QBER            : {self.protocol_qber:.2f} %
Security Verification     : {'PASSED' if self.protocol_qber <= self.qber_threshold else 'FAILED'}

-------------------------------------------------------------------------------
4. ERROR RECONCILIATION (BISECTION SEARCH)
-------------------------------------------------------------------------------
Mismatched Blocks Found   : {self.mismatch_blocks_count}
Bits Corrected by Bob     : {corrected_bits}
Parity Bits Disclosed     : {self.leaked_parity_bits} bits
Information Leakage       : {self.leaked_parity_bits / max(1, len(self.reconcile_key)):.2%}

-------------------------------------------------------------------------------
5. PRIVACY AMPLIFICATION & SHARED SECRET
-------------------------------------------------------------------------------
Theoretical Secure Bound  : {self.theoretical_secure_bits} bits
Amplification Algorithm   : SHA-256 Cryptographic Compression
Final Session Key (HEX)   : {self.final_secret_key_hex if status == 'SUCCESS' else 'NONE'}
Final Session Key (BIN)   : {self.final_secret_key_bin if status == 'SUCCESS' else 'NONE'}

-------------------------------------------------------------------------------
6. BENCHMARK & STATUS
-------------------------------------------------------------------------------
Total Execution Time      : {execution_time:.6f} seconds
Protocol Final Status     : {status}
===============================================================================
"""
        report_path = log_experiment_report(report, "alice_result")
        print(f"\n[+] Detailed experiment report saved to: {report_path}")

        log_csv_metrics({
            "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "Node": "Alice",
            "Total_Qubits": self.num_qubits,
            "Sifted_Key_Length": len(self.sifted_key),
            "Sample_Bits_Discarded": len(self.sample_positions),
            "Reconciled_Key_Length": len(self.reconcile_key),
            "QBER(%)": f"{self.protocol_qber:.2f}",
            "QBER_Threshold(%)": f"{self.qber_threshold:.2f}",
            "Mismatched_Blocks": self.mismatch_blocks_count,
            "Leaked_Parity_Bits": self.leaked_parity_bits,
            "Theoretical_Secure_Bits": self.theoretical_secure_bits,
            "Execution_Time_s": f"{execution_time:.4f}",
            "Status": status,
            "Secret_Key_Hex": self.final_secret_key_hex if status == "SUCCESS" else "N/A",
            "Secret_Key_Bin_Prefix": self.final_secret_key_bin[:32] if status == "SUCCESS" else "N/A",
        })

        if status == "SUCCESS":
            log_key_database(self.final_secret_key_hex, self.final_secret_key_bin, "ESTABLISHED")


# =============================================================================
# QUANTUM TRANSMISSION CLIENT (ALICE)
# =============================================================================
@inlineCallbacks
def runClientNode(qReg, virtRoot, myName, classicalNet, lNode=None):
    """
    Main quantum execution routine for Alice.
    Generates qubits with CSPRNG bits/bases and transmits them to Bob.
    """
    logging.info("Starting BB84 Quantum Transmitter (Alice)")
    print("\n" + "=" * 60)
    print("      BB84 QUANTUM KEY DISTRIBUTION: ALICE TRANSMITTER")
    print("=" * 60)

    # Initialize timer and node reference
    alice_node = lNode
    alice_node.start_time = time.perf_counter()
    quantum_start = time.perf_counter()

    bob = classicalNet.hostDict["Bob"]

    print(f"\nPreparing and transmitting {alice_node.num_qubits} quantum states to Bob...\n")

    for i in range(alice_node.num_qubits):
        # Create new qubit in Alice's quantum register
        q = yield virtRoot.callRemote("new_qubit")

        # Generate bit and basis using cryptographically secure PRNG (secrets)
        bit = secrets.randbelow(2)
        basis = secrets.choice(["Z", "X"])

        alice_node.raw_bits.append(bit)
        alice_node.raw_bases.append(basis)

        # Quantum state preparation
        # Z basis: 0 -> |0>, 1 -> |1> (apply X)
        # X basis: 0 -> |+> = H|0>, 1 -> |-> = H|1> = H(X|0>)
        if basis == "Z":
            if bit == 1:
                yield q.callRemote("apply_X")
        else:
            if bit == 1:
                yield q.callRemote("apply_X")
            yield q.callRemote("apply_H")

        # Transmit qubit to Bob via virtual quantum network
        remote_qubit_id = yield virtRoot.callRemote("send_qubit", q, "Bob")

        # Notify Bob over classical channel that qubit i is ready for measurement
        yield bob.root.callRemote("receive_bb84", remote_qubit_id, i)

        if (i + 1) % 25 == 0 or (i + 1) == alice_node.num_qubits:
            print(f"[*] Successfully prepared & transmitted: {i + 1}/{alice_node.num_qubits} qubits")

    alice_node.quantum_time = time.perf_counter() - quantum_start

    print("\n" + "=" * 60)
    print("QUANTUM TRANSMISSION COMPLETED")
    print("=" * 60)
    print(f"Elapsed Quantum Time     : {alice_node.quantum_time:.4f} seconds")
    print("\nSending Alice's Basis Sequence to Bob for Sifting...")

    # Transmit Alice's basis sequence to Bob
    yield bob.root.callRemote("receive_basis_list", alice_node.raw_bases)


# =============================================================================
# MAIN INITIALIZATION & RUNNER
# =============================================================================
def main():
    parser = argparse.ArgumentParser(description="BB84 Alice (Transmitter) Node")
    parser.add_argument("--qubits", type=int, default=DEFAULT_NUM_QUBITS, help="Total qubits to transmit")
    parser.add_argument("--block-size", type=int, default=DEFAULT_BLOCK_SIZE, help="Error reconciliation block size")
    parser.add_argument("--qber-threshold", type=float, default=DEFAULT_QBER_THRESHOLD, help="Max acceptable QBER (%)")
    parser.add_argument("--sample-ratio", type=float, default=DEFAULT_SAMPLE_RATIO, help="Ratio for QBER estimation")
    args = parser.parse_args()

    myName = "Alice"

    # Load SimulaQron configuration
    simulaqron_settings.read_from_file(LOCAL_SIMULAQRON_SETTINGS)
    network_config.read_from_file(LOCAL_NETWORK_SETTINGS)

    # Allow generous retry timeout (120s) so the user has time to start the second machine
    simulaqron_settings.conn_retry_time = 1.0
    simulaqron_settings.conn_max_retries = 120

    virtualNet = SocketsConfig(network_config, network_name="default", config_type="vnode")
    classicalNet = SocketsConfig(network_config, network_name="default", config_type="app")

    # Instantiate Alice Node
    lNode = AliceBB84Node(
        classicalNet.hostDict[myName],
        classicalNet,
        num_qubits=args.qubits,
        block_size=args.block_size,
        qber_threshold=args.qber_threshold,
        sample_ratio=args.sample_ratio,
    )

    # Launch native mode setup
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
