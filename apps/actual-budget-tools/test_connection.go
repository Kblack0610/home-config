//go:build ignore

package main

import (
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"strings"
)

func main() {
	serverURL := "http://localhost:5006"
	password := "e1e2e3e4"

	// Test basic connectivity
	fmt.Println("Testing basic connectivity...")
	resp, err := http.Get(serverURL)
	if err != nil {
		log.Fatalf("Failed to connect: %v", err)
	}
	fmt.Printf("Server responded with status: %d\n", resp.StatusCode)
	resp.Body.Close()

	// Test login
	fmt.Println("\nTesting login...")
	loginPayload := fmt.Sprintf(`{"password":"%s"}`, password)
	loginResp, err := http.Post(serverURL+"/account/login", "application/json", strings.NewReader(loginPayload))
	if err != nil {
		log.Fatalf("Failed to login: %v", err)
	}
	defer loginResp.Body.Close()

	fmt.Printf("Login status: %d\n", loginResp.StatusCode)

	var loginResult map[string]interface{}
	if err := json.NewDecoder(loginResp.Body).Decode(&loginResult); err != nil {
		fmt.Printf("Could not decode login response: %v\n", err)
		return
	}
	fmt.Printf("Login response: %+v\n", loginResult)

	data, ok := loginResult["data"].(map[string]interface{})
	if !ok {
		fmt.Println("Could not parse login data")
		return
	}

	token, ok := data["token"].(string)
	if !ok {
		fmt.Println("Login failed - no token returned")
		return
	}

	fmt.Printf("\n✓ Got token: %s\n", token)

	// List budgets
	fmt.Println("\n=== Fetching available budgets ===")

	client := &http.Client{}

	// Try sync endpoint to list files
	req, _ := http.NewRequest("POST", serverURL+"/sync/list-user-files", strings.NewReader("{}"))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Actual-Token", token)

	budgetResp, err := client.Do(req)
	if err != nil {
		log.Printf("Failed to list budgets: %v", err)
		return
	}
	defer budgetResp.Body.Close()

	body, _ := io.ReadAll(budgetResp.Body)
	fmt.Printf("List budgets status: %d\n", budgetResp.StatusCode)
	fmt.Printf("Response: %s\n", string(body))

	var budgetResult map[string]interface{}
	if err := json.Unmarshal(body, &budgetResult); err == nil {
		if data, ok := budgetResult["data"].(map[string]interface{}); ok {
			if files, ok := data["files"].([]interface{}); ok {
				fmt.Println("\n=== Available Budgets ===")
				for _, f := range files {
					file := f.(map[string]interface{})
					fmt.Printf("  Name: %v\n", file["name"])
					fmt.Printf("  Sync ID: %v\n", file["groupId"])
					fmt.Printf("  ---\n")
				}
			}
		}
	}
}
