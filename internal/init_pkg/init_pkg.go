package init_pkg

import (
	"fmt"
	"os"
	"path/filepath"
)

// Default directory structure
var defaultDirs = []string{
	"modules",
	"schemas",
	"configs",
}

// Default files to create
var defaultFiles = map[string]string{
	"config.yaml": `# BigQuery Schema Analysis Configuration
project_id: "your-gcp-project-id"
dataset_filters:
  - include: "*"  # Include all datasets by default
  - exclude: "temp_*"  # Exclude temporary datasets
`,
	"modules/example.yaml": `# Example module configuration
name: sales
description: "Sales-related tables for analysis"
tables:
  - project.dataset.customers
  - project.dataset.orders
  - project.dataset.refunds
`,
}

// InitCommand creates the necessary directory structure and files
func InitCommand(rootDir string) error {
	if rootDir == "" {
		rootDir = "."
	}

	// Create directories
	for _, dir := range defaultDirs {
		dirPath := filepath.Join(rootDir, dir)
		if err := os.MkdirAll(dirPath, 0755); err != nil {
			return fmt.Errorf("failed to create directory %s: %w", dirPath, err)
		}
		fmt.Printf("Created directory: %s\n", dirPath)
	}

	// Create default files
	for file, content := range defaultFiles {
		filePath := filepath.Join(rootDir, file)
		
		// Create parent directories if they don't exist
		if err := os.MkdirAll(filepath.Dir(filePath), 0755); err != nil {
			return fmt.Errorf("failed to create parent directory for %s: %w", filePath, err)
		}
		
		// Check if file already exists
		if _, err := os.Stat(filePath); err == nil {
			fmt.Printf("File already exists, skipping: %s\n", filePath)
			continue
		}
		
		// Create and write to file
		if err := os.WriteFile(filePath, []byte(content), 0644); err != nil {
			return fmt.Errorf("failed to create file %s: %w", filePath, err)
		}
		fmt.Printf("Created file: %s\n", filePath)
	}

	fmt.Println("\nInitialization complete! Edit config.yaml to set your GCP project ID.")
	return nil
}
