# Agentic Cloud Resource Manager — Stage 1

Stage 1 is a controlled GCP experiment comparing:

1. **GKE Autopilot + HPA**
2. **Compute Engine regional MIG + custom resource controller**

Both systems run the **same Docker image**, receive the **same deterministic website traffic trace**, start from the **same minimum capacity**, and are capped at the same application resource envelope.

## Experimental question

> Under the same website workload and maximum application compute budget, how do GKE Autopilot and a custom resource manager compare on latency, errors, scaling behavior, resource consumption, and estimated cost?

This stage intentionally uses a simple CPU-threshold controller. It establishes the benchmark harness before adding forecasting, multi-agent coordination, learning, or RL.

---

## Shared resource cap

A resource unit is:

- 2 application vCPU
- ~2 GiB application memory

Maximum = **4 units**:

- **GKE:** max 4 Pods × 2 vCPU / 2 GiB = 8 vCPU / 8 GiB
- **MIG:** max 4 `e2-highcpu-2` VMs ≈ 8 vCPU / 8 GiB

GKE also has a namespace `ResourceQuota` limiting requested/limited CPU and memory to 8 CPU / 8 GiB.

The agent controller enforces `MIG_MAX=4`.

The small controller VM is *not* part of the application capacity cap; its cost is accounted for separately.

---

## Workload

`scenarios/daily_24m.csv` compresses a synthetic 24-hour website day into **24 minutes**.

It contains 1-second offered-load targets and is generated from `daily_phases.csv` with a fixed random seed.

Typical phases:

- quiet night
- morning ramp
- morning traffic
- lunch spike
- afternoon
- evening ramp
- evening peak
- late traffic collapse

Peak traffic is about 700 requests/s.

Each request calls:

```text
/work?cpu_ms=8
```

The web app deliberately burns approximately 8 ms of CPU per request so scaling behavior is visible without requiring a database or other external bottleneck.

The load generator is **open-loop**: if the target becomes slow, it keeps offering the scheduled request rate rather than waiting for responses. This avoids making an overloaded server appear healthy simply because a closed-loop client slows down.

---

## Repository layout

```text
agentic-cloud-stage1/
├── app/
│   ├── app.py
│   ├── Dockerfile
│   └── requirements.txt
├── agent/
│   ├── controller.py
│   ├── Dockerfile
│   └── requirements.txt
├── gke/
│   ├── namespace.yaml
│   ├── quota.yaml
│   ├── deployment.yaml.tpl
│   ├── service.yaml
│   └── hpa.yaml
├── mig/
│   ├── startup.sh.tpl
│   └── controller-startup.sh.tpl
├── loadgen/
│   ├── run.py
│   └── requirements.txt
├── scenarios/
│   ├── daily_phases.csv
│   ├── daily_24m.csv
│   └── make_trace.py
├── scripts/
│   ├── setup_gcp.sh
│   ├── deploy_gke.sh
│   ├── deploy_mig.sh
│   ├── run_experiment.sh
│   ├── observe.py
│   ├── compare.py
│   ├── compare_last.sh
│   └── cleanup.sh
├── .env.example
└── pricing.example.json
```

---

# 1. Prerequisites

The easiest place to run the orchestration scripts is **Google Cloud Shell**.

You need:

- a GCP project with billing enabled
- `gcloud`
- `kubectl`
- Python 3
- permission to create Compute Engine, GKE, IAM service-account, Artifact Registry, Cloud Build, and load-balancer resources

From the repository root:

```bash
cp .env.example .env
```

Edit `.env` and set:

```bash
PROJECT_ID=your-real-project-id
```

The default region is Warsaw:

```text
europe-central2
```

Install the load-generator dependency:

```bash
python3 -m pip install -r loadgen/requirements.txt
```

---

# 2. Build images and enable GCP APIs

```bash
./scripts/setup_gcp.sh
```

This enables the required APIs, creates an Artifact Registry repository, and builds:

```text
arm-web:stage1
arm-controller:stage1
```

---

# 3. Deploy GKE Autopilot

```bash
./scripts/deploy_gke.sh
```

The deployment includes:

- Autopilot cluster
- one initial web Pod
- HPA target CPU = 60%
- min replicas = 1
- max replicas = 4
- 2 vCPU / 2 GiB per Pod
- ResourceQuota = 8 vCPU / 8 GiB
- external GCE HTTP Ingress / application load balancer

The target URL is saved to:

```text
.state/gke_url
```

Inspect it with:

```bash
cat .state/gke_url
```

---

# 4. Deploy the agent-managed Compute Engine system

```bash
./scripts/deploy_mig.sh
```

This creates:

- regional managed instance group
- `e2-highcpu-2` application VMs
- initial MIG size = 1
- maximum controller size = 4
- global HTTP load balancer
- health check
- controller service account
- small controller VM
- controller container

The Compute Engine autoscaler is intentionally not used. The custom controller owns the MIG target size.

The target is saved to:

```text
.state/mig_url
```

View controller decisions:

```bash
gcloud compute ssh arm-controller \
  --zone europe-central2-a \
  --command='docker logs -f arm-controller'
```

A decision resembles:

```json
{
  "average_cpu": 0.73,
  "action": "scale_up",
  "target_size": 2,
  "proposed_size": 3
}
```

Stage-1 policy:

```text
CPU > 65% -> +1 resource unit
CPU < 30% -> -1 resource unit
otherwise  -> hold
```

with a 120-second cooldown.

---

# 5. Run exactly the same scenario against GKE

```bash
./scripts/run_experiment.sh gke
```

Before the workload starts, the script resets application capacity to one resource unit and waits for the configured warm-up period.

Results are written under:

```text
results/gke/<timestamp>/
```

including:

```text
summary.json
per_second.csv
resources.csv
```

---

# 6. Run the same scenario against the custom agent

```bash
./scripts/run_experiment.sh agent
```

Results go to:

```text
results/agent/<timestamp>/
```

The exact same `daily_24m.csv` is replayed.

For stronger results, alternate run order across repetitions:

```text
Run 1: GKE -> Agent
Run 2: Agent -> GKE
Run 3: GKE -> Agent
...
```

and run several repetitions rather than drawing conclusions from one trial.

---

# 7. Compare the latest runs

Without price inputs:

```bash
./scripts/compare_last.sh
```

The comparison reports:

- attempted requests
- success rate
- p50 / p95 / p99 latency
- per-second SLO violation fraction
- peak resource units
- mean resource units
- observed scaling changes
- vCPU-hours
- GiB-hours

Default SLO:

```text
p99 <= 500 ms
error rate <= 1% per evaluated second
```

Machine-readable output is written to:

```text
results/comparison.json
```

---

# 8. Add current GCP prices

Do **not** hard-code historical GCP prices into the experiment.

Copy:

```bash
cp pricing.example.json pricing.json
```

Fill the current rates for your selected region:

```json
{
  "gke_autopilot_vcpu_hour": 0.0,
  "gke_autopilot_gib_hour": 0.0,
  "gke_cluster_hour": 0.0,
  "mig_e2_highcpu_2_vm_hour": 0.0,
  "agent_controller_vm_hour": 0.0,
  "load_balancer_hour": 0.0
}
```

Then:

```bash
./scripts/compare_last.sh
```

It additionally calculates:

- estimated run cost
- estimated cost per 1 million successful requests

The explicit pricing file makes the experiment reproducible even when cloud prices change.

---

# Metrics interpretation

Do not declare a winner using cost alone.

A controller that turns servers off can produce an excellent infrastructure bill and terrible service.

At minimum compare:

```text
cost
success rate
p99 latency
SLO violation time
vCPU-hours
scaling events
```

A useful eventual objective is:

```text
minimum cost subject to SLO compliance
```

rather than simply minimum compute consumption.

---

# Fairness controls

Stage 1 deliberately controls the following variables:

| Variable | GKE | Agent/MIG |
|---|---|---|
| Application image | same | same |
| Request endpoint | same | same |
| Traffic trace | same | same |
| Initial resource units | 1 | 1 |
| Resource unit | 2 vCPU / ~2 GiB | 2 vCPU / ~2 GiB |
| Maximum units | 4 | 4 |
| Maximum application CPU | 8 vCPU | 8 vCPU |
| Maximum application memory | ~8 GiB | ~8 GiB |
| Public traffic entry | GCP HTTP load balancer | GCP HTTP load balancer |

Not everything is identical, by design. GKE Autopilot and a VM MIG have different provisioning, billing, orchestration, and scaling mechanics. Those differences are part of the platform comparison and should be documented when interpreting results.

---

# Important Stage-1 limitations

1. The custom controller is not yet a multi-agent system.
2. It reacts to Compute Engine CPU metrics, which have monitoring delay.
3. Workload is CPU-bound and stateless.
4. No database is included.
5. No cache is included.
6. No Spot VMs are used.
7. No predictive scaling is used.
8. No RL or online training is used.
9. Price comparison is an estimate based on supplied current rates, not Cloud Billing invoice reconciliation.
10. One 24-minute run is not statistically sufficient; repeat experiments.

These are intentional constraints for the benchmark foundation.

---

# Stage 2 after this benchmark works

The next controller can consume more signals:

```text
request rate
CPU
p99 latency
rate-of-change of traffic
current VM count
provisioning delay
```

Then compare:

```text
GKE Autopilot + HPA
vs
Stage-1 CPU controller
vs
predictive agent
vs
multi-agent manager
```

Only after the benchmark is trustworthy should self-learning/RL be introduced.

---

# Cleanup

These resources incur GCP charges. Remove them when finished:

```bash
./scripts/cleanup.sh
```

The script intentionally keeps the Artifact Registry repository and IAM service accounts so images and configuration can be reused. Delete those separately if you no longer need them.
