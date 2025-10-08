FROM mcr.microsoft.com/vscode/devcontainers/python:3.11

# System deps
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

# Copy just pyproject for caching deps (optional)
WORKDIR /workspaces/llm-safecall
