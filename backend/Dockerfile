FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 中文字体：保证生成图上的「AI 效果示意」显式标识能正确渲染
RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY . .

# 先建表并装载种子数据，保证容器首次启动即可演示
RUN python run.py init

EXPOSE 8130
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8130/health')" || exit 1

CMD ["python", "run.py", "serve", "--host", "0.0.0.0", "--port", "8130"]
