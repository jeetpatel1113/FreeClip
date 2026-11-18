# syntax=docker/dockerfile:1
# CUDA 12 + cuDNN 9 runtime: faster-whisper (CTranslate2) runs on the GPU when one is attached,
# and FFmpeg uses NVENC if the driver exposes it. Without a GPU everything falls back to CPU.
FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    NVIDIA_DRIVER_CAPABILITIES=compute,video,utility \
    HF_HOME=/models

RUN apt-get update && apt-get install -y --no-install-recommends \
      software-properties-common ca-certificates curl xz-utils fontconfig \
      fonts-dejavu-core fonts-noto-color-emoji \
      libgl1 libglib2.0-0 \
 && add-apt-repository -y ppa:deadsnakes/ppa \
 && apt-get update && apt-get install -y --no-install-recommends python3.11 python3.11-venv python3.11-dev \
 && rm -rf /var/lib/apt/lists/*

# Static FFmpeg build with libass + NVENC support.
RUN curl -fsSL https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n7.1-latest-linux64-gpl-7.1.tar.xz \
      | tar -xJ -C /opt \
 && ln -s /opt/ffmpeg-n7.1-latest-linux64-gpl-7.1/bin/ffmpeg /usr/local/bin/ffmpeg \
 && ln -s /opt/ffmpeg-n7.1-latest-linux64-gpl-7.1/bin/ffprobe /usr/local/bin/ffprobe

# Caption fonts (SIL Open Font License): Montserrat (Hormozi/Neon) and Inter (Minimalist).
RUN mkdir -p /usr/share/fonts/truetype/freeclip && cd /usr/share/fonts/truetype/freeclip \
 && for w in Black ExtraBold Bold; do \
      curl -fsSL -o "Montserrat-$w.ttf" "https://github.com/JulietaUla/Montserrat/raw/master/fonts/ttf/Montserrat-$w.ttf"; \
    done \
 && curl -fsSL -o inter.zip https://github.com/rsms/inter/releases/download/v4.1/Inter-4.1.zip \
 && python3.11 -c "import zipfile; z=zipfile.ZipFile('inter.zip'); [z.extract(n, '.') for n in z.namelist() if n.startswith('extras/ttf/Inter-') and n.endswith(('-Regular.ttf','-Medium.ttf','-SemiBold.ttf'))]" \
 && find . -name 'Inter-*.ttf' -exec mv {} . \; && rm -rf extras inter.zip \
 && fc-cache -f

RUN python3.11 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY workers/video-pipeline/requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY workers/video-pipeline/ .

RUN python -m compileall -q pipeline worker.py
VOLUME ["/models", "/tmp/freeclip"]
CMD ["python", "worker.py"]
