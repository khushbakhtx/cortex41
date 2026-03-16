output "backend_url" {
  description = "Cloud Run backend URL"
  value       = google_cloud_run_v2_service.cortex41_backend.uri
}

output "service_account_email" {
  description = "Service account email for cortex41"
  value       = google_service_account.cortex41_sa.email
}
