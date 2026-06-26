#!/usr/bin/env bash
#
# bootstrap.sh — install Terraform + AWS CLI v2 into ~/.local/bin (no root needed).
# Idempotent: skips anything already installed. Detects arch (arm64 / x86_64).
#
set -euo pipefail
BIN="$HOME/.local/bin"
mkdir -p "$BIN"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "NOTE: add $BIN to your PATH"; export PATH="$BIN:$PATH";; esac

arch="$(uname -m)"
case "$arch" in
  aarch64|arm64) TF_ARCH=arm64; AWS_ARCH=aarch64 ;;
  x86_64|amd64)  TF_ARCH=amd64; AWS_ARCH=x86_64  ;;
  *) echo "unsupported arch: $arch" >&2; exit 1 ;;
esac

if ! command -v aws >/dev/null 2>&1; then
  echo "==> installing AWS CLI v2 ($AWS_ARCH)"
  tmp="$(mktemp -d)"
  curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-${AWS_ARCH}.zip" -o "$tmp/aws.zip"
  (cd "$tmp" && unzip -q aws.zip && ./aws/install -i "$HOME/.local/aws-cli" -b "$BIN" --update)
  rm -rf "$tmp"
else
  echo "==> aws already installed: $(aws --version 2>&1)"
fi

if ! command -v terraform >/dev/null 2>&1; then
  echo "==> installing Terraform ($TF_ARCH)"
  ver="$(curl -fsSL https://api.releases.hashicorp.com/v1/releases/terraform/latest | jq -r .version)"
  tmp="$(mktemp -d)"
  curl -fsSL "https://releases.hashicorp.com/terraform/${ver}/terraform_${ver}_linux_${TF_ARCH}.zip" -o "$tmp/tf.zip"
  (cd "$tmp" && unzip -q tf.zip && install -m 0755 terraform "$BIN/terraform")
  rm -rf "$tmp"
else
  echo "==> terraform already installed: $(terraform version | head -1)"
fi

command -v jq >/dev/null 2>&1 || echo "WARN: jq not found — install it (sudo apt-get install -y jq)"
echo "==> bootstrap complete"
