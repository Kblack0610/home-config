# ESP32 Rust Development Environment
# Based on official Espressif Rust image with additional tools

FROM espressif/idf-rust:all_1.82.0.0

# Set environment variables
ENV CARGO_HOME=/usr/local/cargo
ENV RUSTUP_HOME=/usr/local/rustup
ENV PATH="${CARGO_HOME}/bin:${PATH}"

# Install additional tools
RUN apt-get update && apt-get install -y \
    git \
    curl \
    pkg-config \
    libudev-dev \
    && rm -rf /var/lib/apt/lists/*

# Install espflash for flashing firmware
RUN cargo install espflash --locked

# Set working directory
WORKDIR /workspace

# Default command
CMD ["/bin/bash"]
