import logging
import random
import hashlib
import time

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
NOISE_PROBABILITY = 0.05

# ============================================================
# GLOBAL STORAGE
# ============================================================

bob_bits = []
bob_bases = []
bob_keep_key = []

# ------------------------------------------------------------
# Complete BB84 Protocol Timer
# ------------------------------------------------------------

protocol_start_time = 0


# ============================================================
# BB84 Quantum Receiver
#
# Bob receives quantum states transmitted by Alice.
#
# Responsibilities
#
# 1. Receive incoming qubits
# 2. Randomly choose a measurement basis
# 3. Measure each qubit
# 4. Simulate channel noise
# 5. Store the measured bits and bases
#
# After all qubits are received, the protocol proceeds to the
# classical communication phase.
# ============================================================

def runClientNode(qReg, virtRoot, myName, classicalNet):

    logging.info("Starting BB84 Receiver (Bob)")

    global bob_bits
    global bob_bases
    global bob_keep_key
    global protocol_start_time

    # --------------------------------------------------------
    # Clear previous execution data
    # --------------------------------------------------------

    bob_bits.clear()
    bob_bases.clear()
    bob_keep_key.clear()

    # --------------------------------------------------------
    # Start Complete Protocol Timer
    # --------------------------------------------------------

    protocol_start_time = time.perf_counter()

# ============================================================
# Bob Local Node
#
# This class represents Bob's classical application running
# on the SimulaQron network.
#
# Responsibilities
#
# • Receive quantum states from Alice
# • Measure each received qubit
# • Store measurement results
# • Perform Basis Reconciliation
# • Perform QBER Estimation
# • Execute Error Correction
# • Perform Privacy Amplification
# ============================================================

class localNode(pb.Root):

    # --------------------------------------------------------
    # Constructor
    #
    # Initialize Bob's local variables required throughout
    # the execution of the BB84 protocol.
    # --------------------------------------------------------
    def __init__(self, node, classicalNet):

        # Reference to Bob's classical node
        self.node = node

        # Classical communication network
        self.classicalNet = classicalNet

        # Reference to Bob's virtual quantum node
        self.virtRoot = None

        # Bob's local quantum register
        self.qReg = None

        # Number of qubits received
        self.count = 0

    # --------------------------------------------------------
    # Store Virtual Quantum Node Reference
    # --------------------------------------------------------
    def set_virtual_node(self, virtRoot):

        self.virtRoot = virtRoot

    # --------------------------------------------------------
    # Store Local Quantum Register
    # --------------------------------------------------------
    def set_virtual_reg(self, qReg):

        self.qReg = qReg

    # --------------------------------------------------------
    # Wait Until Quantum Backend is Ready
    #
    # SimulaQron initializes the virtual quantum backend
    # asynchronously. Bob waits until both the virtual node
    # and quantum register become available.
    # --------------------------------------------------------
    @inlineCallbacks
    def _wait_ready(self):

        while self.virtRoot is None or self.qReg is None:

            yield deferLater(
                reactor,
                0.1,
                lambda: None
            )

    # ========================================================
    # Receive Quantum State from Alice
    #
    # For every received qubit Bob:
    #
    # 1. Retrieves the virtual qubit.
    # 2. Randomly selects a measurement basis.
    # 3. Measures the qubit.
    # 4. Simulates channel noise.
    # 5. Stores the measurement result.
    # ========================================================
    @inlineCallbacks
    def remote_receive_bb84(self, virtualNum, qubit_no):

        yield self._wait_ready()

        print("\n================================================")
        print("Receiving Quantum State")
        print("================================================")

        print("Qubit Number :", qubit_no)

        # ----------------------------------------------------
        # Retrieve the received qubit
        # ----------------------------------------------------
        q = yield self.virtRoot.callRemote(
            "get_virtual_ref",
            virtualNum
        )

        print("Remote Qubit ID :", virtualNum)

        # ----------------------------------------------------
        # Randomly choose the measurement basis
        # ----------------------------------------------------
        basis = random.choice(["Z", "X"])

        bob_bases.append(basis)

        print("Measurement Basis :", basis)

        # ----------------------------------------------------
        # Apply Hadamard Gate before measurement if
        # measuring in the X basis.
        # ----------------------------------------------------
        if basis == "X":

            yield q.callRemote("apply_H")

        # ----------------------------------------------------
        # Measure the received qubit
        # ----------------------------------------------------
        measurement = yield q.callRemote("measure")

        # ----------------------------------------------------
        # Simulate Quantum Channel Noise
        #
        # With a fixed probability the measured bit is
        # intentionally flipped to emulate transmission noise.
        # ----------------------------------------------------
        if random.random() < NOISE_PROBABILITY:

            measurement = 1 - measurement

            print("Channel Noise Introduced")
            print("Bit Flipped During Transmission")

        bob_bits.append(measurement)

        print("Measured Bit :", measurement)

        self.count += 1

        # ----------------------------------------------------
        # Display Bob's complete raw measurement results
        # after all qubits have been received.
        # ----------------------------------------------------
        if self.count == NUM_QUBITS:

            print("\n================================================")
            print("Bob Raw Key (Before Sifting)")
            print("================================================")

            print("".join(map(str, bob_bits)))

            print("\n================================================")
            print("Bob Random Basis Sequence")
            print("================================================")

            print("".join(bob_bases))

            print("\nTotal Qubits Received :", len(bob_bits))
        # ========================================================
    # Basis Reconciliation
    #
    # Bob compares Alice's basis sequence with his own basis
    # sequence and keeps only those positions where both
    # selected the same measurement basis.
    #
    # The resulting bits form Bob's sifted key.
    # ========================================================

    @inlineCallbacks
    def remote_receive_basis_list(self, alice_bases):

        global bob_keep_key

        print("\n================================================")
        print("Basis Reconciliation")
        print("================================================")

        matching_indices = []
        bob_keep_key = []

        # ----------------------------------------------------
        # Compare Alice's basis with Bob's basis
        # ----------------------------------------------------

        for index in range(NUM_QUBITS):

            if alice_bases[index] == bob_bases[index]:

                matching_indices.append(index)

                bob_keep_key.append(
                    bob_bits[index]
                )

        print("Alice Basis Sequence")
        print("".join(alice_bases))

        print("\nBob Basis Sequence")
        print("".join(bob_bases))

        print("\nMatching Basis Positions :")
        print(matching_indices)

        print("\nTotal Matching Bases :", len(matching_indices))

        print("\n================================================")
        print("Bob Sifted Key")
        print("================================================")

        print("".join(map(str, bob_keep_key)))

        print("\nSifted Key Length :", len(bob_keep_key))

        # ----------------------------------------------------
        # Send matching positions to Alice
        # ----------------------------------------------------

        print("\nSending Matching Positions to Alice...")

        alice = self.classicalNet.hostDict["Alice"]

        yield alice.root.callRemote(

            "receive_matching_indices",

            matching_indices
        )


    # ========================================================
    # Receive Sample Positions for QBER Estimation
    #
    # Alice randomly selects approximately 20% of the sifted
    # key and sends the corresponding positions.
    #
    # Bob extracts the requested bits and returns them to
    # Alice for Quantum Bit Error Rate estimation.
    # ========================================================

    @inlineCallbacks
    def remote_receive_qber_sample_positions(
        self,
        sample_positions
    ):

        print("\n================================================")
        print("QBER Sample Request")
        print("================================================")

        print("Sample Positions :")
        print(sample_positions)

        sample_bits = []

        # ----------------------------------------------------
        # Extract Bob's bits at the requested positions
        # ----------------------------------------------------

        for position in sample_positions:

            sample_bits.append(
                bob_keep_key[position]
            )

        print("\nBob Sample Bits :")
        print(sample_bits)

        # ----------------------------------------------------
        # Send sample bits back to Alice
        # ----------------------------------------------------

        print("\nSending Sample Bits to Alice...")

        alice = self.classicalNet.hostDict["Alice"]

        yield alice.root.callRemote(

            "receive_qber_sample_bits",

            sample_bits
        )
        # ========================================================
    # Error Reconciliation
    #
    # Alice sends the parity of each sifted-key block.
    #
    # Bob computes the parity of his corresponding blocks,
    # identifies mismatched blocks, performs bit correction,
    # and finally applies Privacy Amplification.
    # ========================================================

    @inlineCallbacks
    def remote_receive_parity_list(self, alice_parity):

        global bob_keep_key
        global protocol_start_time

        print("\n================================================")
        print("Error Reconciliation")
        print("================================================")

        bob_parity = []

        # ----------------------------------------------------
        # Calculate Bob's parity for each block
        # ----------------------------------------------------

        for i in range(0, len(bob_keep_key), BLOCK_SIZE):

            block = bob_keep_key[i:i + BLOCK_SIZE]

            parity = sum(block) % 2

            bob_parity.append(parity)

        print("Alice Block Parity :")
        print(alice_parity)

        print("\nBob Block Parity :")
        print(bob_parity)

        # ----------------------------------------------------
        # Identify parity mismatches
        # ----------------------------------------------------

        mismatch_blocks = []

        for block_index in range(len(alice_parity)):

            if alice_parity[block_index] != bob_parity[block_index]:

                mismatch_blocks.append(block_index)

        print("\nMismatch Blocks :")
        print(mismatch_blocks)

        print("\n================================================")
        print("Detected Error Blocks")
        print("================================================")

        if len(mismatch_blocks) == 0:

            print("No mismatched blocks detected.")

        else:

            for block_index in mismatch_blocks:

                start = block_index * BLOCK_SIZE

                end = min(
                    start + BLOCK_SIZE,
                    len(bob_keep_key)
                )

                print(
                    f"Block {block_index} :",
                    bob_keep_key[start:end]
                )

        # ----------------------------------------------------
        # Bit Error Correction
        # ----------------------------------------------------

        print("\n================================================")
        print("Bit Error Correction")
        print("================================================")

        alice = self.classicalNet.hostDict["Alice"]

        corrected_bits = 0

        if len(mismatch_blocks) == 0:

            print("No bit correction required.")

        else:

            for block_index in mismatch_blocks:

                start = block_index * BLOCK_SIZE

                end = min(
                    start + BLOCK_SIZE,
                    len(bob_keep_key)
                )

                bob_block = bob_keep_key[start:end]

                alice_block = yield alice.root.callRemote(

                    "get_block",

                    block_index
                )

                print(f"\nProcessing Block {block_index}")

                print("Alice :", alice_block)

                print("Bob   :", bob_block)

                for i in range(len(bob_block)):

                    if bob_block[i] != alice_block[i]:

                        print(
                            f"Correcting Bit Position {i}"
                        )

                        bob_block[i] = alice_block[i]

                        corrected_bits += 1

                bob_keep_key[start:end] = bob_block

        print("\nTotal Corrected Bits :", corrected_bits)

        print("\n================================================")
        print("Corrected Bob Key")
        print("================================================")

        print("".join(map(str, bob_keep_key)))

        print("\nCorrected Bob Key Length :",
              len(bob_keep_key))

        # ----------------------------------------------------
        # Privacy Amplification
        # ----------------------------------------------------

        print("\n================================================")
        print("Privacy Amplification")
        print("================================================")

        sifted_key = "".join(map(str, bob_keep_key))

        secret_key = hashlib.sha256(

            sifted_key.encode()

        ).hexdigest()

        print("Final Shared Secret Key (SHA-256)")
        print(secret_key)

        # ----------------------------------------------------
        # Performance Statistics
        # ----------------------------------------------------

        protocol_end_time = time.perf_counter()

        print("\n================================================")
        print("Performance Statistics")
        print("================================================")

        print(
            "Total Protocol Execution Time : {:.6f} seconds".format(
                protocol_end_time - protocol_start_time
            )
        )

        # ----------------------------------------------------
        # BB84 Statistics
        # ----------------------------------------------------

        print("\n============= BB84 Statistics =============")

        print("Total Qubits Received :", NUM_QUBITS)

        print("Sifted Key Length     :", len(bob_keep_key))

        print("Noise Probability     :", NOISE_PROBABILITY)

        print("Block Size            :", BLOCK_SIZE)

        print("Privacy Amplification : SHA-256")

        print("Protocol Status       : SUCCESS")

        print("===========================================")

        # ----------------------------------------------------
        # Notify Alice that reconciliation is complete
        # ----------------------------------------------------

        yield alice.root.callRemote(

            "receive_error_blocks",

            mismatch_blocks
        )

        # ----------------------------------------------------
        # Protocol Completed
        # ----------------------------------------------------

        print("\n================================================")
        print("BB84 Protocol Completed")
        print("================================================")

        print("Secure Shared Secret Key Successfully Established.")


# ============================================================
# Main Function
#
# This function initializes the BB84 simulation environment,
# loads the SimulaQron configuration files, creates the
# virtual quantum network and classical communication network,
# and finally starts Bob's BB84 application.
# ============================================================

def main():

    # --------------------------------------------------------
    # Define the local application name.
    # --------------------------------------------------------
    myName = "Bob"

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
    # This network is responsible for receiving quantum states
    # transmitted from Alice.
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
    # • Error Reconciliation
    # • Privacy Amplification
    # --------------------------------------------------------
    classicalNet = SocketsConfig(
        network_config,
        network_name="default",
        config_type="app",
    )

    # --------------------------------------------------------
    # Create Bob's Local Node.
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
        level=logging.DEBUG,
    )

    main()
