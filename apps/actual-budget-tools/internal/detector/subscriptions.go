package detector

import (
	"crypto/sha256"
	"fmt"
	"math"
	"sort"
	"strings"

	"github.com/kblack0610/actual-budget-tools/internal/models"
)

// Detector analyzes transactions for recurring patterns
type Detector struct {
	minOccurrences       int
	minOccurrencesYearly int     // Lower threshold for yearly patterns
	tolerance            float64 // Percentage tolerance for amount matching
}

// NewDetector creates a subscription detector
func NewDetector() *Detector {
	return &Detector{
		minOccurrences:       2,    // At least 2 occurrences for weekly/monthly
		minOccurrencesYearly: 2,    // Only 2 needed for yearly (2 years of data)
		tolerance:            0.20, // 20% tolerance for amount differences (covers price changes, prorations)
	}
}

// transactionKey groups transactions by payee and approximate amount
type transactionKey struct {
	payee  string
	amount int64 // Normalized amount (absolute value, rounded)
}

// transactionGroup holds transactions with the same key
type transactionGroup struct {
	key          transactionKey
	transactions []models.Transaction
}

// DetectSubscriptions analyzes transactions and returns detected subscriptions
func (d *Detector) DetectSubscriptions(transactions []models.Transaction) []models.DetectedSubscription {
	// Group transactions by payee and similar amount
	groups := d.groupTransactions(transactions)

	var subscriptions []models.DetectedSubscription

	for _, group := range groups {
		// Use lower threshold initially to catch yearly patterns
		if len(group.transactions) < d.minOccurrencesYearly {
			continue
		}

		// Analyze the pattern
		sub := d.analyzePattern(group)
		if sub == nil {
			continue
		}
		if sub.Confidence < 0.5 {
			continue
		}

		// Apply frequency-specific occurrence requirements
		if sub.Frequency == "yearly" {
			if sub.OccurrenceCount < d.minOccurrencesYearly {
				continue
			}
		} else {
			// Weekly/monthly require more occurrences
			if sub.OccurrenceCount < d.minOccurrences {
				continue
			}
		}

		subscriptions = append(subscriptions, *sub)
	}

	// Sort by confidence (descending) then by occurrence count
	sort.Slice(subscriptions, func(i, j int) bool {
		if subscriptions[i].Confidence != subscriptions[j].Confidence {
			return subscriptions[i].Confidence > subscriptions[j].Confidence
		}
		return subscriptions[i].OccurrenceCount > subscriptions[j].OccurrenceCount
	})

	return subscriptions
}

// groupTransactions groups transactions by payee and similar amounts
func (d *Detector) groupTransactions(transactions []models.Transaction) []transactionGroup {
	groupMap := make(map[transactionKey][]models.Transaction)

	for _, tx := range transactions {
		// Skip positive amounts (income/refunds)
		if tx.Amount >= 0 {
			continue
		}

		// Normalize payee name
		payee := normalizePayee(tx.Payee)
		if payee == "" {
			continue
		}

		// Normalize amount (absolute value)
		amount := abs(tx.Amount)

		key := transactionKey{payee: payee, amount: amount}

		// Check if we have a similar amount group
		found := false
		for existingKey := range groupMap {
			if existingKey.payee == payee && d.amountsMatch(existingKey.amount, amount) {
				groupMap[existingKey] = append(groupMap[existingKey], tx)
				found = true
				break
			}
		}

		if !found {
			groupMap[key] = []models.Transaction{tx}
		}
	}

	// Convert map to slice
	var groups []transactionGroup
	for key, txs := range groupMap {
		groups = append(groups, transactionGroup{key: key, transactions: txs})
	}

	return groups
}

// analyzePattern determines if a group of transactions represents a subscription
func (d *Detector) analyzePattern(group transactionGroup) *models.DetectedSubscription {
	txs := group.transactions

	// Sort by date
	sort.Slice(txs, func(i, j int) bool {
		return txs[i].Date.Before(txs[j].Date)
	})

	// Calculate intervals between transactions (filtering out same-day duplicates)
	var intervals []float64
	for i := 1; i < len(txs); i++ {
		days := txs[i].Date.Sub(txs[i-1].Date).Hours() / 24
		// Skip 0-day intervals (same-day transactions are likely duplicates or multiple services)
		if days >= 1 {
			intervals = append(intervals, days)
		}
	}

	if len(intervals) == 0 {
		return nil
	}

	// Calculate average interval
	avgInterval := mean(intervals)

	// Determine frequency based on average interval
	frequency, confidence := d.determineFrequency(intervals, avgInterval)
	if frequency == "" {
		return nil
	}

	// Calculate average amount
	var amounts []int64
	for _, tx := range txs {
		amounts = append(amounts, abs(tx.Amount))
	}
	avgAmount := meanInt(amounts)

	// Generate unique ID based on payee and amount
	id := generateID(group.key.payee, avgAmount)

	return &models.DetectedSubscription{
		ID:              id,
		Payee:           txs[0].Payee, // Use original payee name
		Amount:          avgAmount,
		Frequency:       frequency,
		OccurrenceCount: len(txs),
		FirstSeen:       txs[0].Date,
		LastSeen:        txs[len(txs)-1].Date,
		AverageInterval: avgInterval,
		Confidence:      confidence,
	}
}

// determineFrequency analyzes intervals to determine subscription frequency
func (d *Detector) determineFrequency(intervals []float64, avgInterval float64) (string, float64) {
	// Expected intervals for different frequencies
	frequencies := map[string]float64{
		"weekly":  7,
		"monthly": 30.44, // Average days in a month
		"yearly":  365.25,
	}

	// Find best matching frequency
	var bestFreq string
	var bestScore float64

	for freq, expected := range frequencies {
		// Calculate how close the average interval is to expected
		deviation := math.Abs(avgInterval-expected) / expected

		// Calculate consistency (how much the intervals vary)
		consistency := d.calculateConsistency(intervals, expected)

		// Combined score: lower deviation + higher consistency = better
		score := consistency * (1 - math.Min(deviation, 1))

		if score > bestScore {
			bestScore = score
			bestFreq = freq
		}
	}

	// Require minimum confidence
	if bestScore < 0.3 {
		return "", 0
	}

	return bestFreq, bestScore
}

// calculateConsistency measures how consistent the intervals are
func (d *Detector) calculateConsistency(intervals []float64, expectedInterval float64) float64 {
	if len(intervals) == 0 {
		return 0
	}

	// Calculate standard deviation relative to expected interval
	var sumSquaredDiff float64
	for _, interval := range intervals {
		diff := (interval - expectedInterval) / expectedInterval
		sumSquaredDiff += diff * diff
	}
	stdDev := math.Sqrt(sumSquaredDiff / float64(len(intervals)))

	// Convert to 0-1 score (lower stdDev = higher consistency)
	// stdDev of 0.5 (50% variation) maps to ~0.6 consistency
	consistency := 1.0 / (1.0 + stdDev*2)

	return consistency
}

// amountsMatch checks if two amounts are within tolerance
func (d *Detector) amountsMatch(a, b int64) bool {
	if a == b {
		return true
	}
	diff := math.Abs(float64(a - b))
	avg := float64(a+b) / 2
	return diff/avg <= d.tolerance
}

// Helper functions

func normalizePayee(payee string) string {
	// Remove common suffixes/variations
	payee = strings.ToLower(strings.TrimSpace(payee))

	// Remove common payment processor prefixes
	prefixes := []string{"sq *", "paypal *", "venmo *", "zelle *"}
	for _, prefix := range prefixes {
		payee = strings.TrimPrefix(payee, prefix)
	}

	// Remove trailing reference numbers (common in bank statements)
	// This is simplified - in production you'd want more sophisticated normalization
	parts := strings.Fields(payee)
	if len(parts) > 1 {
		// Check if last part looks like a reference number
		last := parts[len(parts)-1]
		if len(last) > 4 && isAlphanumeric(last) {
			payee = strings.Join(parts[:len(parts)-1], " ")
		}
	}

	return strings.TrimSpace(payee)
}

func isAlphanumeric(s string) bool {
	for _, c := range s {
		if !((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9')) {
			return false
		}
	}
	return true
}

func abs(n int64) int64 {
	if n < 0 {
		return -n
	}
	return n
}

func mean(values []float64) float64 {
	if len(values) == 0 {
		return 0
	}
	var sum float64
	for _, v := range values {
		sum += v
	}
	return sum / float64(len(values))
}

func meanInt(values []int64) int64 {
	if len(values) == 0 {
		return 0
	}
	var sum int64
	for _, v := range values {
		sum += v
	}
	return sum / int64(len(values))
}

func generateID(payee string, amount int64) string {
	data := fmt.Sprintf("%s:%d", payee, amount)
	hash := sha256.Sum256([]byte(data))
	return fmt.Sprintf("%x", hash[:8])
}
