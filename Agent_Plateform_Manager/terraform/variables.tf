variable "project_id" {
  description = "GCP project ID."
  type        = string
}

variable "region" {
  description = "GCP region for the Autopilot cluster and external IP."
  type        = string
  default     = "europe-central2"
}

variable "cluster_name" {
  description = "GKE Autopilot cluster name."
  type        = string
  default     = "arm-stage1-autopilot"
}

variable "namespace" {
  description = "Kubernetes namespace used by the benchmark."
  type        = string
  default     = "arm-stage1"
}

variable "artifact_repository" {
  description = "Artifact Registry Docker repository."
  type        = string
  default     = "arm-stage1"
}

variable "app_name" {
  description = "Application / Deployment / HPA name."
  type        = string
  default     = "arm-web"
}

variable "pod_cpu" {
  description = "CPU requested and limited by each web Pod."
  type        = string
  default     = "2"
}

variable "pod_memory" {
  description = "Memory requested and limited by each web Pod."
  type        = string
  default     = "2Gi"
}

variable "pod_memory_gib" {
  description = "Numeric GiB equivalent used by experiment accounting."
  type        = number
  default     = 2
}

variable "min_replicas" {
  description = "Minimum HPA replica count."
  type        = number
  default     = 1
}

variable "max_replicas" {
  description = "Maximum HPA replica count. 4 x 2 vCPU = 8 vCPU cap."
  type        = number
  default     = 4
}

variable "hpa_target_cpu" {
  description = "HPA target average CPU utilization percentage."
  type        = number
  default     = 60
}

variable "work_cpu_ms" {
  description = "Default CPU burn per HTTP /work request."
  type        = number
  default     = 8
}
