package handlers

import (
	"log"
	"net/http"
	"strconv"
	"time"

	"github.com/go-chi/chi/v5"

	"github.com/kblack0610/actual-budget-tools/internal/actual"
	"github.com/kblack0610/actual-budget-tools/internal/detector"
	"github.com/kblack0610/actual-budget-tools/internal/models"
	"github.com/kblack0610/actual-budget-tools/internal/store"
	"github.com/kblack0610/actual-budget-tools/templates"
)

// Handler holds dependencies for HTTP handlers
type Handler struct {
	client   *actual.Client
	store    *store.Store
	detector *detector.Detector

	// Cache of detected subscriptions (cleared on each scan)
	cachedSubs []models.DetectedSubscription
}

// New creates a new Handler
func New(client *actual.Client, store *store.Store) *Handler {
	return &Handler{
		client:   client,
		store:    store,
		detector: detector.NewDetector(),
	}
}

// Dashboard renders the main dashboard
func (h *Handler) Dashboard(w http.ResponseWriter, r *http.Request) {
	stats, err := h.store.GetStats()
	if err != nil {
		log.Printf("Error getting stats: %v", err)
		stats = &store.Stats{}
	}

	serverStatus := "connected"
	if err := h.client.HealthCheck(); err != nil {
		serverStatus = "disconnected: " + err.Error()
	}

	templates.Dashboard(stats, serverStatus).Render(r.Context(), w)
}

// Subscriptions renders the subscription detection page
func (h *Handler) Subscriptions(w http.ResponseWriter, r *http.Request) {
	message := r.URL.Query().Get("message")
	templates.Subscriptions(h.cachedSubs, false, message).Render(r.Context(), w)
}

// ScanSubscriptions scans transactions for subscriptions
func (h *Handler) ScanSubscriptions(w http.ResponseWriter, r *http.Request) {
	// Fetch last 12 months of transactions
	endDate := time.Now()
	startDate := endDate.AddDate(-1, 0, 0) // 1 year ago

	transactions, err := h.client.GetTransactions("", startDate, endDate)
	if err != nil {
		log.Printf("Error fetching transactions: %v", err)
		templates.Alert("error", "Failed to fetch transactions: "+err.Error()).Render(r.Context(), w)
		return
	}

	log.Printf("Fetched %d transactions for subscription detection", len(transactions))

	// Detect subscriptions
	h.cachedSubs = h.detector.DetectSubscriptions(transactions)

	log.Printf("Detected %d potential subscriptions", len(h.cachedSubs))

	// Return the subscription list partial
	templates.SubscriptionList(h.cachedSubs).Render(r.Context(), w)
}

// QueueSubscription adds a single subscription to the review queue
func (h *Handler) QueueSubscription(w http.ResponseWriter, r *http.Request) {
	subID := chi.URLParam(r, "id")

	// Find the subscription in cache
	var sub *models.DetectedSubscription
	for i := range h.cachedSubs {
		if h.cachedSubs[i].ID == subID {
			sub = &h.cachedSubs[i]
			break
		}
	}

	if sub == nil {
		http.Error(w, "Subscription not found", http.StatusNotFound)
		return
	}

	// Check if already exists
	exists, err := h.store.SubscriptionExists(subID)
	if err != nil {
		log.Printf("Error checking subscription: %v", err)
		http.Error(w, "Database error", http.StatusInternalServerError)
		return
	}

	if exists {
		templates.QueuedRow().Render(r.Context(), w)
		return
	}

	// Add to review queue
	_, err = h.store.AddReviewItem(*sub)
	if err != nil {
		log.Printf("Error adding to queue: %v", err)
		http.Error(w, "Failed to add to queue", http.StatusInternalServerError)
		return
	}

	templates.QueuedRow().Render(r.Context(), w)
}

// QueueAllSubscriptions adds all detected subscriptions to the review queue
func (h *Handler) QueueAllSubscriptions(w http.ResponseWriter, r *http.Request) {
	added := 0
	for _, sub := range h.cachedSubs {
		exists, _ := h.store.SubscriptionExists(sub.ID)
		if exists {
			continue
		}

		_, err := h.store.AddReviewItem(sub)
		if err != nil {
			log.Printf("Error adding subscription %s: %v", sub.Payee, err)
			continue
		}
		added++
	}

	// Redirect to review page
	http.Redirect(w, r, "/review?status=pending", http.StatusSeeOther)
}

// Review renders the review queue page
func (h *Handler) Review(w http.ResponseWriter, r *http.Request) {
	status := r.URL.Query().Get("status")
	if status == "" {
		status = "pending"
	}

	items, err := h.store.GetReviewItems(status)
	if err != nil {
		log.Printf("Error getting review items: %v", err)
		items = []models.ReviewItem{}
	}

	templates.Review(items, status).Render(r.Context(), w)
}

// ApproveItem approves a review item and creates a schedule in Actual Budget
func (h *Handler) ApproveItem(w http.ResponseWriter, r *http.Request) {
	idStr := chi.URLParam(r, "id")
	id, err := strconv.ParseInt(idStr, 10, 64)
	if err != nil {
		http.Error(w, "Invalid ID", http.StatusBadRequest)
		return
	}

	// Get the review item
	item, err := h.store.GetReviewItem(id)
	if err != nil || item == nil {
		http.Error(w, "Item not found", http.StatusNotFound)
		return
	}

	// Create schedule in Actual Budget
	schedule := models.Schedule{
		Name:      item.Subscription.Payee,
		Amount:    -item.Subscription.Amount, // Negative for expenses
		Date:      calculateNextDate(item.Subscription),
		Frequency: item.Subscription.Frequency,
	}

	scheduleID, err := h.client.CreateSchedule(schedule)
	if err != nil {
		log.Printf("Error creating schedule: %v", err)
		// Still mark as approved but note the error
		templates.Alert("error", "Approved but failed to create schedule: "+err.Error()).Render(r.Context(), w)
		h.store.ApproveItem(id, "")
		return
	}

	// Update review item
	if err := h.store.ApproveItem(id, scheduleID); err != nil {
		log.Printf("Error updating review item: %v", err)
	}

	// Return updated row
	item.Status = "approved"
	item.ScheduleID = &scheduleID
	templates.ReviewRow(*item).Render(r.Context(), w)
}

// RejectItem rejects a review item
func (h *Handler) RejectItem(w http.ResponseWriter, r *http.Request) {
	idStr := chi.URLParam(r, "id")
	id, err := strconv.ParseInt(idStr, 10, 64)
	if err != nil {
		http.Error(w, "Invalid ID", http.StatusBadRequest)
		return
	}

	// Get the review item first
	item, err := h.store.GetReviewItem(id)
	if err != nil || item == nil {
		http.Error(w, "Item not found", http.StatusNotFound)
		return
	}

	if err := h.store.RejectItem(id); err != nil {
		log.Printf("Error rejecting item: %v", err)
		http.Error(w, "Failed to reject", http.StatusInternalServerError)
		return
	}

	// Return updated row
	item.Status = "rejected"
	templates.ReviewRow(*item).Render(r.Context(), w)
}

// calculateNextDate determines the next occurrence date for a subscription
func calculateNextDate(sub models.DetectedSubscription) time.Time {
	now := time.Now()
	lastSeen := sub.LastSeen

	switch sub.Frequency {
	case "monthly":
		// Add months until we're in the future
		next := lastSeen.AddDate(0, 1, 0)
		for next.Before(now) {
			next = next.AddDate(0, 1, 0)
		}
		return next
	case "yearly":
		next := lastSeen.AddDate(1, 0, 0)
		for next.Before(now) {
			next = next.AddDate(1, 0, 0)
		}
		return next
	case "weekly":
		next := lastSeen.AddDate(0, 0, 7)
		for next.Before(now) {
			next = next.AddDate(0, 0, 7)
		}
		return next
	default:
		return now.AddDate(0, 1, 0) // Default to 1 month from now
	}
}
