package schema_functions

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"strings"

	"cloud.google.com/go/bigquery"
)

func ExtractBigQuerySchema(ctx context.Context, tablePath string) (*bigquery.Schema, error) {
	parts := strings.Split(tablePath, ".")
	if len(parts) != 3 {
		return nil, fmt.Errorf("invalid table path format, expected 'project.dataset.table'")
	}
	projectID, datasetID, tableID := parts[0], parts[1], parts[2]

	client, err := bigquery.NewClient(ctx, projectID)
	if err != nil {
		return nil, fmt.Errorf("failed to create BigQuery client: %v", err)
	}
	defer client.Close()

	table := client.Dataset(datasetID).Table(tableID)
	metadata, err := table.Metadata(ctx)
	if err != nil {
		return nil, fmt.Errorf("failed to get table metadata: %v", err)
	}

	return &metadata.Schema, nil
}


func GetBigQueryTableData(ctx context.Context, tablePath string, limit int) ([]map[string]bigquery.Value, error) {
	parts := strings.Split(tablePath, ".")
	if len(parts) != 3 {
		return nil, fmt.Errorf("invalid table path format, expected 'project.dataset.table'")
	}
	projectID, datasetID, tableID := parts[0], parts[1], parts[2]

	client, err := bigquery.NewClient(ctx, projectID)
	if err != nil {
		return nil, fmt.Errorf("failed to create BigQuery client: %v", err)
	}
	defer client.Close()

	query := client.Query(fmt.Sprintf("SELECT * FROM `%s.%s.%s` LIMIT %d", projectID, datasetID, tableID, limit))
	
	it, err := query.Read(ctx)
	if err != nil {
		return nil, fmt.Errorf("failed to execute query: %v", err)
	}

	var results []map[string]bigquery.Value
	for {
		var row map[string]bigquery.Value
		err := it.Next(&row)
		if err != nil {
			if err.Error() == "no more items in iterator" {
				break
			}
			return nil, fmt.Errorf("error reading row: %v", err)
		}
		results = append(results, row)
	}

	return results, nil
}

type SchemaField struct {
	Name        string        `json:"name"`
	Type        string        `json:"type"`
	Description string        `json:"description,omitempty"`
	Required    bool          `json:"required,omitempty"`
	Repeated    bool          `json:"repeated,omitempty"`
	Schema      []SchemaField `json:"schema,omitempty"`
}


func SaveSchemaToFile(schema bigquery.Schema, filename string) {
	var serializableSchema []SchemaField
	
	for _, field := range schema {
		schemaField := SchemaField{
			Name:        field.Name,
			Type:        string(field.Type),
			Description: field.Description,
			Required:    field.Required,
			Repeated:    field.Repeated,
		}
		
		if field.Type == bigquery.RecordFieldType && field.Schema != nil {
			schemaField.Schema = convertNestedSchema(field.Schema)
		}
		
		serializableSchema = append(serializableSchema, schemaField)
	}
	
	jsonData, err := json.MarshalIndent(serializableSchema, "", "  ")
	if err != nil {
		log.Fatalf("Failed to marshal schema to JSON: %v", err)
	}
	
	err = os.WriteFile(filename, jsonData, 0644)
	if err != nil {
		log.Fatalf("Failed to write schema to file: %v", err)
	}
	
	fmt.Printf("Schema saved to %s\n", filename)
}

func convertNestedSchema(schema bigquery.Schema) []SchemaField {
	var result []SchemaField
	
	for _, field := range schema {
		schemaField := SchemaField{
			Name:        field.Name,
			Type:        string(field.Type),
			Description: field.Description,
			Required:    field.Required,
			Repeated:    field.Repeated,
		}
		
		// Recursively handle nested fields
		if field.Type == bigquery.RecordFieldType && field.Schema != nil {
			schemaField.Schema = convertNestedSchema(field.Schema)
		}
		
		result = append(result, schemaField)
	}
	
	return result
}