locals {
  required_apis = toset([
    "aiplatform.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudscheduler.googleapis.com",
    "compute.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "monitoring.googleapis.com",
    "run.googleapis.com",
    "storage.googleapis.com",
  ])

  source_root = "${path.module}/../.."
  source_files = sort(concat(
    tolist(fileset(local.source_root, "resource_manager/*.py")),
    [".dockerignore", ".gcloudignore", "Dockerfile", "pyproject.toml"],
  ))
  source_hash = sha256(join("", [
    for file in local.source_files : filesha256("${local.source_root}/${file}")
  ]))

  workload_source_root = "${path.module}/../../../../app"
  workload_source_files = [
    "Dockerfile",
    "app.py",
    "requirements.txt",
  ]
  workload_source_hash = sha256(join("", [
    for file in local.workload_source_files : filesha256("${local.workload_source_root}/${file}")
  ]))

  image_tag         = "${var.agent_version}-${substr(local.source_hash, 0, 12)}"
  agent_image       = "${var.region}-docker.pkg.dev/${var.project_id}/${var.artifact_repository}/${var.service_name}:${local.image_tag}"
  workload_image    = "${var.region}-docker.pkg.dev/${var.project_id}/${var.artifact_repository}/${var.workload_image_name}:${substr(local.workload_source_hash, 0, 12)}"
  state_bucket_name = coalesce(var.state_bucket_name, "${var.project_id}-arm-adk-state")
}

resource "google_project_service" "required" {
  for_each = local.required_apis

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "agent" {
  project       = var.project_id
  location      = var.region
  repository_id = var.artifact_repository
  description   = "Container images for the ADK resource manager and managed workload"
  format        = "DOCKER"

  depends_on = [
    google_project_service.required["artifactregistry.googleapis.com"],
  ]
}

resource "null_resource" "agent_image" {
  triggers = {
    source_hash = local.source_hash
    image       = local.agent_image
  }

  provisioner "local-exec" {
    working_dir = path.module
    command     = "gcloud builds submit ../.. --project='${var.project_id}' --tag='${local.agent_image}' --quiet"
  }

  depends_on = [
    google_artifact_registry_repository.agent,
    google_project_service.required["cloudbuild.googleapis.com"],
  ]
}

resource "null_resource" "workload_image" {
  triggers = {
    source_hash = local.workload_source_hash
    image       = local.workload_image
  }

  provisioner "local-exec" {
    working_dir = path.module
    command     = "gcloud builds submit ../../../../app --project='${var.project_id}' --tag='${local.workload_image}' --quiet"
  }

  depends_on = [
    google_artifact_registry_repository.agent,
    google_project_service.required["cloudbuild.googleapis.com"],
  ]
}

resource "google_service_account" "workload" {
  project      = var.project_id
  account_id   = "arm-adk-workload"
  display_name = "ARM ADK managed workload VMs"

  depends_on = [
    google_project_service.required["iam.googleapis.com"],
  ]
}

resource "google_project_iam_member" "workload_artifact_reader" {
  project = var.project_id
  role    = "roles/artifactregistry.reader"
  member  = "serviceAccount:${google_service_account.workload.email}"
}

resource "google_compute_instance_template" "workload" {
  project      = var.project_id
  name_prefix  = "${var.mig_name}-"
  machine_type = var.mig_machine_type
  tags         = [var.workload_network_tag]

  disk {
    boot         = true
    auto_delete  = true
    source_image = "projects/cos-cloud/global/images/family/cos-stable"
  }

  network_interface {
    network = var.network

    # The VM needs outbound access to pull the private workload image.
    access_config {}
  }

  service_account {
    email  = google_service_account.workload.email
    scopes = ["cloud-platform"]
  }

  metadata_startup_script = <<-EOT
    #!/bin/bash
    set -euo pipefail

    REGISTRY_HOST="${var.region}-docker.pkg.dev"
    for ATTEMPT in $(seq 1 12); do
      TOKEN="$(curl -fsS -H 'Metadata-Flavor: Google' \
        'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token' \
        | awk -F'"' '/access_token/{print $4}')"
      if echo "$${TOKEN}" | docker login -u oauth2accesstoken --password-stdin "$${REGISTRY_HOST}" \
        && docker pull "${local.workload_image}"; then
        break
      fi
      if [ "$${ATTEMPT}" -eq 12 ]; then
        exit 1
      fi
      sleep 10
    done

    docker rm -f arm-web || true
    docker run -d --name arm-web --restart=always \
      -p 8080:8080 \
      -e WEB_CONCURRENCY=${var.workload_web_concurrency} \
      -e DEFAULT_CPU_MS=${var.workload_default_cpu_ms} \
      "${local.workload_image}"
  EOT

  lifecycle {
    create_before_destroy = true
  }

  depends_on = [
    null_resource.workload_image,
    google_project_iam_member.workload_artifact_reader,
    google_project_service.required["compute.googleapis.com"],
  ]
}

resource "google_compute_region_instance_group_manager" "target" {
  project            = var.project_id
  name               = var.mig_name
  region             = var.region
  base_instance_name = var.workload_base_instance_name
  target_size        = var.min_units

  version {
    instance_template = google_compute_instance_template.workload.self_link
  }

  named_port {
    name = "http"
    port = 8080
  }

  # After initial creation, the ADK agent owns target size. Terraform must not
  # undo a live scaling decision on the next apply.
  lifecycle {
    ignore_changes = [target_size]
  }
}

resource "google_compute_firewall" "allow_health_checks" {
  project = var.project_id
  name    = "${var.mig_name}-allow-health-checks"
  network = var.network

  direction     = "INGRESS"
  source_ranges = ["130.211.0.0/22", "35.191.0.0/16"]
  target_tags   = [var.workload_network_tag]

  allow {
    protocol = "tcp"
    ports    = ["8080"]
  }
}

resource "google_compute_health_check" "workload" {
  project             = var.project_id
  name                = "${var.mig_name}-health"
  check_interval_sec  = 5
  timeout_sec         = 3
  healthy_threshold   = 2
  unhealthy_threshold = 3

  http_health_check {
    port         = 8080
    request_path = "/healthz"
  }
}

resource "google_compute_backend_service" "workload" {
  project               = var.project_id
  name                  = "${var.mig_name}-backend"
  protocol              = "HTTP"
  port_name             = "http"
  timeout_sec           = 10
  load_balancing_scheme = "EXTERNAL_MANAGED"
  health_checks         = [google_compute_health_check.workload.self_link]

  backend {
    group           = google_compute_region_instance_group_manager.target.instance_group
    balancing_mode  = "UTILIZATION"
    max_utilization = 0.8
  }
}

resource "google_compute_url_map" "workload" {
  project         = var.project_id
  name            = "${var.mig_name}-map"
  default_service = google_compute_backend_service.workload.self_link
}

resource "google_compute_target_http_proxy" "workload" {
  project = var.project_id
  name    = "${var.mig_name}-proxy"
  url_map = google_compute_url_map.workload.self_link
}

resource "google_compute_global_address" "workload" {
  project = var.project_id
  name    = "${var.mig_name}-ip"
}

resource "google_compute_global_forwarding_rule" "workload" {
  project               = var.project_id
  name                  = "${var.mig_name}-http"
  ip_address            = google_compute_global_address.workload.address
  port_range            = "80"
  target                = google_compute_target_http_proxy.workload.self_link
  load_balancing_scheme = "EXTERNAL_MANAGED"
}

resource "google_storage_bucket" "state" {
  project                     = var.project_id
  name                        = local.state_bucket_name
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = true

  versioning {
    enabled = true
  }

  depends_on = [
    google_project_service.required["storage.googleapis.com"],
  ]
}

resource "google_service_account" "runtime" {
  project      = var.project_id
  account_id   = "arm-adk-runtime"
  display_name = "ADK resource manager runtime"

  depends_on = [
    google_project_service.required["iam.googleapis.com"],
  ]
}

resource "google_service_account" "scheduler" {
  project      = var.project_id
  account_id   = "arm-adk-scheduler"
  display_name = "ADK resource manager scheduler invoker"

  depends_on = [
    google_project_service.required["iam.googleapis.com"],
  ]
}

resource "google_project_iam_custom_role" "mig_scaler" {
  project     = var.project_id
  role_id     = "armAdkMigScaler"
  title       = "ARM ADK MIG Scaler"
  description = "Inspect or resize regional managed instance groups"
  permissions = [
    "compute.instanceGroupManagers.get",
    "compute.instanceGroupManagers.update",
  ]

  depends_on = [
    google_project_service.required["iam.googleapis.com"],
  ]
}

resource "google_project_iam_member" "runtime_mig_scaler" {
  project = var.project_id
  role    = google_project_iam_custom_role.mig_scaler.name
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_project_iam_member" "runtime_monitoring" {
  project = var.project_id
  role    = "roles/monitoring.viewer"
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_project_iam_member" "runtime_vertex" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_storage_bucket_iam_member" "runtime_state" {
  bucket = google_storage_bucket.state.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_cloud_run_v2_service" "agent" {
  project             = var.project_id
  name                = var.service_name
  location            = var.region
  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_ALL"
  labels = {
    agent_version = var.agent_version
    component     = "resource-manager-agent"
  }

  lifecycle {
    precondition {
      condition     = var.max_units >= var.min_units
      error_message = "max_units must be greater than or equal to min_units."
    }
    precondition {
      condition     = var.scale_down_cpu < var.scale_up_cpu
      error_message = "scale_down_cpu must be lower than scale_up_cpu."
    }
    precondition {
      condition     = !var.enable_live_scaling || var.exclusive_scaler_confirmed
      error_message = "Set exclusive_scaler_confirmed=true only after disabling the legacy controller and native MIG autoscaling."
    }
  }

  template {
    service_account                  = google_service_account.runtime.email
    timeout                          = "300s"
    max_instance_request_concurrency = 1

    scaling {
      min_instance_count = 0
      max_instance_count = 1
    }

    containers {
      image = local.agent_image

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }

      env {
        name  = "GOOGLE_GENAI_USE_VERTEXAI"
        value = "TRUE"
      }
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "GOOGLE_CLOUD_LOCATION"
        value = var.vertex_location
      }
      env {
        name  = "ADK_MODEL"
        value = var.adk_model
      }
      env {
        name  = "AGENT_VERSION"
        value = var.agent_version
      }
      env {
        name  = "PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "REGION"
        value = var.region
      }
      env {
        name  = "MIG_NAME"
        value = google_compute_region_instance_group_manager.target.name
      }
      env {
        name  = "STATE_BUCKET"
        value = google_storage_bucket.state.name
      }
      env {
        name  = "MIG_MIN"
        value = tostring(var.min_units)
      }
      env {
        name  = "MIG_MAX"
        value = tostring(var.max_units)
      }
      env {
        name  = "SCALE_UP_CPU"
        value = tostring(var.scale_up_cpu)
      }
      env {
        name  = "SCALE_DOWN_CPU"
        value = tostring(var.scale_down_cpu)
      }
      env {
        name  = "CPU_LOOKBACK_SECONDS"
        value = tostring(var.cpu_lookback_seconds)
      }
      env {
        name  = "COOLDOWN_SECONDS"
        value = tostring(var.cooldown_seconds)
      }
      env {
        name  = "SCALING_ENABLED"
        value = tostring(var.enable_live_scaling)
      }

      startup_probe {
        failure_threshold     = 12
        initial_delay_seconds = 0
        period_seconds        = 5
        timeout_seconds       = 2

        tcp_socket {
          port = 8080
        }
      }

      liveness_probe {
        failure_threshold = 3
        period_seconds    = 30
        timeout_seconds   = 2

        http_get {
          path = "/healthz"
          port = 8080
        }
      }
    }
  }

  depends_on = [
    null_resource.agent_image,
    google_project_iam_member.runtime_mig_scaler,
    google_project_iam_member.runtime_monitoring,
    google_project_iam_member.runtime_vertex,
    google_storage_bucket_iam_member.runtime_state,
    google_project_service.required["run.googleapis.com"],
  ]
}

resource "google_cloud_run_v2_service_iam_member" "scheduler_invoker" {
  project  = var.project_id
  location = google_cloud_run_v2_service.agent.location
  name     = google_cloud_run_v2_service.agent.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}

resource "google_cloud_scheduler_job" "evaluate" {
  project          = var.project_id
  region           = var.region
  name             = "${var.service_name}-evaluate"
  description      = "Run one guarded ADK capacity evaluation"
  schedule         = var.scheduler_cron
  time_zone        = "Etc/UTC"
  attempt_deadline = "320s"
  paused           = var.scheduler_paused

  retry_config {
    retry_count = 0
  }

  http_target {
    http_method = "POST"
    uri         = "${google_cloud_run_v2_service.agent.uri}/evaluate"
    headers = {
      "Content-Type" = "application/json"
    }
    body = base64encode("{}")

    oidc_token {
      service_account_email = google_service_account.scheduler.email
      audience              = google_cloud_run_v2_service.agent.uri
    }
  }

  depends_on = [
    google_cloud_run_v2_service_iam_member.scheduler_invoker,
    google_project_service.required["cloudscheduler.googleapis.com"],
    google_project_service.required["iamcredentials.googleapis.com"],
  ]
}
