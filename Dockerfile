# Docker 部署用。本地直接 python app.py 即可，不需要 Docker。

FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 数据目录：挂载卷到这个路径，容器重启数据才不会丢
ENV DATA_DIR=/data
RUN mkdir -p /data

EXPOSE 8000

CMD ["python", "app.py"]
