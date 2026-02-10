package store

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"time"

	_ "github.com/mattn/go-sqlite3"

	"github.com/kblack0610/actual-budget-tools/internal/models"
)

// Store handles persistence for review items
type Store struct {
	db *sql.DB
}

// New creates a new SQLite store
func New(dbPath string) (*Store, error) {
	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		return nil, fmt.Errorf("open database: %w", err)
	}

	store := &Store{db: db}
	if err := store.migrate(); err != nil {
		return nil, fmt.Errorf("migrate database: %w", err)
	}

	return store, nil
}

// Close closes the database connection
func (s *Store) Close() error {
	return s.db.Close()
}

// migrate creates the necessary tables
func (s *Store) migrate() error {
	schema := `
	CREATE TABLE IF NOT EXISTS review_items (
		id INTEGER PRIMARY KEY AUTOINCREMENT,
		subscription_json TEXT NOT NULL,
		status TEXT NOT NULL DEFAULT 'pending',
		created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
		reviewed_at DATETIME,
		schedule_id TEXT
	);

	CREATE INDEX IF NOT EXISTS idx_review_items_status ON review_items(status);
	CREATE INDEX IF NOT EXISTS idx_review_items_created_at ON review_items(created_at);
	`

	_, err := s.db.Exec(schema)
	return err
}

// AddReviewItem adds a detected subscription to the review queue
func (s *Store) AddReviewItem(sub models.DetectedSubscription) (int64, error) {
	subJSON, err := json.Marshal(sub)
	if err != nil {
		return 0, fmt.Errorf("marshal subscription: %w", err)
	}

	result, err := s.db.Exec(
		`INSERT INTO review_items (subscription_json, status, created_at) VALUES (?, 'pending', ?)`,
		string(subJSON),
		time.Now().UTC(),
	)
	if err != nil {
		return 0, fmt.Errorf("insert review item: %w", err)
	}

	return result.LastInsertId()
}

// GetReviewItems returns review items filtered by status
func (s *Store) GetReviewItems(status string) ([]models.ReviewItem, error) {
	query := `SELECT id, subscription_json, status, created_at, reviewed_at, schedule_id FROM review_items`
	args := []interface{}{}

	if status != "" && status != "all" {
		query += ` WHERE status = ?`
		args = append(args, status)
	}

	query += ` ORDER BY created_at DESC`

	rows, err := s.db.Query(query, args...)
	if err != nil {
		return nil, fmt.Errorf("query review items: %w", err)
	}
	defer rows.Close()

	var items []models.ReviewItem
	for rows.Next() {
		var item models.ReviewItem
		var subJSON string
		var reviewedAt sql.NullTime
		var scheduleID sql.NullString

		err := rows.Scan(&item.ID, &subJSON, &item.Status, &item.CreatedAt, &reviewedAt, &scheduleID)
		if err != nil {
			return nil, fmt.Errorf("scan row: %w", err)
		}

		if err := json.Unmarshal([]byte(subJSON), &item.Subscription); err != nil {
			return nil, fmt.Errorf("unmarshal subscription: %w", err)
		}

		if reviewedAt.Valid {
			item.ReviewedAt = &reviewedAt.Time
		}
		if scheduleID.Valid {
			item.ScheduleID = &scheduleID.String
		}

		items = append(items, item)
	}

	return items, nil
}

// GetReviewItem returns a single review item by ID
func (s *Store) GetReviewItem(id int64) (*models.ReviewItem, error) {
	var item models.ReviewItem
	var subJSON string
	var reviewedAt sql.NullTime
	var scheduleID sql.NullString

	err := s.db.QueryRow(
		`SELECT id, subscription_json, status, created_at, reviewed_at, schedule_id
		 FROM review_items WHERE id = ?`,
		id,
	).Scan(&item.ID, &subJSON, &item.Status, &item.CreatedAt, &reviewedAt, &scheduleID)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("query review item: %w", err)
	}

	if err := json.Unmarshal([]byte(subJSON), &item.Subscription); err != nil {
		return nil, fmt.Errorf("unmarshal subscription: %w", err)
	}

	if reviewedAt.Valid {
		item.ReviewedAt = &reviewedAt.Time
	}
	if scheduleID.Valid {
		item.ScheduleID = &scheduleID.String
	}

	return &item, nil
}

// ApproveItem marks an item as approved and records the schedule ID
func (s *Store) ApproveItem(id int64, scheduleID string) error {
	_, err := s.db.Exec(
		`UPDATE review_items SET status = 'approved', reviewed_at = ?, schedule_id = ? WHERE id = ?`,
		time.Now().UTC(),
		scheduleID,
		id,
	)
	return err
}

// RejectItem marks an item as rejected
func (s *Store) RejectItem(id int64) error {
	_, err := s.db.Exec(
		`UPDATE review_items SET status = 'rejected', reviewed_at = ? WHERE id = ?`,
		time.Now().UTC(),
		id,
	)
	return err
}

// ClearPending removes all pending items (for re-scanning)
func (s *Store) ClearPending() error {
	_, err := s.db.Exec(`DELETE FROM review_items WHERE status = 'pending'`)
	return err
}

// Stats returns statistics about review items
type Stats struct {
	Pending  int `json:"pending"`
	Approved int `json:"approved"`
	Rejected int `json:"rejected"`
	Total    int `json:"total"`
}

func (s *Store) GetStats() (*Stats, error) {
	var stats Stats

	err := s.db.QueryRow(`
		SELECT
			COALESCE(SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END), 0),
			COALESCE(SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END), 0),
			COALESCE(SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END), 0),
			COUNT(*)
		FROM review_items
	`).Scan(&stats.Pending, &stats.Approved, &stats.Rejected, &stats.Total)

	if err != nil {
		return nil, fmt.Errorf("query stats: %w", err)
	}

	return &stats, nil
}

// SubscriptionExists checks if a subscription with the same ID already exists
func (s *Store) SubscriptionExists(subID string) (bool, error) {
	var count int
	err := s.db.QueryRow(
		`SELECT COUNT(*) FROM review_items WHERE json_extract(subscription_json, '$.id') = ?`,
		subID,
	).Scan(&count)
	if err != nil {
		return false, fmt.Errorf("check subscription exists: %w", err)
	}
	return count > 0, nil
}
