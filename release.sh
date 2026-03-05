#!/usr/bin/env bash
set -euo pipefail

# Read version from VERSION file (single source of truth)
VERSION=$(cat VERSION | tr -d '[:space:]')

if [ -z "$VERSION" ]; then
    echo "Error: could not read version from VERSION file"
    exit 1
fi

TAG="v${VERSION}"

echo "Version from VERSION file: ${VERSION}"
echo "Git tag: ${TAG}"

# Check for uncommitted changes
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "Error: you have uncommitted changes. Commit first."
    exit 1
fi

# Check if tag already exists
if git rev-parse "$TAG" >/dev/null 2>&1; then
    echo "Error: tag ${TAG} already exists"
    exit 1
fi

echo ""
echo "This will:"
echo "  1. Push current branch to origin"
echo "  2. Create and push tag ${TAG}"
echo "  3. Trigger Docker image build for ${TAG} + latest"
echo ""
read -rp "Continue? [y/N] " confirm
if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 0
fi

git push
git tag "$TAG"
git push origin "$TAG"

echo ""
echo "Done! Tag ${TAG} pushed."
echo "Docker build: https://github.com/$(git remote get-url origin | sed 's/.*github.com[:/]\(.*\)\.git/\1/')/actions"
