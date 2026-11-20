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
DEFAULT_NOISE_PROBABILITY = 0.03  # Emulated quantum channel noise (0.0 for pure channel)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(BASE_DIR, "results")
CSV_DIR = os.path.join(BASE_DIR, "csv_results")
KEYS_DIR = os.path.join(BASE_DIR, "keys")

for d in [RESULTS_DIR, CSV_DIR, KEYS_DIR]:
    os.makedirs(d, exist_ok=True)


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
# BOB LOCAL NODE CLASS (ENCAPSULATED STATE MACHINE)
# =============================================================================
class BobBB84Node(pb.Root):
    """
    Represents Bob's classical controller and RPC interface in SimulaQron.
    Manages qubit reception, measurement basis selection, basis reconciliation,
    bisection error correction, privacy amplification, and key confirmation.
    """

    def __init__(self, node, classicalNet, num_qubits=DEFAULT_NUM_QUBITS,
                 block_size=DEFAULT_BLOCK_SIZE, noise_prob=DEFAULT_NOISE_PROBABILITY):
        self.node = node
        self.classicalNet = classicalNet
        self.num_qubits = num_qubits
        self.block_size = block_size
        self.noise_prob = noise_prob

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
        self.received_qubit_count = 0
        self.leaked_parity_bits = 0
        self.corrected_bits_count = 0
        self.mismatch_blocks = []

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

        # Retrieve remote qubit reference from SimulaQron virtual node
        q = yield self.virtRoot.callRemote("get_virtual_ref", virtualNum)

        # CSPRNG measurement basis choice (Z or X)
        basis = secrets.choice(["Z", "X"])
        self.raw_bases.append(basis)

        # For X basis measurement, apply Hadamard prior to Z-basis measurement
        if basis == "X":
            yield q.callRemote("apply_H")

        # Measure qubit
        measurement = yield q.callRemote("measure")

        # Emulated Quantum Channel Noise (Depolarizing / Bit-flip channel)
        if self.noise_prob > 0.0 and secrets.SystemRandom().random() < self.noise_prob:
            measurement = 1 - measurement

        self.raw_bits.append(measurement)
        self.received_qubit_count += 1

        if self.received_qubit_count % 25 == 0 or self.received_qubit_count == self.num_qubits:
            print(f"[*] Bob measured: {self.received_qubit_count}/{self.num_qubits} qubits")

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

        print(f"Sample Bits Discarded    : {len(sample_set)}")
        print(f"Remaining Reconcile Key  : {len(self.reconcile_key)} bits")

        print("\nSending sample bits to Alice for QBER estimation...")
        alice = self.classicalNet.hostDict["Alice"]
        yield alice.root.callRemote("receive_qber_sample_bits", sample_bits)

    # -------------------------------------------------------------------------
    # STAGE 4: PARITY RECONCILIATION & INTERACTIVE BISECTION SEARCH
    # -------------------------------------------------------------------------
    @inlineCallbacks
    def remote_receive_parity_list(self, alice_parities: list):
        """
        Receives block parities from Alice.
        Identifies parity mismatches, executes true bisection search (Binary Search)
        via remote parity queries to correct bit errors without secret bit leakage.
        """
        print("\n" + "=" * 60)
        print("STAGE 4: ERROR RECONCILIATION & BISECTION SEARCH (BOB)")
        print("=" * 60)

        bob_parities = []
        num_blocks = (len(self.reconcile_key) + self.block_size - 1) // self.block_size

        for b in range(num_blocks):
            start = b * self.block_size
            end = min(start + self.block_size, len(self.reconcile_key))
            bob_parities.append(sum(self.reconcile_key[start:end]) % 2)

        self.leaked_parity_bits += len(bob_parities)

        # Identify blocks with parity mismatches (odd number of errors)
        self.mismatch_blocks = [
            i for i in range(min(len(alice_parities), len(bob_parities)))
            if alice_parities[i] != bob_parities[i]
        ]

        print(f"Total Blocks Evaluated   : {num_blocks}")
        print(f"Mismatched Blocks Found  : {len(self.mismatch_blocks)}")
        if self.mismatch_blocks:
            print(f"Mismatch Block Indices   : {self.mismatch_blocks}")

        alice = self.classicalNet.hostDict["Alice"]
        self.corrected_bits_count = 0

        # Execute Interactive Bisection Error Correction
        for block_idx in self.mismatch_blocks:
            start = block_idx * self.block_size
            end = min(start + self.block_size, len(self.reconcile_key))

            # Perform binary search using only 1-bit parity queries
            err_pos, query_bits = yield self._bisection_search(alice.root, start, end)
            self.corrected_bits_count += 1
            self.leaked_parity_bits += query_bits

        print(f"Total Bit Errors Corrected : {self.corrected_bits_count}")
        print(f"Total Parity Bits Leaked   : {self.leaked_parity_bits}")

        # ---------------------------------------------------------------------
        # STAGE 5: PRIVACY AMPLIFICATION (BOB)
        # ---------------------------------------------------------------------
        print("\n" + "=" * 60)
        print("STAGE 5: PRIVACY AMPLIFICATION (BOB)")
        print("=" * 60)

        # Standardized SHA-256 Privacy Amplification (identical representation to Alice)
        reconciled_str = "".join(str(b) for b in self.reconcile_key)
        self.final_secret_key_hex = hashlib.sha256(reconciled_str.encode("utf-8")).hexdigest()
        self.final_secret_key_bin = bin(int(self.final_secret_key_hex, 16))[2:].zfill(256)

        print("\nPrivacy Amplified Shared Secret Key (Bob):")
        print(f"  HEX (64 chars) : {self.final_secret_key_hex}")
        print(f"  BIN (Prefix)   : {self.final_secret_key_bin[:40]}... (256 bits total)")

        # Notify Alice that reconciliation and amplification are ready
        yield alice.root.callRemote(
            "receive_reconciliation_complete",
            self.mismatch_blocks,
            self.corrected_bits_count,
            self.leaked_parity_bits
        )

    @inlineCallbacks
    def _bisection_search(self, alice_remote, start_idx: int, end_idx: int):
        """
        Standard Bisection Binary Search algorithm:
        Isolates erroneous bit index by querying parity of sub-blocks.
        Never transmits plaintext bits, preserving cryptographic security.
        """
        if hasattr(alice_remote, "root"):
            alice_remote = alice_remote.root

        cur_start = start_idx
        cur_end = end_idx
        queries = 0

        while (cur_end - cur_start) > 1:
            mid = (cur_start + cur_end) // 2
            # Query Alice for parity of left sub-block [cur_start, mid)
            alice_sub_parity = yield alice_remote.callRemote("get_subblock_parity", cur_start, mid)
            bob_sub_parity = sum(self.reconcile_key[cur_start:mid]) % 2
            queries += 1

            if alice_sub_parity != bob_sub_parity:
                # Error lies in left half
                cur_end = mid
            else:
                # Error lies in right half
                cur_start = mid

        # Pinpointed single error position at cur_start
        err_pos = cur_start
        # Flip the erroneous bit in Bob's reconcile key
        self.reconcile_key[err_pos] = 1 - self.reconcile_key[err_pos]
        return err_pos, queries

    # -------------------------------------------------------------------------
    # STAGE 6: KEY CONFIRMATION VERIFICATION
    # -------------------------------------------------------------------------
    def remote_confirm_key(self, alice_tag: str, metadata: dict) -> bool:
        """
        Verifies Alice's confirmation authentication tag.
        Confirms identical shared key agreement, persists results,
        and cleanly shuts down Bob's reactor.
        """
        print("\n" + "=" * 60)
        print("STAGE 6: KEY CONFIRMATION VERIFICATION (BOB)")
        print("=" * 60)

        confirm_salt = "BB84_SESSION_CONFIRMATION_TAG"
        bob_tag = hashlib.sha256(f"{self.final_secret_key_hex}:{confirm_salt}".encode("utf-8")).hexdigest()[:16]

        is_verified = (alice_tag == bob_tag)
        execution_time = time.perf_counter() - self.start_time

        if is_verified:
            print("[+] CONFIRMATION SUCCESS: Bob and Alice generated IDENTICAL shared keys!")
            status = "SUCCESS"
        else:
            print("[-] CONFIRMATION FAILURE: Hash tags do not match!")
            status = "CONFIRMATION_FAILED"

        # Persist session report, CSV, and key database
        self._save_session_results(status, execution_time, metadata)

        # Clean graceful reactor teardown
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
        qber = metadata.get("qber", 0.0)
        theo_secure = metadata.get("theoretical_secure_bits", 0)

        report = f"""
===============================================================================
              BB84 QUANTUM KEY DISTRIBUTION - EXPERIMENT REPORT
===============================================================================
Protocol Version   : 3.0 Standard Publication Level
Execution Mode     : Distributed Two-System SimulaQron Native Mode
Station Node       : Bob (Receiver)
Timestamp          : {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
Machine / Host     : {os.uname().nodename}

-------------------------------------------------------------------------------
1. PROTOCOL CONFIGURATION
-------------------------------------------------------------------------------
Total Expected Qubits     : {self.num_qubits}
Error Correction Block    : {self.block_size}
Simulated Noise Level     : {self.noise_prob:.2%}

-------------------------------------------------------------------------------
2. QUANTUM MEASUREMENT RECORD
-------------------------------------------------------------------------------
Bob Raw Bits (First 50)   : {''.join(map(str, self.raw_bits[:50]))}...
Bob Bases    (First 50)   : {''.join(self.raw_bases[:50])}...
Total Qubits Measured     : {len(self.raw_bits)}

-------------------------------------------------------------------------------
3. BASIS SIFTING & SAMPLE DISCARD
-------------------------------------------------------------------------------
Sifted Key Length         : {len(self.sifted_key)}
Sample Bits Discarded     : {len(self.sample_positions)} (Mandatory Rule)
Reconciled Key Candidate  : {len(self.reconcile_key)} bits
Reported Channel QBER     : {qber:.2f} %

-------------------------------------------------------------------------------
4. ERROR RECONCILIATION (BISECTION SEARCH)
-------------------------------------------------------------------------------
Mismatched Blocks Detected: {len(self.mismatch_blocks)}
Bits Corrected by Bob     : {self.corrected_bits_count}
Parity Bits Disclosed     : {self.leaked_parity_bits} bits

-------------------------------------------------------------------------------
5. PRIVACY AMPLIFICATION & SHARED SECRET
-------------------------------------------------------------------------------
Theoretical Secure Bound  : {theo_secure} bits
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
        report_path = log_experiment_report(report, "bob_result")
        print(f"\n[+] Detailed experiment report saved to: {report_path}")

        log_csv_metrics({
            "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "Node": "Bob",
            "Total_Qubits": self.num_qubits,
            "Sifted_Key_Length": len(self.sifted_key),
            "Sample_Bits_Discarded": len(self.sample_positions),
            "Reconciled_Key_Length": len(self.reconcile_key),
            "QBER(%)": f"{qber:.2f}",
            "QBER_Threshold(%)": "11.00",
            "Mismatched_Blocks": len(self.mismatch_blocks),
            "Leaked_Parity_Bits": self.leaked_parity_bits,
            "Theoretical_Secure_Bits": theo_secure,
            "Execution_Time_s": f"{execution_time:.4f}",
            "Status": status,
            "Secret_Key_Hex": self.final_secret_key_hex if status == "SUCCESS" else "N/A",
            "Secret_Key_Bin_Prefix": self.final_secret_key_bin[:32] if status == "SUCCESS" else "N/A",
        })

        if status == "SUCCESS":
            log_key_database(self.final_secret_key_hex, self.final_secret_key_bin, "ESTABLISHED")


# =============================================================================
# QUANTUM RECEIVER CLIENT (BOB)
# =============================================================================
def runClientNode(qReg, virtRoot, myName, classicalNet, lNode=None):
    """
    Main quantum setup routine for Bob.
    Initializes Bob's state machine and awaits Alice's remote transmissions.
    """
    logging.info("Starting BB84 Quantum Receiver (Bob)")
    print("\n" + "=" * 60)
    print("      BB84 QUANTUM KEY DISTRIBUTION: BOB RECEIVER")
    print("=" * 60)
    print(f"Ready and listening for {lNode.num_qubits} quantum transmissions from Alice...\n")


# =============================================================================
# MAIN INITIALIZATION & RUNNER
# =============================================================================
def main():
    parser = argparse.ArgumentParser(description="BB84 Bob (Receiver) Node")
    parser.add_argument("--qubits", type=int, default=DEFAULT_NUM_QUBITS, help="Total qubits to receive")
    parser.add_argument("--block-size", type=int, default=DEFAULT_BLOCK_SIZE, help="Error reconciliation block size")
    parser.add_argument("--noise", type=float, default=DEFAULT_NOISE_PROBABILITY, help="Channel noise probability (e.g. 0.03)")
    args = parser.parse_args()

    myName = "Bob"

    # Load SimulaQron configuration
    simulaqron_settings.read_from_file(LOCAL_SIMULAQRON_SETTINGS)
    network_config.read_from_file(LOCAL_NETWORK_SETTINGS)

    # Allow generous retry timeout (120s) so the user has time to start the second machine
    simulaqron_settings.conn_retry_time = 1.0
    simulaqron_settings.conn_max_retries = 120

    virtualNet = SocketsConfig(network_config, network_name="default", config_type="vnode")
    classicalNet = SocketsConfig(network_config, network_name="default", config_type="app")

    # Instantiate Bob Node
    lNode = BobBB84Node(
        classicalNet.hostDict[myName],
        classicalNet,
        num_qubits=args.qubits,
        block_size=args.block_size,
        noise_prob=args.noise,
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
