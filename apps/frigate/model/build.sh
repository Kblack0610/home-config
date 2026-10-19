#!/usr/bin/env bash
# Export the YOLOv9 ONNX model Frigate's -tensorrt image runs on the GPU, and
# publish it to the Forgejo generic package registry the init container pulls from.
#
# Frigate 0.18 ships no GPU model; this is the export recipe from its docs
# (docs/data/object_detectors_models.yaml, key yolov9, at tag v0.18.0), changed
# to run on the classic builder: the docs' `--output` and `ADD <git repo>` both
# need BuildKit, which this workstation's docker does not have. The yolov9 repo
# is pinned so a rebuild produces the same model.
#
# Usage: apps/frigate/model/build.sh [MODEL_SIZE] [IMG_SIZE]
#   then: FORGEJO_TOKEN=... apps/frigate/model/build.sh --publish <file>
set -euo pipefail

REGISTRY="https://git.kblab.me/api/packages/kblack0610/generic/frigate-model"

if [ "${1:-}" = "--publish" ]; then
  f="$2"; ver="$(basename "$f" .onnx)"
  curl -fsS --user "kblack0610:${FORGEJO_TOKEN:?set FORGEJO_TOKEN}" \
    --upload-file "$f" "$REGISTRY/$ver/$(basename "$f")"
  echo "published $REGISTRY/$ver/$(basename "$f")"
  sha256sum "$f"
  exit 0
fi

MODEL_SIZE="${1:-s}"
IMG_SIZE="${2:-320}"
YOLOV9_REF="5b1ea9a8b3f0ffe4fe0e203ec6232d788bb3fcff"
out="$(mktemp -d)"
tag="frigate-model-build:yolov9-${MODEL_SIZE}-${IMG_SIZE}"

docker build -t "$tag" --build-arg MODEL_SIZE="$MODEL_SIZE" --build-arg IMG_SIZE="$IMG_SIZE" --build-arg YOLOV9_REF="$YOLOV9_REF" - <<'DOCKERFILE'
FROM python:3.11 AS build
RUN apt-get update && apt-get install --no-install-recommends -y cmake libgl1 git && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.10.4 /uv /bin/
ARG YOLOV9_REF
RUN git clone https://github.com/WongKinYiu/yolov9.git /yolov9 && git -C /yolov9 checkout "$YOLOV9_REF"
WORKDIR /yolov9
RUN uv pip install --system -r requirements.txt
RUN uv pip install --system onnx==1.18.0 onnxruntime onnx-simplifier==0.4.* onnxscript
ARG MODEL_SIZE
ARG IMG_SIZE
ADD https://github.com/WongKinYiu/yolov9/releases/download/v0.1/yolov9-${MODEL_SIZE}-converted.pt yolov9-${MODEL_SIZE}.pt
RUN sed -i "s/ckpt = torch.load(attempt_download(w), map_location='cpu')/ckpt = torch.load(attempt_download(w), map_location='cpu', weights_only=False)/g" models/experimental.py
RUN python3 export.py --weights ./yolov9-${MODEL_SIZE}.pt --imgsz ${IMG_SIZE} --simplify --include onnx
FROM scratch
ARG MODEL_SIZE
ARG IMG_SIZE
COPY --from=build /yolov9/yolov9-${MODEL_SIZE}.onnx /yolov9-${MODEL_SIZE}-${IMG_SIZE}.onnx
DOCKERFILE

cid="$(docker create "$tag" none)"
docker cp "$cid:/yolov9-${MODEL_SIZE}-${IMG_SIZE}.onnx" "$out/"
docker rm "$cid" >/dev/null
ls -la "$out"/*.onnx
sha256sum "$out"/*.onnx
