FROM rust:1.88-slim AS builder
WORKDIR /build
COPY . .
RUN cargo build --release -p mqtt2prom

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=builder /build/target/release/mqtt2prom /usr/local/bin/mqtt2prom
EXPOSE 9201
CMD ["mqtt2prom"]
