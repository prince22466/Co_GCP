# Resource Manager ADK v1

This directory is the frozen `v1` release (`1.0.0`) of the resource-manager
agent. Behavior-changing revisions should be created as a new sibling release,
not silently introduced into v1.

This Google Agent Development Kit (ADK) stack provisions a regional Compute
Engine managed instance group (MIG), evaluates its recent CPU load, and can
resize it. Each default workload unit is an `e2-highcpu-2` VM, matching the
baseline's 2-vCPU application unit without deploying GKE Autopilot.

## Runtime design

```text
Cloud Scheduler (OIDC)
        |
        v
private Cloud Run /evaluate
        |
        v
Google ADK agent -> manage_mig_capacity()
                         |
                         +-- read MIG target and rollout state
                         +-- read recent Cloud Monitoring CPU
                         +-- enforce 1-step / min / max / cooldown policy
                         +-- resize the regional MIG when live mode is enabled
```

Terraform also builds the benchmark web image, creates the Container-Optimized
OS instance template, initializes the MIG at `min_units`, and places it behind
an external HTTP load balancer. It does not create a GKE cluster, HPA, native
Compute Engine autoscaler, or legacy controller VM. After creation, Terraform
ignores MIG target-size drift because the ADK agent owns that field.

The model cannot supply a replica count to the live tool. The tool takes no
arguments, reads fresh GCP state, calculates the permitted result locally, and
refuses to act while instances or metrics are incomplete. Cooldown state is
stored in a private, versioned Cloud Storage object.

The current live signal is Compute Engine CPU. The benchmark's p99 latency and
error rate are not yet published to Cloud Monitoring, so live decisions do not
use those two signals.

## Safety defaults

- Cloud Scheduler is paused.
- `SCALING_ENABLED` is false (dry-run).
- Cloud Run requires authentication.
- Only the Scheduler service account receives `roles/run.invoker`.
- The runtime receives a custom role containing only MIG inspect/resize
  permissions, plus Monitoring Viewer, Vertex AI User, and access to its state
  bucket.
- Capacity changes are limited to one unit per evaluation and 1-4 units by
  default.
- Terraform refuses live mode until you confirm the old controller and native
  Compute Engine autoscaler are disabled.

Do not run another external controller, a Compute Engine autoscaler, and this
service as concurrent writers. They can issue conflicting resize operations.

## Local development

Python 3.11 or newer is required.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item resource_manager\.env.example resource_manager\.env
pytest
adk web .
```

## Deploy with Terraform

Run deployment from Google Cloud Shell, which provides `gcloud`. Terraform uses
Cloud Build to build the content-addressed container image.

```bash
cd resource_manager_adk/v1/deployment/terraform
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars` and set at least:

```hcl
project_id = "your-real-project-id"
region     = "europe-central2"
mig_name   = "arm-web-mig"
```

The deployment expects the repository's sibling `app/` directory because it
builds the same benchmark workload image used by the baseline configuration.
The default `e2-highcpu-2` machine type provides one 2-vCPU workload unit, and
the MIG starts with one unit.

Initialize Terraform:

```bash
terraform init
```

If `arm-web-mig` already exists from the legacy deployment script, stop its
controller and, after initialization, import the MIG before planning this stack
rather than attempting to create a second resource with the same name:

```bash
terraform import google_compute_region_instance_group_manager.target \
  projects/PROJECT_ID/regions/REGION/instanceGroupManagers/MIG_NAME
```

Review the following plan carefully: adopting the MIG changes it to the
Terraform-managed instance template and can roll its workload VMs.

Format, validate, review, and deploy the safe configuration:

```bash
terraform fmt -check
terraform validate
terraform plan -out=tfplan
terraform apply tfplan
```

The MIG uses a proactive Terraform-managed update policy. When the instance
template changes, `terraform apply` rolls the workload VMs onto the new
template; no separate `gcloud` replacement command is required.

This first deployment is paused and dry-run. Test the authenticated endpoint or
temporarily enable the scheduler while leaving `enable_live_scaling = false`.
Inspect the Cloud Run logs and verify the observed MIG and CPU values.

Before live mode:

1. Stop the legacy `arm-controller` VM/container.
2. Verify native MIG autoscaling is off.
3. Confirm the configured MIG, region, min/max, thresholds, and cooldown.
4. Set:

```hcl
scheduler_paused           = false
enable_live_scaling        = true
exclusive_scaler_confirmed = true
```

Then create and apply a new reviewed plan:

```bash
terraform plan -out=tfplan-live
terraform apply tfplan-live
```

Useful outputs:

```bash
terraform output service_url
terraform output agent_version
terraform output workload_url
terraform output workload_machine_type
terraform output scheduler_state
terraform output scaling_mode
terraform output runtime_service_account
```

The identity running Terraform needs permission to enable services, create IAM
roles/service accounts, create Compute Engine instance templates, MIGs, firewall
and load-balancer resources, deploy Cloud Run and Scheduler resources, create
the state bucket, submit Cloud Builds, and act as the workload, runtime, and
Scheduler service accounts.
