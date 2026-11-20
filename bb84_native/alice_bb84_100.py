import logging
import random
import hashlib
import time
import os
import csv
from datetime import datetime

from simulaqron.local.setup import setup_local
from simulaqron.general.host_config import SocketsConfig
from simulaqron.settings import (
    simulaqron_settings,
    network_config,
    LOCAL_SIMULAQRON_SETTINGS,
    LOCAL_NETWORK_SETTINGS,
)

from simulaqron.reactor import reactor

from twisted.internet.defer import inlineCallbacks
from twisted.internet.task import deferLater
from twisted.spread import pb

# ============================================================
# BB84 PARAMETERS
# ============================================================

NUM_QUBITS = 100
BLOCK_SIZE = 4
QBER_THRESHOLD = 11.0

# ============================================================
# GLOBAL STORAGE
# ============================================================

alice_bits = []
alice_bases = []
alice_keep_key = []
protocol_qber = 0.0
# ============================================================
# Result File Generation
# ------------------------------------------------------------
# This function automatically saves the complete BB84 protocol
# execution report into the "results" directory.
# Each experiment is stored with a unique timestamp to avoid
# overwriting previous experiment results.
# ============================================================

def save_results(filename, content):

    # Create results directory if it does not already exist
    os.makedirs("results", exist_ok=True)

    # Count existing experiment files
    experiment_id = len(
        [f for f in os.listdir("results") if f.startswith(filename)]
    ) + 1

    # Generate current timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Create full filename
    filepath = os.path.join(
        "results",
        f"{filename}_Exp{experiment_id:03d}_{timestamp}.txt"
    )

    # Write report
    with open(filepath, "w") as file:
        file.write(content)

    print("\n================================================")
    print("Experiment Report Saved Successfully")
    print("================================================")
    print("Location :", filepath)

# ============================================================
# CSV Experiment Logger
# ============================================================

def save_csv_result(
    node,
    qubits,
    sifted_key_length,
    qber,
    corrected_bits,
    execution_time,
    status,
    secret_key,
):

    base_dir = os.path.dirname(os.path.abspath(__file__))

    csv_dir = os.path.join(base_dir, "csv_results")

    os.makedirs(csv_dir, exist_ok=True)

    csv_file = os.path.join(csv_dir, "experiment_statistics.csv")
    print("\nCSV File Path :", csv_file)

    experiment = datetime.now().strftime("%Y%m%d_%H%M%S")

    file_exists = os.path.isfile(csv_file)

    with open(csv_file, "a", newline="") as file:

        writer = csv.writer(file)

        if not file_exists:
            writer.writerow([
                "Experiment_No",
                "Experiment",
                "Date", "Node", 
                "Total_Qubits",
                "Sifted_Key_Length",
                "QBER(%)", 
                "Corrected_Bits", 
                "Execution_Time(s)",
                "Status",
                "SHA256_Key",

        ])

        writer.writerow([
            sum(1 for _ in open(csv_file)) if file_exists else 1,
            experiment,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            node,
            qubits,
            sifted_key_length,
            f"{qber:.2f}",
            corrected_bits,
            execution_time,
            status,
            secret_key,
        ])


###########################################################
# KEY DATABASE
###########################################################
def save_key_database(secret_key):

    key_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "keys"
    )

    os.makedirs(key_dir, exist_ok=True)

    key_file = os.path.join(
        key_dir,
        "key_database.txt"
    )

    # Create file with header if it doesn't exist
    if not os.path.exists(key_file):
        with open(key_file, "w") as f:
            f.write(f"{'Key_ID':<8}{'Key':<15}{'Time'}\n")

    # Count existing keys (ignore header)
    with open(key_file, "r") as f:
        lines = f.readlines()

    key_id = len(lines)      # Header = line 1, first key = 1

    with open(key_file, "a") as f:
        f.write(
            f"{key_id:<8}"
            f"{secret_key[:32]:<35}"
            f"{datetime.now().strftime('%H:%M:%S.%f')}\n"
        )



# ----------------------------------------------------
# Complete BB84 Protocol Timer
# ----------------------------------------------------
protocol_start_time = 0

# ============================================================
# BB84 Quantum State Preparation and Transmission
#
# This function performs the following operations:
# 1. Generates a random classical bit.
# 2. Randomly selects the encoding basis (Z or X).
# 3. Encodes the bit into a quantum state.
# 4. Sends the prepared qubit to Bob.
# 5. Stores the transmitted bits and bases locally.
# ============================================================
@inlineCallbacks
def runClientNode(qReg, virtRoot, myName, classicalNet):

    logging.info("Starting BB84 Sender (Alice)")
    global alice_bits
    global alice_bases
    global alice_keep_key

    alice_bits.clear()
    alice_bases.clear()
    alice_keep_key.clear()
    # ----------------------------------------------------
    # Start execution timer
    # ----------------------------------------------------
    global protocol_start_time
    protocol_start_time = time.perf_counter()

    bob = classicalNet.hostDict["Bob"]

    print("\n================================================")
    print("      BB84 Quantum Key Distribution")
    print("          Alice - Quantum Sender")
    print("================================================")

    print(f"\nPreparing and Sending {NUM_QUBITS} Quantum States...\n")

    for i in range(NUM_QUBITS):

        # ----------------------------------------------------
        # Create a new qubit inside Alice's quantum register
        # ----------------------------------------------------
        q = yield virtRoot.callRemote("new_qubit")

        # ----------------------------------------------------
        # Generate a random secret bit and encoding basis
        # ----------------------------------------------------
        bit = random.randint(0, 1)
        basis = random.choice(["Z", "X"])

        alice_bits.append(bit)
        alice_bases.append(basis)

        print("--------------------------------------------")
        print(f"Qubit Number   : {i + 1}")
        print(f"Secret Bit     : {bit}")
        print(f"Encoding Basis : {basis}")

        # ----------------------------------------------------
        # Prepare the qubit in Computational (Z) Basis
        #
        # Bit 0  → |0⟩
        # Bit 1  → |1⟩   (Apply Pauli-X Gate)
        # ----------------------------------------------------
        if basis == "Z":

            if bit == 1:
                yield q.callRemote("apply_X")

        # ----------------------------------------------------
        # Prepare the qubit in Diagonal (X) Basis
        #
        # Bit 0  → |+⟩ = H|0⟩
        # Bit 1  → |−⟩ = H|1⟩
        # ----------------------------------------------------
        else:

            if bit == 1:
                yield q.callRemote("apply_X")

            yield q.callRemote("apply_H")

        # ----------------------------------------------------
        # Transmit the encoded qubit to Bob
        # ----------------------------------------------------
        remoteNum = yield virtRoot.callRemote(
            "send_qubit",
            q,
            "Bob"
        )

        print("Remote Qubit ID :", remoteNum)
        print("Qubit Successfully Sent to Bob")
        # ----------------------------------------------------
        # Notify Bob that a new qubit is available
        # ----------------------------------------------------
        yield bob.root.callRemote(
            "receive_bb84",
            remoteNum,
            i + 1
        )

    # --------------------------------------------------------
    # Quantum Transmission Completed
    # Start the Classical Communication Phase
    # --------------------------------------------------------
    print("\n================================================")
    print("Quantum Transmission Completed")
    print("================================================")

    print("\nSending Basis List to Bob...")

    yield bob.root.callRemote(
        "receive_basis_list",
        alice_bases
    )

    # --------------------------------------------------------
    # Display Alice's Complete Transmission Record
    # --------------------------------------------------------
    print("\n================================================")
    print("Alice Raw Key (Before Sifting)")
    print("================================================")
    print("".join(map(str, alice_bits)))

    print("\n================================================")
    print("Alice Basis Sequence")
    print("================================================")
    print("".join(alice_bases))

    print("\nTotal Qubits Transmitted :", len(alice_bits))

    # ----------------------------------------------------
    # Stop execution timer
    # ----------------------------------------------------
    
    #reactor.stop()

# ============================================================
# Alice Local Node
#
# This class represents Alice's classical application running
# on the SimulaQron network. It performs all classical
# communication required by the BB84 protocol after the quantum
# transmission phase.
#
# Responsibilities:
#
# • Receive matching basis positions from Bob
# • Generate the sifted key
# • Perform Quantum Bit Error Rate (QBER) estimation
# • Support Error Correction
# • Perform Privacy Amplification
# ============================================================

class localNode(pb.Root):

    # --------------------------------------------------------
    # Constructor
    #
    # Initialize all local variables required during the
    # execution of the BB84 protocol.
    # --------------------------------------------------------
    def __init__(self, node, classicalNet):

        # Reference to Alice's classical node
        self.node = node

        # Classical communication network
        self.classicalNet = classicalNet

        # Reference to Alice's virtual quantum node
        self.virtRoot = None

        # Local quantum register
        self.qReg = None

        # Stores randomly selected QBER sample positions
        self.sample_positions = []

    # --------------------------------------------------------
    # Store the Virtual Quantum Node Reference
    #
    # SimulaQron provides a remote reference to Alice's quantum
    # node. This reference is used to create and manipulate
    # qubits.
    # --------------------------------------------------------
    def set_virtual_node(self, virtRoot):

        self.virtRoot = virtRoot

    # --------------------------------------------------------
    # Store the Local Quantum Register
    #
    # The quantum register maintains Alice's local qubits during
    # the execution of the protocol.
    # --------------------------------------------------------
    def set_virtual_reg(self, qReg):

        self.qReg = qReg

    # --------------------------------------------------------
    # Connectivity Test
    #
    # Bob can invoke this remote function to verify that Alice's
    # application is active and ready to participate in the
    # BB84 protocol.
    # --------------------------------------------------------
    def remote_test(self):

        return "BB84 Alice Node Ready"
    # ========================================================
    # Receive Matching Basis Positions from Bob
    #
    # Bob compares Alice's basis list with his own basis list
    # and sends the indices where both used the same basis.
    #
    # Alice extracts only those bits to generate the
    # sifted key.
    #
    # After generating the sifted key, Alice randomly selects
    # approximately 20% of the bits for QBER estimation.
    # ========================================================

    @inlineCallbacks
    def remote_receive_matching_indices(self, matching_indices):

        global alice_keep_key

        print("\n===================================")
        print("Alice Sifted Key")
        print("===================================")

        # Clear previous sifted key
        alice_keep_key = []

        # ----------------------------------------------------
        # Generate Alice's Sifted Key
        # ----------------------------------------------------
        for index in matching_indices:
            alice_keep_key.append(alice_bits[index])

        print("Matching Positions :")
        print(matching_indices)

        print("\nAlice Sifted Key :")
        print("".join(str(bit) for bit in alice_keep_key))

        print("\nSifted Key Length :", len(alice_keep_key))

        # ----------------------------------------------------
        # Randomly select 20% of the sifted key for
        # QBER estimation.
        # ----------------------------------------------------

        sample_size = max(
            1,
            len(alice_keep_key) // 5
        )

        self.sample_positions = sorted(
            random.sample(
                range(len(alice_keep_key)),
                sample_size
            )
        )

        print("\n==============================")
        print("QBER Sample Positions")
        print("==============================")

        print(self.sample_positions)

        print("Sample Size :", sample_size)

        print("\nSending QBER sample positions to Bob...")

        # ----------------------------------------------------
        # Send sample positions to Bob
        # ----------------------------------------------------

        bob = self.classicalNet.hostDict["Bob"]

        yield bob.root.callRemote(
            "receive_qber_sample_positions",
            self.sample_positions
        )
    # ========================================================
    # Receive Bob's Sample Bits for QBER Estimation
    #
    # Alice compares Bob's sample bits with her own sample
    # bits to estimate the Quantum Bit Error Rate (QBER).
    #
    # If the QBER is below the predefined threshold,
    # the protocol proceeds to the error correction phase.
    #
    # Otherwise, the protocol is terminated because the
    # communication channel is considered insecure.
    # ========================================================

    @inlineCallbacks
    def remote_receive_qber_sample_bits(self, sample_bits):

        global alice_keep_key

        print("\n===================================")
        print("QBER Calculation")
        print("===================================")

        # ----------------------------------------------------
        # Display Bob's sample bits
        # ----------------------------------------------------

        print("Bob Sample Bits :")
        print(sample_bits)

        # ----------------------------------------------------
        # Extract Alice's sample bits using the previously
        # selected sample positions.
        # ----------------------------------------------------

        alice_sample_bits = [
            alice_keep_key[position]
            for position in self.sample_positions
        ]

        print("Alice Sample Bits :")
        print(alice_sample_bits)

        # ----------------------------------------------------
        # Count mismatched bits
        # ----------------------------------------------------

        errors = 0

        for alice_bit, bob_bit in zip(
            alice_sample_bits,
            sample_bits
        ):

            if alice_bit != bob_bit:
                errors += 1

        sample_size = len(sample_bits)

        if sample_size == 0:
            qber = 0.0
        else:
            qber = (errors / sample_size) * 100
            global protocol_qber
            protocol_qber = qber
        print("\nErrors :", errors)
        print("Sample Size :", sample_size)
        print("QBER = {:.2f}%".format(qber))

        # ----------------------------------------------------
        # Verify whether QBER is acceptable
        # ----------------------------------------------------

        if qber > QBER_THRESHOLD:

            print("\n===================================")
            print("Protocol Aborted")
            print("===================================")

            print("QBER exceeds threshold.")
            print("Possible eavesdropping or excessive channel noise.")
            print("Secret key generation is terminated.")

            return

        # ----------------------------------------------------
        # QBER is acceptable.
        # Continue with Error Correction.
        # ----------------------------------------------------

        print("\n===================================")
        print("Starting Error Correction")
        print("===================================")

        parity_list = []

        # ----------------------------------------------------
        # Divide the sifted key into fixed-size blocks and
        # compute parity for every block.
        # ----------------------------------------------------

        for i in range(
            0,
            len(alice_keep_key),
            BLOCK_SIZE
        ):

            block = alice_keep_key[i:i + BLOCK_SIZE]

            parity = sum(block) % 2

            parity_list.append(parity)

        print("Alice Block Parity :")
        print(parity_list)

        # ----------------------------------------------------
        # Send parity values to Bob
        # ----------------------------------------------------

        bob = self.classicalNet.hostDict["Bob"]

        yield bob.root.callRemote(
            "receive_parity_list",
            parity_list
        )
    # ========================================================
    # Return the parity of a selected portion of a block.
    #
    # During Cascade Error Correction, Bob recursively divides
    # a mismatched block into smaller sub-blocks. Alice returns
    # the parity of the requested sub-block without revealing
    # the actual key bits.
    #
    # This minimizes information leakage while locating the
    # erroneous bit.
    # ========================================================

    def remote_get_block_parity(
        self,
        block_index,
        start,
        end
    ):

        global alice_keep_key

        # Starting position of the requested block
        block_start = block_index * BLOCK_SIZE

        # Calculate parity of requested sub-block
        parity = sum(

            alice_keep_key[
                block_start + start :
                block_start + end
            ]

        ) % 2

        return parity


    # ========================================================
    # Return the complete contents of a block.
    #
    # This function is currently used only for demonstration
    # purposes to verify the implementation.
    #
    # In a real BB84 implementation this function SHOULD NOT
    # reveal the complete block because it leaks secret key
    # information.
    #
    # Later this function can be removed and replaced entirely
    # with recursive parity queries (Cascade Protocol).
    # ========================================================

    def remote_get_block(self, block_index):

        global alice_keep_key

        start = block_index * BLOCK_SIZE
        end = min(
            start + BLOCK_SIZE,
            len(alice_keep_key)
        )

        return alice_keep_key[start:end]


    # ========================================================
    # Receive Error Correction Result from Bob
    #
    # Bob sends the list of blocks that contained parity
    # mismatches during error reconciliation.
    #
    # After successful reconciliation, Alice performs
    # Privacy Amplification using SHA-256 to generate the
    # final secret session key.
    # ========================================================

    def remote_receive_error_blocks(self, mismatch_blocks):

        global alice_keep_key

        print("\n===================================")
        print("Error Correction Result")
        print("===================================")

        # ----------------------------------------------------
        # Display error correction status
        # ----------------------------------------------------

        if len(mismatch_blocks) == 0:

            print("No parity mismatches found.")
            print("Keys are synchronized.")

        else:

            print("Mismatch Blocks :")
            print(mismatch_blocks)

        # ----------------------------------------------------
        # Display Alice's corrected sifted key
        # ----------------------------------------------------

        print("\nCorrected Alice Key :")
        print("".join(map(str, alice_keep_key)))

        print("\nCorrected Alice Key Length :",
              len(alice_keep_key))

        # ----------------------------------------------------
        # Privacy Amplification
        #
        # Compress the reconciled sifted key using SHA-256.
        # This removes any partial information that may have
        # leaked during public discussion.
        # ----------------------------------------------------

        print("\n===================================")
        print("Privacy Amplification")
        print("===================================")

        sifted_key = "".join(map(str, alice_keep_key))

        # Generate SHA-256 hash (hexadecimal)
        sha256_hex = hashlib.sha256(
            sifted_key.encode()
        ).hexdigest()

        # Convert hexadecimal SHA-256 to 256-bit binary

        secret_key = format(int(sha256_hex, 16), "0256b")
        save_key_database(secret_key)
        print("SHA-256 Privacy Amplified Key (Binary) :")
        print(secret_key)

        # ----------------------------------------------------
        # Complete BB84 Protocol Execution Time
        # ----------------------------------------------------
        global protocol_start_time

        protocol_end_time = time.perf_counter()
        execution_time = protocol_end_time - protocol_start_time

        print("\n===================================")
        print("Performance Statistics")
        print("===================================")

        print(
            "Complete BB84 Protocol Time : {:.6f} seconds".format(
                execution_time
            )
        )

        print("\n============= BB84 Statistics =============")

        print("Total Qubits Sent      :", NUM_QUBITS)
        print("Sifted Key Length      :", len(alice_keep_key))
        print("QBER Threshold         :", QBER_THRESHOLD, "%")
        print("Block Size             :", BLOCK_SIZE)
        print("Privacy Amplification  : SHA-256")
        print("Protocol Status        : SUCCESS")
        print("===========================================") 



        # ----------------------------------------------------
        # Protocol Completion
        # ----------------------------------------------------

        print("\n===================================")
        print("BB84 Protocol Completed")
        print("===================================")

        print("Secure Shared Secret Key Successfully Established.")


        # ============================================================
        # Generate Complete Experiment Report
        # ============================================================

        report = f"""

============================================================
          BB84 QUANTUM KEY DISTRIBUTION PROTOCOL
============================================================

Experiment Type : BB84 Quantum Key Distribution
Protocol Version: 2.0
Implementation  : SimulaQron Native Mode
Node            : Alice
Generated On    : {datetime.now().strftime("%d-%m-%Y %H:%M:%S")}

============================================================
System Information
============================================================

Machine Name    : {os.uname().nodename}
Network File    : simulaqron_network.json
Settings File   : simulaqron_settings.json

============================================================
Protocol Parameters
============================================================
Total Qubits       : {NUM_QUBITS}
Block Size         : {BLOCK_SIZE}
QBER Threshold     : {QBER_THRESHOLD} %

============================================================
Quantum State Preparation
============================================================

Alice Raw Key

{''.join(map(str, alice_bits))}

============================================================
Alice Basis Sequence
============================================================

{''.join(alice_bases)}

============================================================
Basis Reconciliation
============================================================

Alice Sifted Key

{''.join(map(str, alice_keep_key))}

Sifted Key Length : {len(alice_keep_key)}

============================================================
Error Reconciliation
============================================================

Mismatch Blocks : {mismatch_blocks}

============================================================
Privacy Amplification
============================================================

SHA-256 Secret Key

{secret_key}

============================================================
Performance Statistics
============================================================

Execution Time : {execution_time:.6f} seconds

============================================================
Final Statistics
============================================================

Protocol Status : SUCCESS

============================================================
End of Experiment Report
============================================================
"""






        save_results("alice_result", report)


        save_csv_result(
            "Alice",
            NUM_QUBITS,
            len(alice_keep_key),
            protocol_qber,
            len(mismatch_blocks),
            execution_time,
            "SUCCESS",
            secret_key,
        )

# Give Twisted time to finish sending any pending messages,
# then stop the reactor.
        reactor.callLater(1, reactor.stop)

# ============================================================
# Main Function
#
# This function initializes the BB84 simulation environment,
# loads the SimulaQron configuration files, creates the
# virtual quantum network and classical communication network,
# and finally starts Alice's BB84 application.
# ============================================================
def main():

    # --------------------------------------------------------
    # Define the local application name.
    # --------------------------------------------------------
    myName = "Alice"

    # --------------------------------------------------------
    # Load SimulaQron configuration files.
    #
    # LOCAL_SIMULAQRON_SETTINGS :
    #     Quantum backend configuration.
    #
    # LOCAL_NETWORK_SETTINGS :
    #     Classical and virtual network topology.
    # --------------------------------------------------------
    simulaqron_settings.read_from_file(
        LOCAL_SIMULAQRON_SETTINGS
    )

    network_config.read_from_file(
        LOCAL_NETWORK_SETTINGS
    )

    # --------------------------------------------------------
    # Create the Virtual Quantum Network.
    #
    # This network is responsible for creating and transmitting
    # qubits between Alice and Bob.
    # --------------------------------------------------------
    virtualNet = SocketsConfig(
        network_config,
        network_name="default",
        config_type="vnode",
    )

    # --------------------------------------------------------
    # Create the Classical Communication Network.
    #
    # This authenticated classical channel is used for:
    #
    # • Basis Reconciliation
    # • QBER Estimation
    # • Error Correction
    # • Privacy Amplification
    # --------------------------------------------------------
    classicalNet = SocketsConfig(
        network_config,
        network_name="default",
        config_type="app",
    )

    # --------------------------------------------------------
    # Create Alice's Local Node.
    # --------------------------------------------------------
    lNode = localNode(
        classicalNet.hostDict[myName],
        classicalNet
    )

    # --------------------------------------------------------
    # Start the BB84 Protocol.
    # --------------------------------------------------------
    setup_local(
        myName,
        virtualNet,
        classicalNet,
        lNode,
        runClientNode
    )


# ============================================================
# Program Entry Point
#
# Configure the logging system and start the BB84 protocol.
# ============================================================
if __name__ == "__main__":

    logging.basicConfig(
        format="%(asctime)s : %(levelname)s : %(message)s",
        level=logging.INFO,
    )

    main()
