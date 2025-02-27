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
	"modules/example/backend",
	"modules/example/provider",
}

// Default files to create
var defaultFiles = map[string]string{
	"modules/example/main.yaml": `# Main configuration file that calls modules
name: sales_analysis
description: "Sales data analysis pipeline"

# Import modules to use
modules:
  - name: sales_data
    path: ./modules/sales
    config:
      project_id: ${PROJECT_ID}
      region: ${REGION}

# Output configuration
outputs:
  - name: sales_summary
    source: sales_data.summary
`,
	"modules/example/backend/config.yaml": `# Backend configuration
# Specifies where the analysis context/state will be stored

type: gcs
config:
  bucket: "your-data-bucket"
  prefix: "biql/state"
  # Alternatively use local storage:
  # type: local
  # config:
  #   path: "./state"
`,
	"modules/example/provider/bigquery.yaml": `# Provider configuration
# Specifies the database connection details

type: bigquery
config:
  project_id: ${PROJECT_ID}
  region: ${REGION}
  # Optional authentication settings
  # credentials_file: "/path/to/credentials.json"
`,
	"modules/example/variables.yaml": `# Module variables
variables:
  - name: PROJECT_ID
    description: "The GCP project ID"
    required: true
    
  - name: REGION
    description: "The GCP region"
    default: "us-central1"
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

	fmt.Println("\nInitialization complete! Edit biql.yaml to set your GCP project ID.")
	return nil
}
