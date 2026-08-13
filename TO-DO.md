# Capabilities
[[01-system-overview#1.3 Scope]]
- [x] USB-C inline monitoring.
- [ ] Local AI inference 
- [ ] Battery health assessment
- [ ] Charger quality assessment
- [x] Cable quality assessment
- [x] Charging efficiency analysis
- [x] Historical trends
- [x] Optional cloud synchronization

> [!NOTE] NOTE
> All of the above are done using the following components:
> - INA219
> - CH224K
> - Raspberry-pi code readings and local storage
> - Sending data to a cloud database.


# Functional components
[[02-functional-architecture#2.4 Functional Components]]
- [x] Hardware Platform
- [x] Telemetry Engine
- [x] AI Inference Engine
- [x] Local Storage
- [ ] User Experience
- [x] Cloud Synchronization

> [!NOTE] NOTE
> Done using the following components:
> - INA219
> - CH224K
> - Raspberry-pi code readings and local storage
> - Mathematical formulas to analyze the readings and give confidence
> - Sending data to a cloud database.
# Error handling
[[03-functional-requirements#3.11 Error Handling]]
- [x] Sensor unavailable.
- [x] AI unavailable.
- [x] Cloud unavailable.
- [x] Invalid measurement.
- [x] Power interruption.

> [!NOTE] NOTE
> All of the above are done using the following components:
> - Self check is conducted to check all sensors are working
> - Current architecture is AI not available fall back
> - When cloud is not available a queue is created to send data to cloud when available
> - In case of no power, NO_SOURCE state is initiated and those readings are not considered in calculations
# Hardware goals
[[04-hardware-architecture#4.2 Hardware Goals]]
- [x] Single monitored port
- [ ] Appliance operation
- [ ] Reliability
- [x] Accuracy
- [ ] Safety
- [ ] Expandability

> [!NOTE] NOTE
> All of the above are done using the following components:
> - Current architecture includes single port monitoring from CH224K.
> - The results are currently accurate and the mathematical results are suitable for model training.
# Telemetry objectives
[[05-telemetry-engine#5.2 Objectives]]
- [ ] Continuous acquisition
- [x] High Fidelity
- [x] Deterministic Timing
- [ ] Fault tolerance
- [x] AI-Ready

> [!NOTE] NOTE
> All of the above are done using the following components:
> - Faulty signals are ignored supporting high fidelity
> - Timing is recorded for each reading
> - The results and mathematical calculations are AI ready.

# Telemetry Sample attributes
[[05-telemetry-engine#5.5 Sample Attributes]]
- [x] Timestamp
- [x] Voltage
- [x] Current
- [x] Power
- [ ] Energy
- [x] Session ID
- [x] Sample Quality

> [!NOTE] NOTE
> All of these are stored in the database.

# Telemetry validation rules
[[05-telemetry-engine#5.7 Validation Rules]]
- [x] Range check
- [ ] Missing Sample Check
- [ ] Timestamp Check
- [x] Sensor Health Check
- [x] Outlier detection

> [!NOTE] NOTE
> All of these are done using the ranges set in config.toml, the debouce time detection, and the initial checks.

# Feature Generation
[[05-telemetry-engine#5.8 Feature Generation]]
- [x] Average voltage
- [x] Average current
- [x] Peak Current
- [ ] Peak Power
- [x] Energy delivered
- [x] Charge duration
- [x] Voltage stability
- [ ] Current stability

> [!NOTE] NOTE
> The above readings are calculated withing the code, for calculations and some of them are shown in the output as well.

# Telemetry Error Handling
[[05-telemetry-engine#5.10 Error Handling]]
- [ ] Sensor timeout
- [x] Invalid measurement
- [ ] storage unavailable
- [x] cloud unavailable
- [x] clock drift

> [!NOTE] NOTE
> The readings are marked as valid and in-valid based on data validation.
> Cloud unavailability is also catered by syncing when the cloud is available.
# AI objectives
[[06-ai-inference-engine#6.2 Objectives]]
- [ ] Infer health
- [x] Explain results
- [ ] Learn over time
- [x] Local operation
- [x] Extensive models

> [!NOTE] NOTE
> The results are currently explained with evidence, and tags.
> The operation is completely local.
> The current architecture is completely suitable for new models that want to work on the same mathematical results.
# AI inference domains
[[06-ai-inference-engine#6.5 Primary Inference Domains]]
- [ ] Batter health
- [ ] Degradation  trend
- [ ] Charger quality
- [x] Cable quality
- [x] Charging efficiency
- [x] Thermal behavior
- [x] Session Anomalies

> [!NOTE] NOTE
> - Cable quality is given at the end.
> - Charging efficiency is detected by evaluating the power transfer at each sample.
> - Thermal behavior is calculated using the change in resistance over time.
> - Session anomalies are detected and ignored for better mathematical calculations and avoiding fault interruptions.
# AI learning
[[06-ai-inference-engine#6.7 Learning Strategy]]
- [ ] Initial
- [ ] Adaptive
- [ ] Optional Cloud
- [ ] Continuous

# AI failure handling
[[06-ai-inference-engine#6.9 Failure Handling]]
- [ ] Missing features
- [ ] Low Confidence
- [ ] Model Failure
- [ ] Corrupt model
- [x] Cloud unavailable

# Cloud services
[[07-cloud-platform#7.4 Cloud Services]]
- [x] Device Registry
- [x] Secure Synchronization
- [ ] Firmware Distribution
- [ ] AI model Distribution
- [x] Analytics
- [ ] Notification service
- [ ] Account Management

> [!NOTE] NOTE
> All of the above are defined and stored in the cloud database.

# UX principles
[[08-user-experience#8.2 User Experience Principles]]
- [ ] Zero Configuration
- [ ] Appliance First
- [ ] Explainable AI
- [ ] Minimal Interaction
- [ ] Consistency

# App Dashboard Components
[[08-user-experience#8.5 Dashboard Components]]
- [ ] Batter health
- [ ] Charger score
- [ ] Cable score
- [ ] Charging efficiency
- [ ] Session timeline
- [ ] Recommendations

# App accessibility
[[08-user-experience#8.9 Accessibility]]
- [ ] Text
- [ ] Color
- [ ] Icons
- [ ] Language
- [ ] Navigation

# Security principles
[[09-security-privacy#9.2 Security Principles]]
- [ ] Secure by default
- [ ] Least privilege
- [ ] Defense in depth
- [x] Privacy first
- [ ] Verify everything

> [!NOTE] NOTE
> Only relevant data is collected for mathematical calculations

# Security domains
[[09-security-privacy#9.5 Security Domains]]
- [x] Device Identity
- [ ] Firmware
- [x] Communications
- [x] Local Storage
- [x] Cloud Access
- [ ] AI models

> [!NOTE] NOTE
> Unique device identities are given to the raspberry pi's
> Communication is done using the HTTPS protocol for now.
> Local storage is only accessible by the raspberry pi.
> Cloud access is only give to the registered device ids.
# Data protection
[[09-security-privacy#9.7 Data Protection]]
- [x] Device Identity
- [ ] Session data
- [ ] Cloud Traffic
- [ ] Firmware
- [ ] AI-models
- [ ] Diagnostic logs

> [!NOTE] NOTE
> Unique device IDs
# Deployment principles
[[10-deployment-operations#10.2 Operational Principles]]
- [ ] Zero touch
- [ ] Appliance first
- [ ] Local first
- [ ] Self Monitoring
- [ ] Recoverable

# Factory provisioning
[[10-deployment-operations#10.5 Factory Provisioning]]
- [ ] Identity Assignment
- [ ] Firmware Installation
- [ ] Validation
- [ ] Packaging

# Operational Health
[[10-deployment-operations#10.7 Operational Health]]
- [ ] Telemetry status
- [ ] Storage status
- [ ] Wifi Status
- [ ] AI status
- [ ] Update status

# Failure Loss
[[10-deployment-operations#10.9 Failure Recovery]]
- [ ] Power Loss
- [ ] Cloud Loss
- [ ] Failed Update
- [ ] Sensor Failure
- [ ] Storage Warning

# Test levels
[[11-verification-validation#11.4 Test Levels]]
- [x] Unit
- [x] Integration
- [ ] System
- [ ] Regression
- [ ] Acceptance

> [!NOTE] NOTE
> Done in the tests folder
# Validation areas
[[11-verification-validation#11.7 Functional Validation Areas]]
- [x] Telemetry
- [ ] AI inference
- [x] USB-C operation
- [x] Cloud Services
- [ ] User Experience
- [ ] Security

> [!NOTE] NOTE
> Telemetry results are verified and the charging behavior is completely transparent.
# AI validation
[[11-verification-validation#11.8 AI Validation]]
- [ ] Confidence Distribution
- [x] Repeatability
- [ ] Drift Detection
- [x] Explainability

# Roadmap principles
[[12-roadmap-future-capabilities#12.2 Roadmap Principles]]
- [ ] Backward Compatibility
- [ ] Incremental Delivery
- [ ] AI-first evolution
- [ ] Appliance Philosophy
- [ ] Modular Growth

