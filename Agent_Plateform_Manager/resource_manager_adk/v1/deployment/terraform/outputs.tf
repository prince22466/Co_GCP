output "service_url" {
  description = "Authenticated Cloud Run service URL."
  value       = google_cloud_run_v2_service.agent.uri
}

output "agent_image" {
  description = "Content-addressed ADK container image."
  value       = local.agent_image
}

output "agent_version" {
  value = var.agent_version
}

output "runtime_service_account" {
  value = google_service_account.runtime.email
}

output "scheduler_job" {
  value = google_cloud_scheduler_job.evaluate.id
}

output "scheduler_state" {
  value = var.scheduler_paused ? "PAUSED" : "ENABLED"
}

output "scaling_mode" {
  value = var.enable_live_scaling ? "LIVE" : "DRY_RUN"
}

output "state_bucket" {
  value = google_storage_bucket.state.name
}

output "target_mig" {
  value = google_compute_region_instance_group_manager.target.self_link
}

output "native_autoscaler_name" {
  value = google_compute_region_autoscaler.native_disabled.name
}

output "native_autoscaler_mode" {
  value = google_compute_region_autoscaler.native_disabled.autoscaling_policy[0].mode
}

output "workload_image" {
  description = "Content-addressed benchmark workload image."
  value       = local.workload_image
}

output "workload_machine_type" {
  value = var.mig_machine_type
}

output "workload_ip" {
  value = google_compute_global_address.workload.address
}

output "workload_url" {
  value = "http://${google_compute_global_address.workload.address}"
}

output "project_id" {
  value = var.project_id
}

output "region" {
  value = var.region
}

output "mig_name" {
  value = google_compute_region_instance_group_manager.target.name
}

output "min_units" {
  value = var.min_units
}

output "max_units" {
  value = var.max_units
}

output "cooldown_seconds" {
  value = var.cooldown_seconds
}

output "workload_default_cpu_ms" {
  value = var.workload_default_cpu_ms
}

output "workload_unit_vcpu" {
  value = var.workload_unit_vcpu
}

output "workload_unit_memory_gib" {
  value = var.workload_unit_memory_gib
}
