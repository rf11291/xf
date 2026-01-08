FROM golang:1.22 AS build

WORKDIR /src
COPY go.mod ./
COPY cmd ./cmd
COPY internal ./internal
RUN CGO_ENABLED=0 GOOS=linux go build -o /out/xf-panel ./cmd/server

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=build /out/xf-panel ./xf-panel
COPY README.md ./
COPY .env.example ./
ENV APP_ADDR=:8080
CMD ["./xf-panel"]
