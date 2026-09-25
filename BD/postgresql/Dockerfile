# La imagen oficial incluye gosu 1.19 compilado con Go vulnerable.
# Reconstruir el mismo código con un toolchain corregido, sin excepciones al escáner.
FROM golang:1.26.8-alpine3.23@sha256:a8fa79c5bd40d880b52bd3b6d7669ecdcfd00e85facdd427d279efb5ddd79cb1 AS gosu
ENV GOTOOLCHAIN=local CGO_ENABLED=0
WORKDIR /src
ADD --checksum=sha256:33d7537d588ea49458b9509bcf4554bdf5ceacc66da71e5caa1058ea3b689c3b https://codeload.github.com/tianon/gosu/tar.gz/6456aaa0f3c854d199d0f037f068eb97515b7513 /tmp/source.tar.gz
RUN tar -xzf /tmp/source.tar.gz --strip-components=1 -C /src \
    && go build -mod=readonly -trimpath -ldflags="-d -w" -o /out/gosu .

FROM postgres:17.11-alpine3.23@sha256:9ea644da56f1a82283b173fa38a316636fe7c538991a8ae41998cd3765434991
COPY --from=gosu /out/gosu /usr/local/bin/gosu
COPY --from=gosu /src/LICENSE /usr/local/share/licenses/gosu/LICENSE
RUN gosu --version \
    && test "$(gosu postgres id -u)" = 70 \
    && test "$(gosu postgres id -g)" = 70 \
    && gosu nobody true \
    && gosu postgres postgres --version
