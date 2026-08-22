output "cluster_name" {
  value = google_container_cluster.autopilot.name
}

output "region" {
  value = var.region
}

output "namespace" {
  value = var.namespace
}

output "deployment_name" {
  value = var.app_name
}

output "hpa_name" {
  value = var.app_name
}

output "application_ip" {
  value = google_compute_address.web.address
}

output "application_url" {
  value = "http://${google_compute_address.web.address}"
}

output "app_image" {
  value = local.app_image
}

output "pod_cpu" {
  value = tonumber(var.pod_cpu)
}

output "pod_memory_gib" {
  value = var.pod_memory_gib
}

output "min_replicas" {
  value = var.min_replicas
}

output "max_replicas" {
  value = var.max_replicas
}

output "hpa_target_cpu" {
  value = var.hpa_target_cpu
}

output "work_cpu_ms" {
  value = var.work_cpu_ms
}
