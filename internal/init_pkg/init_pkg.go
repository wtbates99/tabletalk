package init_pkg

import (
	"fmt"
	"os"
	"path/filepath"
)

// InitCommand creates the necessary directory structure and files
func InitCommand(rootDir string) error {
	if rootDir == "" {
		rootDir = "."
	}

	for _, dir := range GetDefaultDirs() {
		dirPath := filepath.Join(rootDir, dir)
		if err := os.MkdirAll(dirPath, 0755); err != nil {
			return fmt.Errorf("failed to create directory %s: %w", dirPath, err)
		}
		fmt.Printf("Created directory: %s\n", dirPath)
	}

	for file, content := range GetDefaultFiles() {
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
