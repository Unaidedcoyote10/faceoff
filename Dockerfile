# Faceoff — multi-arch (linux/arm64 for Raspberry Pi, linux/amd64)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data EXPORT_DIR=/export YUNET_MODEL=/app/models/face_detection_yunet_2023mar.onnx

WORKDIR /app
# Tesseract (English only) for keyword redaction of scans and photos. No neural OCR model.
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -c "import pytesseract,pypdfium2; print('docs', pytesseract.get_tesseract_version())"

# Bundle the YuNet face detector (OpenCV Zoo, MIT licence) at build time
ADD https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx /app/models/face_detection_yunet_2023mar.onnx
RUN python -c "import os,cv2; p=os.environ['YUNET_MODEL']; assert os.path.getsize(p)>200000, 'model download failed (LFS pointer?)'; cv2.FaceDetectorYN.create(p,'',(320,320)); print('YuNet OK', cv2.__version__)"

COPY app /app/app
COPY tests /app/tests
COPY extension /app/extension
RUN useradd -u 1000 -m faceoff && mkdir -p /data /export && chown -R faceoff /data /export /app
USER faceoff

EXPOSE 8088
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8088/api/health',timeout=4)" || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8088", "--workers", "1", "--proxy-headers"]
