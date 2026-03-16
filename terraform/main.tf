terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# Enable required APIs
resource "google_project_service" "apis" {
  for_each = toset([
    "run.googleapis.com",
    "firestore.googleapis.com",
    "aiplatform.googleapis.com",
    "cloudbuild.googleapis.com",
    "containerregistry.googleapis.com",
    "secretmanager.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}

# Firestore database
resource "google_firestore_database" "cortex41_db" {
  name        = "(default)"
  location_id = var.region
  type        = "FIRESTORE_NATIVE"
  depends_on  = [google_project_service.apis]
}

# Secret for Gemini API key
resource "google_secret_manager_secret" "gemini_api_key" {
  secret_id = "cortex41-gemini-api-key"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

# Service account for Cloud Run
resource "google_service_account" "cortex41_sa" {
  account_id   = "cortex41-runner"
  display_name = "cortex41 Cloud Run Service Account"
}

resource "google_project_iam_member" "firestore_access" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.cortex41_sa.email}"
}

resource "google_project_iam_member" "secret_access" {
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.cortex41_sa.email}"
}

resource "google_project_iam_member" "vertex_access" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.cortex41_sa.email}"
}

# Cloud Run service
resource "google_cloud_run_v2_service" "cortex41_backend" {
  name     = "cortex41-backend"
  location = var.region
  depends_on = [google_project_service.apis]

  template {
    service_account = google_service_account.cortex41_sa.email

    containers {
      image = "gcr.io/${var.project_id}/cortex41-backend:latest"

      resources {
        limits = {
          cpu    = "2"
          memory = "2Gi"
        }
      }

      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }

      env {
        name = "GEMINI_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.gemini_api_key.secret_id
            version = "latest"
          }
        }
      }
    }

    scaling {
      min_instance_count = 1
      max_instance_count = 10
    }
  }
}

# Allow public access
resource "google_cloud_run_service_iam_member" "public_access" {
  location = google_cloud_run_v2_service.cortex41_backend.location
  service  = google_cloud_run_v2_service.cortex41_backend.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
