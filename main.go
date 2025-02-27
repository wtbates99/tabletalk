package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"biql/internal/schema_functions"
	"biql/internal/init_pkg"
)

func main() {
	if len(os.Args) < 2 {
		printUsage()
		os.Exit(1)
	}

	command := os.Args[1]
	ctx := context.Background()

	switch command {
	case "init":
		// Parse flags for init command
		initCmd := flag.NewFlagSet("init", flag.ExitOnError)
		directory := initCmd.String("dir", ".", "Directory to initialize")
		initCmd.Parse(os.Args[2:])
		
		init_pkg.InitCommand(*directory)
		fmt.Printf("Initialized in directory: %s\n", *directory)

	case "extract":
		// Parse flags for extract command
		extractCmd := flag.NewFlagSet("extract", flag.ExitOnError)
		table := extractCmd.String("table", "", "BigQuery table to extract schema from (required)")
		outputFile := extractCmd.String("output", "", "Output file for schema (default: <table_name>_schema.json)")
		sampleCount := extractCmd.Int("samples", 0, "Number of sample rows to fetch (0 for none)")
		extractCmd.Parse(os.Args[2:])

		if *table == "" {
			fmt.Println("Error: table is required")
			extractCmd.PrintDefaults()
			os.Exit(1)
		}

		// Extract schema
		schema, err := schema_functions.ExtractBigQuerySchema(ctx, *table)
		if err != nil {
			fmt.Printf("Error extracting schema: %v\n", err)
			os.Exit(1)
		}

		// Determine output filename
		outFile := *outputFile
		if outFile == "" {
			outFile = *table + "_schema.json"
		}
		
		// Save schema
		schema_functions.SaveSchemaToFile(*schema, outFile)
		fmt.Printf("Schema saved to %s\n", outFile)

		// Optionally fetch sample data
		if *sampleCount > 0 {
			sampleData, err := schema_functions.GetBigQueryTableData(ctx, *table, *sampleCount)
			if err != nil {
				fmt.Printf("Error fetching sample data: %v\n", err)
				os.Exit(1)
			}
			fmt.Printf("Fetched %d sample rows\n", len(sampleData))
		}

	default:
		fmt.Printf("Unknown command: %s\n", command)
		printUsage()
		os.Exit(1)
	}
}

func printUsage() {
	fmt.Println("Usage: biql <command> [arguments]")
	fmt.Println("Commands:")
	fmt.Println("  init <directory>    Initialize in the specified directory")
	fmt.Println("  extract <table>     Extract schema from BigQuery table")
}