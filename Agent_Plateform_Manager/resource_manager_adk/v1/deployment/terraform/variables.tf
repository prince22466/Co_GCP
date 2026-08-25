variable "project_id" {
  description = "GCP project in which the workload MIG and ADK service are created."
  type        = string

  validation {
    condition     = length(trimspace(var.project_id)) > 0
    error_message = "project_id must not be empty."
  }
}

variable "region" {
  description = "Region for Cloud Run, Scheduler, state storage, and the MIG."
  type        = string
  default     = "europe-central2"
}

variable "vertex_location" {
  description = "Vertex AI model endpoint. Use global unless regional processing is required."
  type        = string
  default     = "global"
}

variable "mig_name" {
  description = "Regional managed instance group created and controlled by the agent stack."
  type        = string
  default     = "arm-web-mig"
}

variable "mig_machine_type" {
  description = "Machine type for one workload resource unit. e2-highcpu-2 matches the baseline's 2-vCPU unit."
  type        = string
  default     = "e2-highcpu-2"
}

variable "network" {
  description = "VPC network used by the managed workload instances."
  type        = string
  default     = "default"
}

variable "workload_network_tag" {
  description = "Network tag used to admit load-balancer health checks to workload instances."
  type        = string
  default     = "arm-adk-web"
}

variable "workload_base_instance_name" {
  description = "Base name assigned to VMs created by the regional MIG."
  type        = string
  default     = "arm-adk-web"
}

variable "workload_image_name" {
  description = "Artifact Registry image name for the benchmark web workload."
  type        = string
  default     = "arm-web"
}

variable "workload_web_concurrency" {
  description = "Gunicorn worker count used by each workload VM."
  type        = number
  default     = 2
}

variable "workload_default_cpu_ms" {
  description = "Default CPU burn per workload request in milliseconds."
  type        = number
  default     = 8
}

variable "workload_unit_vcpu" {
  description = "Accounting value for application vCPUs provided by one MIG instance."
  type        = number
  default     = 2
}

variable "workload_unit_memory_gib" {
  description = "Accounting value for application memory provided by one MIG instance."
  type        = number
  default     = 2
}

variable "service_name" {
  description = "Cloud Run service name."
  type        = string
  default     = "arm-adk-resource-manager"
}

variable "artifact_repository" {
  description = "Artifact Registry repository owned by this Terraform stack."
  type        = string
  default     = "arm-adk"
}

variable "state_bucket_name" {
  description = "Optional globally unique bucket name for durable cooldown state."
  type        = string
  default     = null
  nullable    = true
}

variable "adk_model" {
  description = "Vertex AI Gemini model used by the ADK agent."
  type        = string
  default     = "gemini-3.6-flash"
}

variable "agent_version" {
  description = "Version identifier attached to the agent image and Cloud Run service."
  type        = string
  default     = "v1"

  validation {
    condition     = var.agent_version == "v1"
    error_message = "This release is immutable and must be deployed with agent_version=v1."
  }
}

variable "min_units" {
  description = "Minimum allowed MIG target size."
  type        = number
  default     = 1

  validation {
    condition     = var.min_units >= 1
    error_message = "min_units must be at least 1."
  }
}

variable "max_units" {
  description = "Maximum allowed MIG target size."
  type        = number
  default     = 4

  validation {
    condition     = var.max_units >= 1
    error_message = "max_units must be at least 1."
  }
}

variable "scale_up_cpu" {
  description = "Scale-up CPU ratio; 0.65 means 65 percent."
  type        = number
  default     = 0.65

  validation {
    condition     = var.scale_up_cpu > 0 && var.scale_up_cpu <= 1
    error_message = "scale_up_cpu must be greater than 0 and at most 1."
  }
}

variable "scale_down_cpu" {
  description = "Scale-down CPU ratio; 0.30 means 30 percent."
  type        = number
  default     = 0.30

  validation {
    condition     = var.scale_down_cpu >= 0 && var.scale_down_cpu < 1
    error_message = "scale_down_cpu must be at least 0 and less than 1."
  }
}

variable "cpu_lookback_seconds" {
  description = "Cloud Monitoring CPU lookback window."
  type        = number
  default     = 300
}

variable "cooldown_seconds" {
  description = "Minimum time between successful resize operations."
  type        = number
  default     = 120
}

variable "scheduler_cron" {
  description = "Cloud Scheduler cron expression for capacity evaluations."
  type        = string
  default     = "*/2 * * * *"
}

variable "scheduler_paused" {
  description = "Keep scheduled model calls paused until validation is complete."
  type        = bool
  default     = true
}

variable "enable_live_scaling" {
  description = "Allow the guarded tool to resize the MIG. False is dry-run mode."
  type        = bool
  default     = false
}

variable "exclusive_scaler_confirmed" {
  description = "Confirm that the legacy controller and Compute Engine autoscaler are disabled before live scaling."
  type        = bool
  default     = false
}
