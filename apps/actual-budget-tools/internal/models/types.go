package models

import "time"

// Transaction represents a transaction from Actual Budget
type Transaction struct {
	ID        string    `json:"id"`
	AccountID string    `json:"account_id"`
	Date      time.Time `json:"date"`
	Amount    int64     `json:"amount"` // In cents
	Payee     string    `json:"payee"`
	PayeeID   string    `json:"payee_id"`
	Category  string    `json:"category"`
	Notes     string    `json:"notes"`
	Cleared   bool      `json:"cleared"`
}

// DetectedSubscription represents a recurring payment pattern
type DetectedSubscription struct {
	ID              string    `json:"id"`
	Payee           string    `json:"payee"`
	Amount          int64     `json:"amount"`          // In cents
	Frequency       string    `json:"frequency"`       // "monthly", "yearly", "weekly"
	OccurrenceCount int       `json:"occurrence_count"`
	LastSeen        time.Time `json:"last_seen"`
	FirstSeen       time.Time `json:"first_seen"`
	AverageInterval float64   `json:"average_interval"` // Days between occurrences
	Confidence      float64   `json:"confidence"`       // 0.0 - 1.0
}

// ReviewItem represents an item in the review queue
type ReviewItem struct {
	ID           int64     `json:"id"`
	Subscription DetectedSubscription `json:"subscription"`
	Status       string    `json:"status"` // "pending", "approved", "rejected"
	CreatedAt    time.Time `json:"created_at"`
	ReviewedAt   *time.Time `json:"reviewed_at,omitempty"`
	ScheduleID   *string   `json:"schedule_id,omitempty"` // Set after creating schedule
}

// Schedule represents a schedule to create in Actual Budget
type Schedule struct {
	Name       string    `json:"name"`
	Amount     int64     `json:"amount"`
	Date       time.Time `json:"date"`
	Frequency  string    `json:"frequency"` // "monthly", "yearly", "weekly"
	CategoryID *string   `json:"category_id,omitempty"`
	AccountID  *string   `json:"account_id,omitempty"`
}

// Config holds application configuration
type Config struct {
	ActualServerURL string
	ActualPassword  string
	ActualBudgetID  string
	DatabasePath    string
	ListenAddr      string
}
