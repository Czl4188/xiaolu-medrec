FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 先装依赖，利用镜像层缓存
COPY requirements.txt requirements-prod.txt ./
RUN pip install -r requirements-prod.txt

COPY . .

# 非 root 运行
RUN useradd -m -u 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# 容器里默认单进程；多实例交给编排层（compose/k8s）横向扩
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
