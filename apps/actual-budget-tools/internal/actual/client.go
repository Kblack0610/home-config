package actual

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"

	"github.com/kblack0610/actual-budget-tools/internal/models"
)

// Client handles communication with Actual Budget server
type Client struct {
	baseURL    string
	password   string
	budgetID   string
	httpClient *http.Client
	token      string
}

// NewClient creates a new Actual Budget API client
func NewClient(baseURL, password, budgetID string) *Client {
	return &Client{
		baseURL:  baseURL,
		password: password,
		budgetID: budgetID,
		httpClient: &http.Client{
			Timeout: 30 * time.Second,
		},
	}
}

// LoginResponse from Actual Budget server
type LoginResponse struct {
	Status string `json:"status"`
	Data   struct {
		Token string `json:"token"`
	} `json:"data"`
}

// Login authenticates with the Actual Budget server
func (c *Client) Login() error {
	payload := map[string]string{"password": c.password}
	body, err := json.Marshal(payload)
	if err != nil {
		return fmt.Errorf("marshal login payload: %w", err)
	}

	req, err := http.NewRequest("POST", c.baseURL+"/account/login", bytes.NewReader(body))
	if err != nil {
		return fmt.Errorf("create login request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return fmt.Errorf("login request failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("login failed: %s - %s", resp.Status, string(body))
	}

	var loginResp LoginResponse
	if err := json.NewDecoder(resp.Body).Decode(&loginResp); err != nil {
		return fmt.Errorf("decode login response: %w", err)
	}

	c.token = loginResp.Data.Token
	return nil
}

// TransactionsResponse from query endpoint
type TransactionsResponse struct {
	Status string `json:"status"`
	Data   struct {
		Transactions []RawTransaction `json:"transactions"`
	} `json:"data"`
}

// RawTransaction as returned by Actual API
type RawTransaction struct {
	ID         string  `json:"id"`
	AccountID  string  `json:"account"`
	Date       string  `json:"date"`
	Amount     int64   `json:"amount"`
	PayeeName  string  `json:"payee_name"`
	PayeeID    string  `json:"payee"`
	CategoryID string  `json:"category"`
	Notes      string  `json:"notes"`
	Cleared    bool    `json:"cleared"`
}

// GetTransactions fetches transactions from Actual Budget
// Note: This is a simplified version - the actual API may require
// using the sync protocol. We may need to adjust based on testing.
func (c *Client) GetTransactions(accountID string, startDate, endDate time.Time) ([]models.Transaction, error) {
	if c.token == "" {
		if err := c.Login(); err != nil {
			return nil, fmt.Errorf("auto-login failed: %w", err)
		}
	}

	// Build query for transactions
	// Actual Budget uses a query language for fetching data
	query := map[string]interface{}{
		"query": map[string]interface{}{
			"transactions": map[string]interface{}{
				"$filter": map[string]interface{}{
					"date": map[string]interface{}{
						"$gte": startDate.Format("2006-01-02"),
						"$lte": endDate.Format("2006-01-02"),
					},
				},
				"$select": []string{"id", "account", "date", "amount", "payee_name", "payee", "category", "notes", "cleared"},
			},
		},
	}

	body, err := json.Marshal(query)
	if err != nil {
		return nil, fmt.Errorf("marshal query: %w", err)
	}

	req, err := http.NewRequest("POST", c.baseURL+"/api/query", bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("create request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-ACTUAL-TOKEN", c.token)

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("query request failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("query failed: %s - %s", resp.Status, string(body))
	}

	var queryResp TransactionsResponse
	if err := json.NewDecoder(resp.Body).Decode(&queryResp); err != nil {
		return nil, fmt.Errorf("decode response: %w", err)
	}

	// Convert raw transactions to our model
	transactions := make([]models.Transaction, 0, len(queryResp.Data.Transactions))
	for _, raw := range queryResp.Data.Transactions {
		date, _ := time.Parse("2006-01-02", raw.Date)
		transactions = append(transactions, models.Transaction{
			ID:        raw.ID,
			AccountID: raw.AccountID,
			Date:      date,
			Amount:    raw.Amount,
			Payee:     raw.PayeeName,
			PayeeID:   raw.PayeeID,
			Category:  raw.CategoryID,
			Notes:     raw.Notes,
			Cleared:   raw.Cleared,
		})
	}

	return transactions, nil
}

// CreateSchedule creates a new schedule in Actual Budget
func (c *Client) CreateSchedule(schedule models.Schedule) (string, error) {
	if c.token == "" {
		if err := c.Login(); err != nil {
			return "", fmt.Errorf("auto-login failed: %w", err)
		}
	}

	// Convert frequency to Actual's pattern format
	var pattern map[string]interface{}
	switch schedule.Frequency {
	case "monthly":
		pattern = map[string]interface{}{"value": 1, "type": "month"}
	case "yearly":
		pattern = map[string]interface{}{"value": 1, "type": "year"}
	case "weekly":
		pattern = map[string]interface{}{"value": 1, "type": "week"}
	default:
		pattern = map[string]interface{}{"value": 1, "type": "month"}
	}

	payload := map[string]interface{}{
		"schedule": map[string]interface{}{
			"name":   schedule.Name,
			"amount": schedule.Amount,
			"date": map[string]interface{}{
				"start":    schedule.Date.Format("2006-01-02"),
				"patterns": []interface{}{pattern},
			},
			"posts_transaction": true,
		},
	}

	body, err := json.Marshal(payload)
	if err != nil {
		return "", fmt.Errorf("marshal schedule: %w", err)
	}

	req, err := http.NewRequest("POST", c.baseURL+"/api/schedules/create", bytes.NewReader(body))
	if err != nil {
		return "", fmt.Errorf("create request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-ACTUAL-TOKEN", c.token)

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return "", fmt.Errorf("create schedule request failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return "", fmt.Errorf("create schedule failed: %s - %s", resp.Status, string(body))
	}

	var result struct {
		Status string `json:"status"`
		Data   struct {
			ID string `json:"id"`
		} `json:"data"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return "", fmt.Errorf("decode response: %w", err)
	}

	return result.Data.ID, nil
}

// HealthCheck verifies connection to Actual Budget server
func (c *Client) HealthCheck() error {
	resp, err := c.httpClient.Get(c.baseURL + "/")
	if err != nil {
		return fmt.Errorf("health check failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("server returned %s", resp.Status)
	}
	return nil
}
