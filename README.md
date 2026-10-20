Multi-Path Onion Slicing (MPOS)

Secure, Fault-Tolerant File Transfer Using Erasure Coding, Encryption, and Multi-Path Routing

Project: Computer Networks Laboratory
Repository: Multi-Path Onion Slicing (MPOS)
Language: Python
Interface: Command-Line Interface (CLI) and Graphical User Interface (GUI)

⸻

1. Abstract

Multi-Path Onion Slicing (MPOS) is a secure file-transfer system designed to improve data confidentiality, transmission reliability, and resilience to network failures.

The system divides files into multiple redundant shards using Reed–Solomon erasure coding. Each shard is encrypted using AES-256-GCM and transmitted through a multi-hop relay network using onion-style encryption and multiple communication paths.

At the receiving end, the system decrypts the received shards and reconstructs the original file when a sufficient number of valid shards are available. This allows data recovery even when some transmission paths or shards fail.

The project also explores relay-based communication, network congestion management, fault injection, transfer recovery, and performance evaluation.

MPOS is developed as an educational prototype for studying secure communication, fault tolerance, and multi-path networking.

⸻

2. Problem Statement

Traditional file-transfer systems generally transmit data over a single connection or rely on conventional transport mechanisms for reliability.

Such systems can experience interruptions when a connection fails, and sensitive data requires appropriate protection during transmission.

MPOS addresses these challenges by combining:

* Data redundancy: Recovering the original file from a subset of transmitted shards.
* Encryption: Protecting file contents and communication between system components.
* Multi-path transmission: Distributing shards across multiple relay paths.
* Fault tolerance: Exploring recovery when individual paths or relays fail.
* Network management: Studying congestion, transfer timing, and communication reliability.

The goal is to investigate how these mechanisms can work together in a modular file-transfer architecture.

⸻

3. Objectives

The primary objectives of MPOS are:

1. Implement Reed–Solomon erasure coding for redundant file transmission.
2. Encrypt file shards using authenticated encryption.
3. Implement onion-style encryption across multiple relay hops.
4. Establish communication between sender, relay nodes, and receiver.
5. Support transmission over multiple paths.
6. Reconstruct files from a sufficient number of valid shards.
7. Simulate relay and network failures.
8. Investigate recovery mechanisms and congestion-management strategies.
9. Provide a graphical interface for interacting with the system.
10. Evaluate transfer reliability and performance under different conditions.

⸻

4. System Architecture

The MPOS architecture consists of a sender, an erasure-coding and encryption pipeline, multiple relay paths, and a receiver.

Transmission workflow

                 SENDER
                    |
                    v
             Input File
                    |
                    v
          Reed-Solomon Encoder
                    |
                    v
              Data Shards
                    |
                    v
           AES-256-GCM Encryption
                    |
                    v
            Onion Encryption
                    |
                    v
          Multi-Path Dispatcher
                    |
          +---------+---------+
          |         |         |
          v         v         v
       Guard 1   Guard 2   Guard 3
          |         |         |
          v         v         v
       Middle 1  Middle 2  Middle 3
          |         |         |
          v         v         v
        Exit 1    Exit 2    Exit 3
          |         |         |
          +---------+---------+
                    |
                    v
                 RECEIVER
                    |
                    v
            Shard Validation
                    |
                    v
             Decryption
                    |
                    v
          Reed-Solomon Decoder
                    |
                    v
           Reconstructed File

The diagram illustrates the intended logical workflow. The number of paths, relay topology, and exact processing order depend on the configured implementation.

Main components

Component	Responsibility
Sender	Reads the input file and initiates transmission.
Reed–Solomon encoder	Generates data and redundant shards.
Shard encryption	Protects shard contents using authenticated encryption.
Onion encryption	Applies layered encryption for configured relay hops.
Directory and path management	Maintains node information and selects communication paths.
Relay nodes	Forward traffic through intermediate network hops.
Receiver	Collects and validates incoming shards.
Reed–Solomon decoder	Reconstructs the original data when enough valid shards are available.
Fault injector	Simulates configured failure scenarios.
Congestion-management module	Supports network transmission and congestion-related experiments.
GUI	Provides a graphical interface for interacting with the application.

⸻

5. Technologies Used

Technology	Purpose
Python	Core implementation language
Reed–Solomon erasure coding	Data redundancy and recovery
AES-256-GCM	Authenticated encryption of data
Onion encryption	Layered protection across relay hops
TCP sockets	Network communication
Asynchronous programming	Supporting concurrent communication where implemented
PySide6 / Qt	Graphical user interface
pytest	Automated testing
zfec	Erasure-coding functionality where used by the implementation
Git and GitHub	Version control and source-code hosting

The actual dependencies are defined by requirements.txt and pyproject.toml.

⸻

6. Project Structure

The repository is organized into a modular Python package, automated tests, and phase-wise development directories.

MPOS_share/
│
├── mpos/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py
│   ├── engine.py
│   ├── errors.py
│   ├── events.py
│   │
│   ├── coding/
│   │   ├── __init__.py
│   │   ├── erasure.py
│   │   └── shard_crypto.py
│   │
│   ├── crypto/
│   │   ├── __init__.py
│   │   ├── keys.py
│   │   └── onion.py
│   │
│   ├── faults/
│   │   ├── __init__.py
│   │   ├── injector.py
│   │   └── scenarios.py
│   │
│   ├── gui/
│   │   ├── __init__.py
│   │   ├── app.py
│   │   └── bridge.py
│   │
│   └── net/
│       ├── congestion.py
│       ├── cwnd_trace.py
│       ├── directory.py
│       ├── framing.py
│       ├── guards.py
│       ├── health.py
│       ├── journal.py
│       ├── link.py
│       ├── messages.py
│       ├── node.py
│       ├── paths.py
│       ├── ports.py
│       └── transfer.py
│
├── tests/
│   ├── unit/
│   ├── integration/
│   └── gui/
│
├── Phase_1/
├── Phase_2/
├── Phase_3/
├── Phase_4/
├── Phase_5/
├── Phase_6/
├── Phase_7/
├── Phase_8/
├── Phase_9/
│
├── Input/
├── keys/
├── test_files/
├── pyproject.toml
├── requirements.txt
├── .gitignore
└── README.md

Important modules

* mpos/coding/erasure.py — Reed–Solomon encoding and decoding.
* mpos/coding/shard_crypto.py — Shard-level cryptographic operations.
* mpos/crypto/keys.py — Cryptographic key-related operations.
* mpos/crypto/onion.py — Onion-encryption functionality.
* mpos/net/transfer.py — File-transfer functionality.
* mpos/net/node.py — Network-node functionality.
* mpos/net/paths.py — Path management.
* mpos/net/directory.py — Node-directory management.
* mpos/net/congestion.py — Congestion-related functionality.
* mpos/faults/injector.py — Fault-injection functionality.
* mpos/gui/app.py — GUI application.
* mpos/cli.py — Command-line interface.
* mpos/engine.py — Core transfer engine.

⸻

7. Development Phases

The project evolved through multiple phases, progressively introducing more advanced networking and reliability features.

Phase	Main focus
Phase 1	Reed–Solomon encoding and decoding
Phase 2	File encryption and decryption
Phase 3	Multiple TCP connections
Phase 4	Guard, middle, and exit relay topology
Phase 5	Onion encryption across relay hops
Phase 6	Integrated transfer workflow and cross-device communication
Phase 7	Graphical user interface
Phase 8	Performance experiments and comparisons
Phase 9	Additional experiments and evaluation

The phase descriptions summarize the development plan. The functionality available in each phase should be verified against its source code and documentation.

⸻

8. Installation and Setup

Prerequisites

* Python 3.11 or another Python version supported by the project dependencies.
* Git.
* macOS, Linux, or another compatible Python environment.
* Network access for installing dependencies.
* Additional devices or virtual machines for cross-device testing.

Step 1: Clone the repository

After publishing the project to GitHub:

git clone YOUR_GITHUB_REPOSITORY_URL
cd MPOS_share

Replace the placeholder with the actual repository URL and use the repository’s actual directory name.

Step 2: Create a virtual environment

python3 -m venv venv

Activate it on macOS or Linux:

source venv/bin/activate

Step 3: Install dependencies

Upgrade pip:

python -m pip install --upgrade pip

Install the required packages:

pip install -r requirements.txt

Install the project in editable mode:

pip install -e .

This makes the package available to the Python environment and should expose the mpos command if the project entry point is configured correctly.

Step 4: Verify installation

mpos --help

If the command is unavailable, verify that the virtual environment is activated and that the installation completed successfully.

⸻

9. Running the Application

The exact commands depend on the configuration and command-line options in the final version.

Option A: Inspect available commands

Start by running:

mpos --help

If the command is unavailable, try:

python -m mpos --help

Use the available help output to identify supported commands.

Option B: Run a local-cluster demonstration

The project has used the following local-cluster workflow:

mpos local-cluster

This is intended to start or prepare a local demonstration cluster, depending on the implementation.

In another terminal, a previously documented transfer command is:

mpos send \
  --id alice \
  --directory data/cluster/nodes.json \
  --data-dir data/cluster \
  --bind 127.0.0.1 \
  --file Input/Input.txt \
  --to bob

Run this command only if these options are supported by your installed version. Start any receiver or relay processes required by the cluster configuration before initiating the transfer.

Option C: Launch the graphical interface

The project contains a GUI module under mpos/gui/.

Inspect the available CLI commands first:

mpos --help

If the application provides a gui subcommand, use the exact syntax shown in its help output.

Alternatively, inspect the GUI entry point:

sed -n '1,200p' mpos/gui/app.py

This helps determine whether the GUI can be launched directly as a Python module or through a CLI command.

⸻

10. Cross-Device File Transfer

MPOS is designed to support communication between nodes running on different devices, subject to the network configuration.

General setup

1. Install the project and dependencies on each participating device.
2. Configure the sender, receiver, and relay nodes.
3. Ensure that each node has the correct reachable address and port.
4. Configure the node directory on all participating devices.
5. Start the required relay and receiver processes.
6. Initiate the transfer from the sender.
7. Monitor the transfer and verify the reconstructed file.

Network considerations

For devices connected to the same Wi-Fi network or hotspot:

* Use reachable local IP addresses instead of loopback addresses when communicating across devices.
* Ensure that the required ports are accessible.
* Check the host firewall.
* Verify that the Wi-Fi network does not isolate connected clients.
* Ensure that node-directory information matches the actual network configuration.

Important: 127.0.0.1 refers to the local device. It cannot be used as the remote address for another laptop.

Verify the result

After the transfer completes, compare the original and reconstructed files using SHA-256:

shasum -a 256 Input/Input.txt
shasum -a 256 PATH_TO_RECEIVED_FILE

Replace PATH_TO_RECEIVED_FILE with the actual path of the reconstructed file.

Matching hashes indicate that the two files have identical contents.

⸻

11. Security Mechanisms

Reed–Solomon Erasure Coding

Reed–Solomon coding introduces redundancy by generating additional shards from the original data.

With a configuration of k required shards and n total shards, the system can reconstruct the original data from any k valid shards, provided that the coding implementation and shard metadata are correct.

For example, a 4-of-6 configuration creates six shards, of which any four valid shards should be sufficient for reconstruction.

This improves resilience against shard loss, but does not by itself guarantee successful transfer under every failure condition.

AES-256-GCM

AES-256-GCM provides authenticated encryption.

It protects confidentiality and enables integrity verification when implemented with correct key management, unique nonces, and authentication-tag validation.

The project applies encryption to file data or shards according to its configured workflow.

Onion Encryption

Onion encryption applies multiple cryptographic layers associated with relay hops.

Each relay processes the layer intended for that hop and forwards the remaining protected message according to the routing protocol.

This is intended to limit what an individual relay can learn about the complete route or message.

However, onion encryption alone does not guarantee anonymity. Traffic analysis, endpoint compromise, configuration errors, and other attacks remain possible.

Multi-Path Transmission

Multiple paths distribute traffic across different relay routes.

This can improve resilience when individual paths fail, provided that sufficient valid shards reach the receiver and the paths have the intended independence.

Key Management

Private keys, encryption keys, identity files, and authentication credentials must be protected.

Do not publish private key files or live node credentials in a public repository.

⸻

12. Fault Tolerance and Recovery

MPOS includes modules for fault injection and scenario-based experiments.

Potential scenarios include:

* Relay failure.
* Communication interruption.
* Missing shards.
* Delayed responses.
* Transfer recovery.
* Path unavailability.

The intended objective is to investigate how the system behaves when network components fail.

Reed–Solomon coding can compensate for missing shards up to the limits of the selected coding configuration. Other failures may require retry mechanisms, path changes, or recovery logic.

Actual recovery behavior should be evaluated through the corresponding tests and experiments.

⸻

13. Congestion Management

The project includes a congestion-related module and congestion-window tracing functionality.

These components provide a basis for investigating how network conditions affect transmission.

Relevant measurements may include:

* Congestion-window size.
* Round-trip time.
* Transfer duration.
* Throughput.
* Packet or shard loss.
* Recovery time.

The presence of a congestion-management module does not automatically mean that the system implements a complete TCP congestion-control algorithm. The actual algorithm and its relationship to the underlying TCP transport should be documented according to the implementation.

⸻

14. Testing

The repository includes unit, integration, and GUI tests.

Run the test suite

Install the test dependencies if they are not already included:

pip install pytest

Run all tests:

python -m pytest -v

Run unit tests

python -m pytest tests/unit -v

Run integration tests

python -m pytest tests/integration -v

Run GUI tests

python -m pytest tests/gui -v

GUI tests may require additional platform dependencies or a configured Qt environment.

Test categories

Test category	Purpose
Erasure coding	Verify shard encoding and reconstruction
Cryptography	Verify encryption, decryption, and key-related operations
Network framing	Verify message framing and parsing
Node communication	Test interactions between network nodes
File transfer	Test transfer and reconstruction workflows
Fault tolerance	Test behavior under simulated failures
Congestion	Test congestion-related logic
GUI	Test graphical components and application interactions

Record the actual test results before claiming that the complete test suite passes.

⸻

15. Performance Evaluation

The project includes a Phase 8 directory containing sample result files.

Performance experiments can compare configurations using metrics such as:

Metric	Purpose
Transfer time	Measures end-to-end transfer duration
Throughput	Measures the rate of successful data delivery
Recovery success	Measures whether the original file can be reconstructed
Shard loss tolerance	Evaluates resilience to missing shards
Relay failure recovery	Examines behavior after a relay becomes unavailable
CPU and memory usage	Measures computational overhead
Encryption overhead	Evaluates the cost of cryptographic processing

A meaningful evaluation should compare multiple configurations under controlled conditions, repeat measurements, and report the actual results.

Sample CSV files should be clearly identified as experimental data, simulated output, or illustrative examples as appropriate.

⸻

16. Limitations

MPOS is an educational prototype, and its guarantees depend on the implementation and test environment.

Important limitations to consider include:

* The system has not necessarily undergone an independent security audit.
* A simulated relay network is not equivalent to a production anonymity network.
* Multiple paths may share physical network infrastructure.
* Redundancy increases the amount of data transmitted.
* Encryption and erasure coding introduce computational overhead.
* File reconstruction requires enough valid shards.
* Network reachability depends on addressing, firewall, and routing configuration.
* Performance results depend on hardware, network conditions, and experiment configuration.

The prototype should not be assumed to provide production-grade anonymity or security without further analysis and testing.

⸻

17. Future Enhancements

Potential improvements include:

1. Adaptive path selection based on network conditions.
2. More comprehensive congestion-aware scheduling.
3. Improved relay health monitoring and failure recovery.
4. Automated performance benchmarking.
5. Stronger key provisioning and identity management.
6. Expanded cross-device testing.
7. More detailed GUI-based monitoring and visualization.
8. Improved logging and transfer diagnostics.
9. Reproducible experiments across different network conditions.
10. Security testing and independent review.

These are possible extensions rather than claims about completed functionality.

⸻

18. Screenshots and Demonstrations

Add screenshots demonstrating the actual implementation.

Suggested evidence includes:

* Application GUI.
* Successful local file transfer.
* Successful cross-device transfer.
* Reed–Solomon shard generation.
* Decryption and file reconstruction.
* Relay topology or routing information.
* Fault-injection results.
* Performance graphs.
* Automated test results.

A recommended repository directory is:

docs/
└── screenshots/
    ├── gui.png
    ├── local-transfer.png
    ├── cross-device-transfer.png
    ├── reconstruction.png
    ├── fault-simulation.png
    └── performance-results.png

Use screenshots from actual executions and avoid presenting illustrative images as experimental evidence.

⸻

19. References

The following documentation provides background on the technologies used in the project.

1. Python Documentation — https://docs.python.org/3/
2. Pytest Documentation — https://docs.pytest.org/
3. PySide6 Documentation — https://doc.qt.io/qtforpython-6/
4. PyCA Cryptography Documentation — https://cryptography.io/
5. zfec Project — https://github.com/tahoe-lafs/zfec
6. Git Documentation — https://git-scm.com/doc
7. GitHub Documentation — https://docs.github.com/

Consult the documentation for the exact dependency versions and algorithms used in the final implementation.

⸻

20. Contributors

Project: Multi-Path Onion Slicing (MPOS)

Team Members:

* Add the full name of Team Member 1.
* Add the full name of Team Member 2.
* Add the full name of Team Member 3.
* Add additional team members as applicable.

Institution: Add your institution name.

Course: Computer Networks Laboratory.

⸻

21. License

Choose an appropriate license before publishing the project.

If the project is intended for academic use only, specify the applicable terms clearly. Do not assume that third-party libraries and dependencies share the same license as the project.

⸻

22. Acknowledgements

This project was developed as part of a computer networks laboratory initiative to explore secure file transfer, erasure coding, onion-style routing, network reliability, and fault tolerance.
