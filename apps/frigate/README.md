# Frigate

Standalone Docker Compose deployment for the Frigate NVR service.

## What This Directory Contains

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Single-service Frigate deployment exposing port `5000` |

Expected local directories next to the compose file:

- `config/` for Frigate configuration
- `storage/` for media and recordings

## Run

```bash
cd apps/frigate
docker compose up -d
```

## Verify

```bash
cd apps/frigate
docker compose ps
docker compose logs --tail=100 frigate
```

The UI is exposed on `http://<host>:5000`.

## Notes

- USB devices are passed through from `/dev/bus/usb`.
- Media is persisted under `./storage`.
- A tmpfs cache is mounted at `/tmp/cache` to reduce disk wear.

## Related Docs

- [../../README.md](../../README.md)
- [../../infrastructure.md](../../infrastructure.md)
