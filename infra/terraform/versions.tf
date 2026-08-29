terraform {
  required_version = ">= 1.7"
  backend "gcs" {}
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
    # google_project_service_identity, used to ASK IAP for its service agent rather than
    # guessing the address, is exposed only on the beta surface.
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 6.0"
    }
    # random_id keys the managed certificate's name to its domain set so a domain change
    # can create-before-destroy (load_balancer.tf).
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

provider "google-beta" {
  project = var.project_id
  region  = var.region
}
