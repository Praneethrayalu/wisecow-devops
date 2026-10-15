# Wisecow - cow wisdom web server
# Small, non-root image. Debian slim keeps fortune-mod/cowsay available from apt.
FROM debian:bookworm-slim

LABEL org.opencontainers.image.title="wisecow" \
      org.opencontainers.image.description="Cow wisdom web server (fortune | cowsay over HTTP)" \
      org.opencontainers.image.source="https://github.com/Praneethrayalu/wisecow-devops"

# fortune-mod + cowsay are the app's only runtime dependencies; netcat-openbsd provides `nc -lN`.
RUN apt-get update \
 && apt-get install -y --no-install-recommends fortune-mod fortunes-min cowsay netcat-openbsd \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --system --uid 10001 --create-home --shell /usr/sbin/nologin wisecow

# fortune and cowsay are installed under /usr/games on Debian
ENV PATH="/usr/games:${PATH}" \
    SRVPORT=4499

WORKDIR /app
COPY --chown=wisecow:wisecow wisecow.sh /app/wisecow.sh
RUN chmod +x /app/wisecow.sh && chown -R wisecow:wisecow /app

USER wisecow
EXPOSE 4499

# Liveness for plain `docker run`; Kubernetes uses its own probes (see k8s/deployment.yaml)
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD sh -c 'printf "GET / HTTP/1.0\r\n\r\n" | nc -w 2 127.0.0.1 4499 | grep -q "200"' || exit 1

CMD ["/app/wisecow.sh"]
