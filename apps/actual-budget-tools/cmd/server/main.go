package main

import (
	"log"
	"net/http"
	"os"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"

	"github.com/kblack0610/actual-budget-tools/internal/actual"
	"github.com/kblack0610/actual-budget-tools/internal/handlers"
	"github.com/kblack0610/actual-budget-tools/internal/models"
	"github.com/kblack0610/actual-budget-tools/internal/store"
)

func main() {
	cfg := loadConfig()

	log.Printf("Starting Actual Budget Tools")
	log.Printf("Actual Server URL: %s", cfg.ActualServerURL)
	log.Printf("Database Path: %s", cfg.DatabasePath)

	// Initialize store
	db, err := store.New(cfg.DatabasePath)
	if err != nil {
		log.Fatalf("Failed to initialize database: %v", err)
	}
	defer db.Close()

	// Initialize Actual Budget client
	client := actual.NewClient(cfg.ActualServerURL, cfg.ActualPassword, cfg.ActualBudgetID)

	// Test connection
	if err := client.HealthCheck(); err != nil {
		log.Printf("Warning: Could not connect to Actual Budget server: %v", err)
	} else {
		log.Printf("Successfully connected to Actual Budget server")
	}

	// Initialize handlers
	h := handlers.New(client, db)

	// Set up router
	r := chi.NewRouter()
	r.Use(middleware.Logger)
	r.Use(middleware.Recoverer)
	r.Use(middleware.Compress(5))

	// Routes
	r.Get("/", h.Dashboard)
	r.Get("/subscriptions", h.Subscriptions)
	r.Post("/subscriptions/scan", h.ScanSubscriptions)
	r.Post("/subscriptions/queue/{id}", h.QueueSubscription)
	r.Post("/subscriptions/queue-all", h.QueueAllSubscriptions)
	r.Get("/review", h.Review)
	r.Post("/review/{id}/approve", h.ApproveItem)
	r.Post("/review/{id}/reject", h.RejectItem)

	// Health check
	r.Get("/health", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.Write([]byte("ok"))
	})

	log.Printf("Server listening on %s", cfg.ListenAddr)
	if err := http.ListenAndServe(cfg.ListenAddr, r); err != nil {
		log.Fatalf("Server failed: %v", err)
	}
}

func loadConfig() models.Config {
	return models.Config{
		ActualServerURL: getEnv("ACTUAL_SERVER_URL", "http://localhost:5006"),
		ActualPassword:  getEnv("ACTUAL_PASSWORD", ""),
		ActualBudgetID:  getEnv("ACTUAL_BUDGET_ID", ""),
		DatabasePath:    getEnv("DATABASE_PATH", "./data/tools.db"),
		ListenAddr:      getEnv("LISTEN_ADDR", ":8080"),
	}
}

func getEnv(key, defaultValue string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return defaultValue
}
