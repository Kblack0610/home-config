# Actual Budget Tools

A Go-based companion service for [Actual Budget](https://actualbudget.org/) that provides automated subscription detection and schedule management.

## Features

- **Subscription Detection**: Analyzes transaction history to identify recurring payments
- **Review Queue**: Manual approval workflow before importing to Actual Budget
- **Schedule Creation**: One-click creation of schedules in Actual Budget
- **Lightweight**: ~15-20MB Docker image (Go binary + Alpine)

## Quick Start

### Prerequisites

- Go 1.22+ (for local development)
- Docker (for containerized deployment)
- Actual Budget server running

### Local Development

1. **Install templ** (Go template compiler):
   ```bash
   go install github.com/a-h/templ/cmd/templ@latest
   ```

2. **Generate templates**:
   ```bash
   templ generate
   ```

3. **Set environment variables**:
   ```bash
   export ACTUAL_SERVER_URL="https://finance.kblab.me"
   export ACTUAL_PASSWORD="your-server-password"
   export ACTUAL_BUDGET_ID="your-budget-sync-id"
   ```

4. **Run the server**:
   ```bash
   go run ./cmd/server
   ```

5. **Access the UI**: http://localhost:8080

### Docker Compose

1. **Create `.env` file**:
   ```bash
   ACTUAL_SERVER_URL=https://finance.kblab.me
   ACTUAL_PASSWORD=your-password
   ACTUAL_BUDGET_ID=your-budget-id
   ```

2. **Start the service**:
   ```bash
   docker-compose up -d
   ```

3. **Access the UI**: http://localhost:8080

### Kubernetes

1. **Update the secret** in `k8s/secret.yaml` with your Actual Budget credentials

2. **Build and push the image**:
   ```bash
   docker build -t ghcr.io/kblack0610/actual-budget-tools:latest .
   docker push ghcr.io/kblack0610/actual-budget-tools:latest
   ```

3. **Deploy**:
   ```bash
   kubectl apply -k k8s/
   ```

4. **Access the UI**: https://finance-tools.kblab.me

## Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `ACTUAL_SERVER_URL` | `http://localhost:5006` | URL of your Actual Budget server |
| `ACTUAL_PASSWORD` | (required) | Your Actual Budget server password |
| `ACTUAL_BUDGET_ID` | (required) | Your budget's Sync ID (Settings → Advanced) |
| `DATABASE_PATH` | `./data/tools.db` | Path to SQLite database |
| `LISTEN_ADDR` | `:8080` | Server listen address |

## Finding Your Budget ID

1. Open Actual Budget
2. Go to **Settings** (bottom of sidebar)
3. Click **Show Advanced Settings**
4. Copy the **Sync ID**

## How It Works

### Subscription Detection Algorithm

1. Fetches last 12 months of transactions from Actual Budget
2. Groups transactions by normalized payee name and similar amounts (2% tolerance)
3. Analyzes time intervals between occurrences
4. Calculates confidence score based on:
   - Interval consistency (how regular the payments are)
   - Match to expected frequency (weekly/monthly/yearly)
5. Returns subscriptions with confidence ≥ 50%

### Review Workflow

1. **Scan**: Click "Scan Transactions" to detect subscriptions
2. **Review**: Add items to the review queue
3. **Approve/Reject**: Approve creates a schedule in Actual Budget
4. **Verify**: Check Schedules tab in Actual Budget

## Project Structure

```
actual-budget-tools/
├── cmd/server/           # Main entry point
├── internal/
│   ├── actual/           # Actual Budget API client
│   ├── detector/         # Subscription detection logic
│   ├── handlers/         # HTTP handlers
│   ├── models/           # Data types
│   └── store/            # SQLite persistence
├── templates/            # templ templates (htmx UI)
├── k8s/                  # Kubernetes manifests
├── Dockerfile
├── docker-compose.yaml
└── go.mod
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Dashboard |
| GET | `/subscriptions` | Subscription detection page |
| POST | `/subscriptions/scan` | Scan transactions for subscriptions |
| POST | `/subscriptions/queue/{id}` | Add subscription to review queue |
| POST | `/subscriptions/queue-all` | Add all subscriptions to queue |
| GET | `/review` | Review queue page |
| POST | `/review/{id}/approve` | Approve and create schedule |
| POST | `/review/{id}/reject` | Reject item |
| GET | `/health` | Health check |

## Troubleshooting

### "Failed to fetch transactions"

- Verify `ACTUAL_SERVER_URL` is correct and accessible
- Check `ACTUAL_PASSWORD` is correct
- Ensure the server is running

### "Could not connect to Actual Budget server"

- The server shows a warning on startup if it can't reach Actual Budget
- This is non-fatal; the service will retry on each request

### Empty subscription list

- Make sure you have at least 3 occurrences of a payment
- Check that payments have the same or similar amounts (within 2%)
- Ensure transactions span at least a few months

## Future Enhancements

- [ ] LLM-powered transaction categorization
- [ ] Bank statement PDF parsing
- [ ] Spending trend analysis
- [ ] Budget recommendations
- [ ] GoCardless bank sync integration

## License

MIT
