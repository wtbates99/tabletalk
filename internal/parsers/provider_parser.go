package parsers

import (
    "fmt"
    "path/filepath"

    "github.com/hashicorp/hcl/v2"
    "github.com/hashicorp/hcl/v2/gohcl"
    "github.com/hashicorp/hcl/v2/hclparse"
    "github.com/zclconf/go-cty/cty"
)

// Define structs that match your HCL structure
type Config struct {
    Provider *ProviderConfig `hcl:"provider,block"`
}

type ProviderConfig struct {
    Provider  string `hcl:"provider,label"`
    ProjectID string `hcl:"project_id"`
    Region    string `hcl:"region"`
}

// Variable represents a variable definition in HCL
type Variable struct {
    Name        string `hcl:"name,label"`
    Description string `hcl:"description,optional"`
    Value       string `hcl:"value,optional"`
    Default     string `hcl:"default,optional"`
    Required    bool   `hcl:"required,optional"`
}

// Variables holds multiple variable definitions
type Variables struct {
    Variables []*Variable `hcl:"variable,block"`
}

// ParseProvider parses the provider.hcl file from the given path or current directory
func ParseProvider(configPath string) (*Config, error) {
    // If no path is provided, use "provider.hcl" in current directory
    if configPath == "" {
        configPath = "provider.hcl"
    }
    
    // Make path absolute if it's not already
    if !filepath.IsAbs(configPath) {
        absPath, err := filepath.Abs(configPath)
        if err != nil {
            return nil, fmt.Errorf("failed to get absolute path: %w", err)
        }
        configPath = absPath
    }
    
    // First, load variables from variables.hcl in the same directory
    varsPath := filepath.Join(filepath.Dir(configPath), "variables.hcl")
    variables, err := loadVariables(varsPath)
    if err != nil {
        return nil, fmt.Errorf("failed to load variables: %w", err)
    }
    
    config, err := loadConfig(configPath, variables)
    if err != nil {
        return nil, err
    }
    
    fmt.Printf("Loaded provider config: %+v\n", config)
    return config, nil
}

func loadVariables(filename string) (map[string]cty.Value, error) {
    parser := hclparse.NewParser()
    file, diags := parser.ParseHCLFile(filename)
    if diags.HasErrors() {
        return nil, fmt.Errorf("failed to parse variables: %s", diags.Error())
    }
    
    var varsConfig Variables
    diags = gohcl.DecodeBody(file.Body, nil, &varsConfig)
    if diags.HasErrors() {
        return nil, fmt.Errorf("failed to decode variables: %s", diags.Error())
    }
    
    // Create a map of variable values
    vars := make(map[string]cty.Value)
    for _, v := range varsConfig.Variables {
        // First check for explicit value
        if v.Value != "" {
            vars[v.Name] = cty.StringVal(v.Value)
        } else if v.Default != "" {
            // Fall back to default if no explicit value
            vars[v.Name] = cty.StringVal(v.Default)
        } else if v.Required {
            // For required variables without values, we could error out
            return nil, fmt.Errorf("required variable %s has no value", v.Name)
        }
    }
    
    return vars, nil
}

func loadConfig(filename string, variables map[string]cty.Value) (*Config, error) {
    parser := hclparse.NewParser()
    file, diags := parser.ParseHCLFile(filename)
    if diags.HasErrors() {
        return nil, fmt.Errorf("failed to parse config: %s", diags.Error())
    }
    
    // Create an evaluation context with the variables
    evalContext := &hcl.EvalContext{
        Variables: map[string]cty.Value{
            "var": cty.ObjectVal(variables),
        },
    }
    
    var config Config
    diags = gohcl.DecodeBody(file.Body, evalContext, &config)
    
    if diags.HasErrors() {
        return nil, fmt.Errorf("failed to decode config: %s", diags.Error())
    }
    
    return &config, nil
}