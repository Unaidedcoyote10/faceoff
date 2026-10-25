#!/usr/bin/env sh
# Download the YuNet face detector (OpenCV Zoo, MIT licence) for running Faceoff outside Docker.
# The Docker build downloads it automatically.
set -e
DIR="$(dirname "$0")/../models"; mkdir -p "$DIR"
URL="https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
curl -fL "$URL" -o "$DIR/face_detection_yunet_2023mar.onnx"
SIZE=$(wc -c < "$DIR/face_detection_yunet_2023mar.onnx")
[ "$SIZE" -gt 200000 ] || { echo "download looks wrong ($SIZE bytes; git-lfs pointer?)"; exit 1; }
echo "OK: $DIR/face_detection_yunet_2023mar.onnx ($SIZE bytes)"
