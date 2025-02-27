package init_pkg

// GetDefaultFiles returns the default files to create with their content
func GetDefaultFiles() map[string]string {
	return map[string]string{
		"dev/provider.hcl": `# Provider configuration
provider "bigquery" {
  project_id = "${var.PROJECT_ID}"
  region     = "${var.REGION}"
}`,

		"dev/variables.hcl": `# Module variables
variable "PROJECT_ID" {
  description = "The GCP project ID"
  value       = "my-gcp-project-id"  # User sets this value
}

variable "REGION" {
  description = "The GCP region"
  value       = "us-central1"        # User sets this value
}`,

		"dev/main.hcl": `# Main configuration file that calls modules
module "example_module" {
  path = "./modules/example"
  config = {
    project_id = "${var.PROJECT_ID}"
    region     = "${var.REGION}"
  }
}

module "sales_data" {
  path = "./modules/sales"
  config = {
    project_id = "${var.PROJECT_ID}"
    region     = "${var.REGION}"
  }
}`,
	}
} 