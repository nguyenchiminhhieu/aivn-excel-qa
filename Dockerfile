# AI2026 – Chủ đề 2: Hỏi đáp trên bảng tính Excel (team AI26C0062)
# Linux amd64, CPU only. Source code and all libraries are baked into the image.
FROM --platform=linux/amd64 python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY excelqa/ excelqa/
COPY run.py .

# Grading parameters are appended to the entrypoint, e.g.
#   docker run --rm -e OPENROUTER_API_KEY=... -v /path/test:/data -v /path/out:/output IMAGE /data /output/submission.csv
# The API key is never part of the image: pass it with -e or --env-file at run time.
ENTRYPOINT ["python", "/app/run.py"]
