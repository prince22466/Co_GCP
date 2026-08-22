provider "google" {
  project = var.project_id
  region  = var.region
}

data "google_client_config" "current" {}

locals {
  required_apis = toset([
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "compute.googleapis.com",
    "container.googleapis.com",
    "monitoring.googleapis.com",
  ])

  # A source change creates a new immutable-ish image tag and triggers a Deployment rollout.
  app_source_hash = sha256(join("", [
    filesha256("${path.module}/../app/Dockerfile"),
    filesha256("${path.module}/../app/app.py"),
    filesha256("${path.module}/../app/requirements.txt"),
  ]))

  app_tag   = "stage1-${substr(local.app_source_hash, 0, 12)}"
  app_image = "${var.region}-docker.pkg.dev/${var.project_id}/${var.artifact_repository}/${var.app_name}:${local.app_tag}"
}

resource "google_project_service" "required" {
  for_each = local.required_apis

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "images" {
  project       = var.project_id
  location      = var.region
  repository_id = var.artifact_repository
  description   = "Images for the agentic resource manager benchmark"
  format        = "DOCKER"

  depends_on = [
    google_project_service.required["artifactregistry.googleapis.com"]
  ]
}

# Autopilot nodes use a dedicated service account instead of the default Compute Engine SA.
resource "google_service_account" "gke_nodes" {
  project      = var.project_id
  account_id   = "arm-stage1-gke-nodes"
  display_name = "ARM Stage 1 GKE Autopilot nodes"
}

resource "google_project_iam_member" "gke_node_default_role" {
  project = var.project_id
  role    = "roles/container.defaultNodeServiceAccount"
  member  = "serviceAccount:${google_service_account.gke_nodes.email}"
}

resource "google_project_iam_member" "gke_node_artifact_reader" {
  project = var.project_id
  role    = "roles/artifactregistry.reader"
  member  = "serviceAccount:${google_service_account.gke_nodes.email}"
}

# This keeps the project script-light: Terraform invokes one Cloud Build command when
# the app source changes. Run Terraform from Cloud Shell (gcloud already installed/authenticated).
resource "null_resource" "app_image" {
  triggers = {
    source_hash = local.app_source_hash
    image       = local.app_image
  }

  provisioner "local-exec" {
    working_dir = path.module
    command = <<-EOT
      gcloud builds submit ../app \
        --project='${var.project_id}' \
        --tag='${local.app_image}' \
        --quiet
    EOT
  }

  depends_on = [
    google_project_service.required["cloudbuild.googleapis.com"],
    google_artifact_registry_repository.images,
  ]
}

resource "google_container_cluster" "autopilot" {
  project          = var.project_id
  name             = var.cluster_name
  location         = var.region
  enable_autopilot = true

  deletion_protection = false

  release_channel {
    channel = "REGULAR"
  }

  cluster_autoscaling {
    auto_provisioning_defaults {
      service_account = google_service_account.gke_nodes.email
    }
  }

  depends_on = [
    google_project_service.required["container.googleapis.com"],
    google_project_iam_member.gke_node_default_role,
    google_project_iam_member.gke_node_artifact_reader,
  ]
}

provider "kubernetes" {
  host                   = "https://${google_container_cluster.autopilot.endpoint}"
  token                  = data.google_client_config.current.access_token
  cluster_ca_certificate = base64decode(google_container_cluster.autopilot.master_auth[0].cluster_ca_certificate)

  # GKE Autopilot injects/changes platform annotations. Ignore those to avoid noisy drift.
  ignore_annotations = [
    "^autopilot\\.gke\\.io\\/.*",
    "^cloud\\.google\\.com\\/.*",
  ]
}

resource "google_compute_address" "web" {
  project      = var.project_id
  name         = "arm-stage1-web-ip"
  region       = var.region
  address_type = "EXTERNAL"
  network_tier = "PREMIUM"

  depends_on = [
    google_project_service.required["compute.googleapis.com"]
  ]
}

resource "kubernetes_namespace_v1" "benchmark" {
  metadata {
    name = var.namespace
  }

  depends_on = [google_container_cluster.autopilot]
}

# Hard application envelope: max 8 requested/limited CPU and 8 GiB memory.
resource "kubernetes_resource_quota_v1" "benchmark" {
  metadata {
    name      = "arm-stage1-cap"
    namespace = kubernetes_namespace_v1.benchmark.metadata[0].name
  }

  spec {
    hard = {
      "requests.cpu"    = "8"
      "limits.cpu"      = "8"
      "requests.memory" = "8Gi"
      "limits.memory"   = "8Gi"
    }
  }
}

resource "kubernetes_deployment_v1" "web" {
  metadata {
    name      = var.app_name
    namespace = kubernetes_namespace_v1.benchmark.metadata[0].name

    labels = {
      app = var.app_name
    }
  }

  spec {
    replicas = var.min_replicas

    selector {
      match_labels = {
        app = var.app_name
      }
    }

    template {
      metadata {
        labels = {
          app = var.app_name
        }
      }

      spec {
        container {
          name              = "web"
          image             = local.app_image
          image_pull_policy = "IfNotPresent"

          env {
            name  = "WEB_CONCURRENCY"
            value = "2"
          }

          env {
            name  = "DEFAULT_CPU_MS"
            value = tostring(var.work_cpu_ms)
          }

          port {
            name           = "http"
            container_port = 8080
          }

          resources {
            requests = {
              cpu    = var.pod_cpu
              memory = var.pod_memory
            }

            limits = {
              cpu    = var.pod_cpu
              memory = var.pod_memory
            }
          }

          readiness_probe {
            http_get {
              path = "/healthz"
              port = 8080
            }

            initial_delay_seconds = 2
            period_seconds        = 5
            timeout_seconds       = 2
          }

          liveness_probe {
            http_get {
              path = "/healthz"
              port = 8080
            }

            initial_delay_seconds = 10
            period_seconds        = 10
            timeout_seconds       = 2
          }

          security_context {
            allow_privilege_escalation = false
            privileged                 = false
          }
        }
      }
    }
  }

  depends_on = [
    null_resource.app_image,
    kubernetes_resource_quota_v1.benchmark,
  ]
}

resource "kubernetes_service_v1" "web" {
  metadata {
    name      = var.app_name
    namespace = kubernetes_namespace_v1.benchmark.metadata[0].name
  }

  spec {
    selector = {
      app = var.app_name
    }

    type             = "LoadBalancer"
    load_balancer_ip = google_compute_address.web.address

    port {
      name        = "http"
      port        = 80
      target_port = 8080
      protocol    = "TCP"
    }
  }

  wait_for_load_balancer = true
}

# Deliberately use the ordinary CPU-based HPA as the GKE baseline.
# We leave its default stabilization/scaling behavior intact and measure it.
resource "kubernetes_horizontal_pod_autoscaler_v1" "web" {
  metadata {
    name      = var.app_name
    namespace = kubernetes_namespace_v1.benchmark.metadata[0].name
  }

  spec {
    min_replicas                      = var.min_replicas
    max_replicas                      = var.max_replicas
    target_cpu_utilization_percentage = var.hpa_target_cpu

    scale_target_ref {
      api_version = "apps/v1"
      kind        = "Deployment"
      name        = kubernetes_deployment_v1.web.metadata[0].name
    }
  }
}
