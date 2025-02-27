package init_pkg

// GetDefaultDirs returns the default directory structure
func GetDefaultDirs() []string {
	return []string{
		"dev",
		"prod",
		"modules",
		"modules/example",
	}
} 