#!/usr/bin/env bash
set -euo pipefail

# ------------------------------------------------------------------------------
# Build and push a Docker image (single-arch) with tags.
# By default, it tags as both :<version> and :latest.
# - VERSION is required (e.g., v0.1.2 or sha-abc123)
# - USERNAME is optional; defaults to $DOCKERHUB_USERNAME or $(whoami)
# - IMAGE defaults to "mousesearch" but can be overridden with -i/--image
#
# Usage:
#   ./buildImage.sh -v <version> [-u <dockerhub-username>] [-i <image-name>] [--no-cache] [--no-latest]
#
# Examples:
#   ./buildImage.sh -v v0.1.2
#   ./buildImage.sh -v sha-$(git rev-parse --short HEAD) -u sevenlayercookie
#   ./buildImage.sh -v v0.1.3 --no-latest
# ------------------------------------------------------------------------------

USERNAME_DEFAULT="${DOCKERHUB_USERNAME:-$(whoami)}"
IMAGE_DEFAULT="mousesearch"
NO_CACHE=""
PUSH_LATEST="true"

usage() {
  cat <<EOF
Usage: $0 -v <version> [-u <dockerhub-username>] [-i <image-name>] [--no-cache] [--no-latest]

Required:
  -v, --version   Image version tag to publish (e.g., v0.1.2)

Optional:
  -u, --username  Docker Hub username (default: \$DOCKERHUB_USERNAME or $(whoami))
  -i, --image     Image name/repository (default: ${IMAGE_DEFAULT})
      --no-cache  Build without using cache
      --no-latest Do not tag or push the 'latest' tag

Environment:
  DOCKERHUB_USERNAME  Used as default for --username if set

Examples:
  $0 -v v0.1.2
  $0 -v sha-\$(git rev-parse --short HEAD) -u sevenlayercookie
  $0 -v v0.1.3 --no-latest
EOF
}

# --- Parse args ---
USERNAME="${USERNAME_DEFAULT}"
IMAGE="${IMAGE_DEFAULT}"
VERSION=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    -u|--username) USERNAME="$2"; shift 2 ;;
    -i|--image)    IMAGE="$2"; shift 2 ;;
    -v|--version)  VERSION="$2"; shift 2 ;;
    --no-cache)    NO_CACHE="--no-cache"; shift ;;
    --no-latest)   PUSH_LATEST="false"; shift ;;
    -h|--help)     usage; exit 0 ;;
    *) echo "Unknown arg: $1"; usage; exit 1 ;;
  esac
done

if [[ -z "${VERSION}" ]]; then
  echo "ERROR: --version is required."
  usage
  exit 1
fi

# --- Export env vars (as requested) ---
export YOUR_USER="${USERNAME}"
export VERSION="${VERSION}"
export IMAGE="${IMAGE}"

echo ">> Using:"
echo "   USERNAME   : ${YOUR_USER}"
echo "   IMAGE      : ${IMAGE}"
echo "   VERSION    : ${VERSION}"
echo "   NO_CACHE   : ${NO_CACHE:-<none>}"
echo "   PUSH_LATEST: ${PUSH_LATEST}"

# --- Sanity checks ---
if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: Docker is not installed or not in PATH."
  exit 1
fi

# Check Docker daemon (best-effort)
if ! docker info >/dev/null 2>&1; then
  echo "ERROR: Cannot talk to the Docker daemon. Is it running? Do you need sudo?"
  exit 1
fi

# --- Build image with tags ---
FULL_VERSION_TAG="${YOUR_USER}/${IMAGE}:${VERSION}"
FULL_LATEST_TAG="${YOUR_USER}/${IMAGE}:latest"

# Prepare the tag arguments for the build command
DOCKER_BUILD_TAG_ARGS=()
DOCKER_BUILD_TAG_ARGS+=(-t "${FULL_VERSION_TAG}")

echo ">> Building:"
echo "   ${FULL_VERSION_TAG}"

if [[ "${PUSH_LATEST}" == "true" ]]; then
  DOCKER_BUILD_TAG_ARGS+=(-t "${FULL_LATEST_TAG}")
  echo "   ${FULL_LATEST_TAG}"
fi

docker build ${NO_CACHE} "${DOCKER_BUILD_TAG_ARGS[@]}" ..

# --- Verify tags exist locally ---
echo ">> Verifying local images..."
if ! docker image inspect "${FULL_VERSION_TAG}" >/dev/null 2>&1; then
  echo "ERROR: Build completed but ${FULL_VERSION_TAG} not found locally."
  exit 1
fi

if [[ "${PUSH_LATEST}" == "true" ]]; then
  if ! docker image inspect "${FULL_LATEST_TAG}" >/dev/null 2>&1; then
    echo "ERROR: Build completed but ${FULL_LATEST_TAG} not found locally."
    exit 1
  fi
fi

echo ">> Local tags verified:"
docker images --format 'table {{.Repository}}\t{{.Tag}}\t{{.ID}}\t{{.Size}}' | grep -E "^${YOUR_USER}/${IMAGE}\s"

# --- Push tags ---
echo ">> Pushing ${FULL_VERSION_TAG}"
docker push "${FULL_VERSION_TAG}"

if [[ "${PUSH_LATEST}" == "true" ]]; then
  echo ">> Pushing ${FULL_LATEST_TAG}"
  docker push "${FULL_LATEST_TAG}"
fi

echo ">> Done!"
echo "   Pull with:"
echo "     docker pull ${FULL_VERSION_TAG}"

if [[ "${PUSH_LATEST}" == "true" ]]; then
  echo "     docker pull ${FULL_LATEST_TAG}"
fi
