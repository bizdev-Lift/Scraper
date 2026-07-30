#!/usr/bin/env bash
set -euo pipefail

STAGE="prod"
BUILD_IMAGE=false
IMAGE_TAG=""
ACTION="deploy"

# Parse flags/arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    --build)
      BUILD_IMAGE=true
      shift
      ;;
    --image-tag)
      IMAGE_TAG="$2"
      shift 2
      ;;
    --destroy)
      ACTION="destroy"
      shift
      ;;
    *)
      STAGE="$1"
      shift
      ;;
  esac
done

cd "$(dirname "$0")"

# Load .env file if it exists
ENV_FILE=".${STAGE}_env"
if [ ! -f "$ENV_FILE" ]; then
  echo "Error: ${ENV_FILE} not found"
  exit 1
fi
set -a
. "./${ENV_FILE}"
set +a
echo "==> Loaded ${ENV_FILE}"

echo "==> Checking prerequisites..."
command -v node >/dev/null 2>&1 || { echo "Error: node is required"; exit 1; }
command -v uv   >/dev/null 2>&1 || { echo "Error: uv is required";   exit 1; }

echo "==> Installing serverless plugins..."
if [ ! -f package.json ]; then
  echo '{"private":true,"devDependencies":{}}' > package.json
fi
npm install --save-dev serverless@3 serverless-python-requirements serverless-step-functions

if [ "$ACTION" = "destroy" ]; then
    echo "==> Destroying stage: $STAGE..."
    npx -y serverless remove --stage "$STAGE"
    echo "==> Done. Stage: $STAGE destroyed successfully."
    exit 0
fi

if [ "$BUILD_IMAGE" = false ] && [ -z "$IMAGE_TAG" ]; then
    echo "Error: Either --build or --image-tag must be provided for deployment."
    exit 1
fi

# Get AWS Account ID
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query "Account" --output text)
ECR_REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.us-east-1.amazonaws.com"
REPO_NAME="domains-data-extractor"
ECR_URI="${ECR_REGISTRY}/${REPO_NAME}"

if [ "$BUILD_IMAGE" = true ]; then
  # Use Git SHA as tag
  IMAGE_TAG=$(git rev-parse --short HEAD 2>/dev/null || echo "latest")
  ECR_IMAGE_URI="${ECR_URI}:${IMAGE_TAG}"

  # Check if image tag already exists in ECR
  EXISTING=$(aws ecr batch-get-image --repository-name domains-data-extractor --image-ids imageTag="${IMAGE_TAG}" --region us-east-1 --query 'images[0]' --output text 2>/dev/null)
  if [ -n "$EXISTING" ] && [ "$EXISTING" != "None" ]; then
    echo "==> Image tag ${IMAGE_TAG} already exists in ECR, skipping build..."
  else
    echo "==> Logging into AWS ECR..."
    aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin "${ECR_REGISTRY}"

    echo "==> Building and pushing Docker image to ECR with tag: ${IMAGE_TAG}..."
    docker build -f Dockerfile --provenance=false -t "${ECR_IMAGE_URI}" . --platform=linux/amd64 --target=llm-extractor
    docker push "${ECR_IMAGE_URI}"
  fi

  export ECR_IMAGE_URI="${ECR_IMAGE_URI}"
else
  echo "==> Using specified image tag: $IMAGE_TAG"
  export ECR_IMAGE_URI="${ECR_URI}:${IMAGE_TAG}"
fi

echo "==> Deploying to stage: $STAGE using Image URI: $ECR_IMAGE_URI"
npx -y serverless deploy --stage "$STAGE"

echo "==> Done. Stage: $STAGE deployed successfully."
