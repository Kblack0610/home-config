# Overlay on top of upstream OpenClaw to add CLI binaries that unlock
# bundled skills (coding-agent, github, gh-issues, session-logs, tmux, ...).
# See `openclaw skills check` for the full readiness matrix.
FROM ghcr.io/openclaw/openclaw:latest

USER root

# github-cli is not in the default Bookworm repos — add the upstream apt source.
RUN set -eu \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      gnupg \
 && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      > /usr/share/keyrings/githubcli-archive-keyring.gpg \
 && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      gh \
      jq \
      ripgrep \
      tmux \
 && rm -rf /var/lib/apt/lists/* \
 && npm install -g opencode-ai \
 && chown -R node:node /home/node

USER node
