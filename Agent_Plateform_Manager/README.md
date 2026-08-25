# Agent Platform Manager

This repository runs a controlled GCP experiment comparing two scaling systems:

1. **GKE Autopilot with a CPU-based Horizontal Pod Autoscaler (HPA)**
2. **A regional Compute Engine managed instance group (MIG) controlled by Resource Manager ADK v1**

Both systems build the same stateless web application from `app/`, receive the
same deterministic open-loop workload, start at one resource unit, and are
capped at four resource units.

## Experiment design

One application resource unit is approximately:

- 2 vCPU
- 2 GiB memory

| Property | GKE case | Agent case |
|---|---|---|
| Compute unit | Pod requesting 2 vCPU / 2 GiB | `e2-highcpu-2` VM |
| Initial units | 1 | 1 |
| Maximum units | 4 | 4 |
| Scaling controller | GKE HPA | ADK v1 on Cloud Run |
| Live signal | Pod CPU utilization | Compute Engine CPU utilization |
| Public endpoint | GCP load balancer | GCP HTTP load balancer |

The application exposes `/work?cpu_ms=8`. Each request deliberately consumes
approximately 8 ms of CPU, making scaling behavior observable without adding a
database or another bottleneck.

The workload generator in `experiment.py` is open-loop: slow responses do not
reduce the offered request rate. Scenario phases, durations, noise, and RPS are
defined in `scenarios/stage1.json`.

## Repository layout

```text
Agent_Platform_Manager/
|-- app/                              # Shared benchmark web application
|-- terraform/                        # GKE Autopilot deployment
|-- resource_manager_adk/
|   |-- README.md                     # Agent release catalog
|   `-- v1/                           # Resource Manager ADK release 1.0.0
|       |-- resource_manager/         # Agent, policy, tools, and Cloud Run API
|       |-- deployment/terraform/     # Agent, MIG, and load-balancer deployment
|       |-- tests/
|       |-- Dockerfile
|       `-- pyproject.toml
|-- scenarios/
|   `-- stage1.json                   # Active experiment scenarios
|-- tests/                            # Unified runner tests
|-- experiment.py                     # GKE / agent / both experiment runner
`-- experiment-requirements.txt
```

## Prerequisites

Google Cloud Shell is the simplest deployment environment. You need:

- A GCP project with billing enabled
- Terraform 1.7 or newer
- `gcloud` authenticated to the project
- `kubectl`
- Python 3.11 or newer
- Permission to enable APIs and create GKE, Compute Engine, Cloud Run,
  Cloud Scheduler, IAM, Artifact Registry, Cloud Build, Storage, Monitoring,
  and load-balancer resources

Install the experiment dependency from the repository root:

```bash
python3 -m pip install -r experiment-requirements.txt
```

The default region is `europe-central2`.

## Deploy the GKE case

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
```

Set the real project ID in `terraform.tfvars`, then deploy:

```bash
terraform init
terraform fmt -check
terraform validate
terraform plan -out=tfplan
terraform apply tfplan
cd ..
```

Useful outputs:

```bash
terraform -chdir=terraform output cluster_name
terraform -chdir=terraform output application_url
```

This stack builds the application image and creates the Autopilot cluster,
namespace, resource quota, deployment, HPA, load-balancer service, and external
IP.

## Deploy the Agent v1 case

```bash
cd resource_manager_adk/v1/deployment/terraform
cp terraform.tfvars.example terraform.tfvars
```

Set the real project ID and review the region, MIG name, capacity bounds, CPU
thresholds, cooldown, model, and machine type. Then deploy the safe initial
configuration:

```bash
terraform init
terraform fmt -check
terraform validate
terraform plan -out=tfplan
terraform apply tfplan
cd ../../../..
```

This stack creates:

- The shared workload container image
- A Container-Optimized OS instance template
- A regional MIG initialized at `min_units`
- The workload health check, firewall, load balancer, and external IP
- Resource Manager ADK v1 as an authenticated Cloud Run service
- A private Cloud Storage bucket for durable cooldown state
- A Cloud Scheduler job that invokes the agent
- Least-privilege runtime and scheduler identities

It does not create a GKE cluster, native Compute Engine autoscaler, or legacy
controller VM. Terraform ignores MIG `target_size` after creation so it does
not reverse live decisions made by the agent.

The initial deployment is intentionally paused and dry-run:

```hcl
scheduler_paused           = true
enable_live_scaling        = false
exclusive_scaler_confirmed = false
```

Validate agent observations in dry-run mode first. Before an agent scaling
experiment, confirm that no other system can resize the MIG, then apply:

```hcl
scheduler_paused           = false
enable_live_scaling        = true
exclusive_scaler_confirmed = true
```

Useful outputs:

```bash
terraform -chdir=resource_manager_adk/v1/deployment/terraform output agent_version
terraform -chdir=resource_manager_adk/v1/deployment/terraform output workload_url
terraform -chdir=resource_manager_adk/v1/deployment/terraform output service_url
terraform -chdir=resource_manager_adk/v1/deployment/terraform output scaling_mode
terraform -chdir=resource_manager_adk/v1/deployment/terraform output scheduler_state
```

See `resource_manager_adk/v1/README.md` for the detailed safety and deployment
notes.

## Run experiments

Run experiments from the repository root. The runner reads endpoints and
settings from the applied Terraform states.

GKE only:

```bash
python3 experiment.py \
  --system gke \
  --scenario daily_normal \
  --rps-scale 0.2
```

Agent-managed MIG only:

```bash
python3 experiment.py \
  --system agent \
  --scenario daily_normal \
  --rps-scale 0.2
```

Both systems with the same deterministic trace:

```bash
python3 experiment.py \
  --system both \
  --scenario daily_normal \
  --rps-scale 0.2 \
  --runs 2
```

`both` runs the platforms sequentially so they do not compete for workload
generator capacity. Odd repetitions run GKE then agent; even repetitions run
agent then GKE to reduce ordering bias.

Before an agent run, the runner:

1. Resets the MIG to `min_units`.
2. Waits for the target, allocated, and ready instance counts to stabilize.
3. Waits for any stored scaling cooldown to expire.
4. Verifies the workload `/healthz` endpoint.
5. Confirms Scheduler is enabled and scaling mode is live.

Use `--allow-agent-dry-run` only when intentionally measuring a non-scaling
control.

Available scenarios are:

```text
daily_normal
morning_ramp
flash_crowd
sudden_drop
repeated_bursts
```

Use `--scenario all` to run every scenario.

## Results

Results are written under:

```text
results/gke/<scenario>/<timestamp>_runNN/
results/agent/<scenario>/<timestamp>_runNN/
```

Every run contains:

- `metadata.json` - target, Terraform outputs, scenario settings, and agent version
- `trace.csv` - exact offered RPS for every second
- `traffic.csv` - requests, errors, latency percentiles, and scheduler lateness
- `summary.json` - performance, SLO, capacity, and scaling summary
- `gke_metrics.csv` or `agent_metrics.csv` - platform-specific capacity samples

The default SLO evaluated by the runner is:

```text
per-second p99 latency <= 500 ms
and error rate <= 1%
```

The current runner reports resource usage but does not calculate invoice cost.
Use GCP Billing Reports or Detailed Billing Export to BigQuery to attribute
Cloud Run, Vertex AI, Compute Engine, load-balancing, and supporting-service
costs. `pricing.example.json` is retained only as an optional manual-rate
template.

## Validation

Run the experiment-runner tests from the repository root:

```bash
python3 -m pytest tests/test_experiment.py
```

Run the Agent v1 tests from its release directory:

```bash
cd resource_manager_adk/v1
python3 -m pip install -e ".[dev]"
pytest
cd ../..
```

Always run `terraform fmt -check`, `terraform validate`, and review a saved
Terraform plan before applying either stack.

## Fairness controls

- Both systems build the application from the same `app/` source.
- Both receive the same deterministic trace and request endpoint.
- Both start from one resource unit and allow at most four.
- Both have an approximately 8-vCPU / 8-GiB maximum application envelope.
- Both expose the application through a GCP HTTP load balancer.
- Repeated `both` runs alternate platform order.

GKE and a VM MIG still differ in provisioning time, billing, orchestration,
metric delay, and platform overhead. Those differences are part of the
comparison and should be retained when interpreting results.

## Current limitations

- Resource Manager v1 is a single ADK agent, not a multi-agent system.
- The live agent policy uses Compute Engine CPU metrics only.
- Monitoring and Scheduler delays affect agent reaction time.
- The workload is CPU-bound and stateless, with no database or cache.
- No Spot VMs, forecasting, reinforcement learning, or online training are used.
- A single run is not statistically sufficient; use repeated, alternating runs.
- Dollar-cost comparison requires billing data outside the experiment runner.

## Cleanup

These resources incur GCP charges. Destroy the Agent stack and GKE stack when
they are no longer needed:

```bash
terraform -chdir=resource_manager_adk/v1/deployment/terraform plan -destroy -out=tfplan-destroy
terraform -chdir=resource_manager_adk/v1/deployment/terraform apply tfplan-destroy

terraform -chdir=terraform plan -destroy -out=tfplan-destroy
terraform -chdir=terraform apply tfplan-destroy
```

Review both destroy plans carefully before applying them.
