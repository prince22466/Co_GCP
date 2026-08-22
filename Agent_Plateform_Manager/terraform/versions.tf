terraform {
  required_version = ">= 1.7.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.43"
    }

    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 3.2"
    }

    null = {
      source  = "hashicorp/null"
      version = "~> 3.2"
    }
  }
}
