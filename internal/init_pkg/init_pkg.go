package init_pkg

import (
	"fmt"
	"os"
	"path/filepath"
)

// Default directory structure
var defaultDirs = []string{
	"dev",
	"prod",
	"modules",
	"modules/example",
}

// Default files to create
var defaultFiles = map[string]string{
	"dev/provider.hcl": `# Provider configuration
provider "bigquery" {
  project_id = "${var.PROJECT_ID}"
  region     = "${var.REGION}"
}`,
	"dev/variables.hcl": `# Module variables
variable "PROJECT_ID" {
  description = "The GCP project ID"
  required    = true
}
variable "REGION" {
  description = "The GCP region"
  default     = "us-central1"
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
}
`,
}

// InitCommand creates the necessary directory structure and files
func InitCommand(rootDir string) error {
	if rootDir == "" {
		rootDir = "."
	}

	for _, dir := range defaultDirs {
		dirPath := filepath.Join(rootDir, dir)
		if err := os.MkdirAll(dirPath, 0755); err != nil {
			return fmt.Errorf("failed to create directory %s: %w", dirPath, err)
		}
		fmt.Printf("Created directory: %s\n", dirPath)
	}

	for file, content := range defaultFiles {
		filePath := filepath.Join(rootDir, file)
		
		if err := os.MkdirAll(filepath.Dir(filePath), 0755); err != nil {
			return fmt.Errorf("failed to create parent directory for %s: %w", filePath, err)
		}
		
		if _, err := os.Stat(filePath); err == nil {
			fmt.Printf("File already exists, skipping: %s\n", filePath)
			continue
		}
		
		if err := os.WriteFile(filePath, []byte(content), 0644); err != nil {
			return fmt.Errorf("failed to create file %s: %w", filePath, err)
		}
		fmt.Printf("Created file: %s\n", filePath)
	}

	fmt.Println("\nInitialization complete! Edit the configuration files to set your GCP project ID.")
	return nil
}
