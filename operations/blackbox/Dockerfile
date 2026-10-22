# v0.28.0 oficial con toolchain y gRPC corregidos; no cambia el código del exporter.
FROM golang:1.26.8-alpine3.23@sha256:a8fa79c5bd40d880b52bd3b6d7669ecdcfd00e85facdd427d279efb5ddd79cb1 AS build
ENV GOTOOLCHAIN=local CGO_ENABLED=0
WORKDIR /src
ADD --checksum=sha256:12b6eb9b307ebc5e55eafe6eb503816756fa0c344854897fb5aeb687544999f9 https://codeload.github.com/prometheus/blackbox_exporter/tar.gz/5a059bee8d8ffa4e75947c5055fb0abeefc582e6 /tmp/source.tar.gz
RUN tar -xzf /tmp/source.tar.gz --strip-components=1 -C /src \
    && go mod edit -require=google.golang.org/grpc@v1.79.3 \
    && go mod tidy \
    && go build -trimpath -ldflags="-s -w -X github.com/prometheus/common/version.Version=0.28.0-chat1" -o /out/blackbox_exporter .

FROM alpine:3.23.6@sha256:85fe1e81d6758c208f3e1eed4338a1997e19d4be002d4dd32d3100c9a8c010a0
RUN apk add --no-cache ca-certificates
COPY --from=build /out/blackbox_exporter /bin/blackbox_exporter
USER 65534:65534
EXPOSE 9115
ENTRYPOINT ["/bin/blackbox_exporter"]
CMD ["--config.file=/etc/blackbox/config.yml"]
