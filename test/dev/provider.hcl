# Provider configuration
provider "bigquery" {
  project_id = "${var.PROJECT_ID}"
  region     = "${var.REGION}"
}