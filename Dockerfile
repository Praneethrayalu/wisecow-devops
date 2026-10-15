# Wisecow - cow wisdom web server
# Base: Debian 12 slim, pinned by digest so every build starts from the same bytes.
# (Refresh the digest deliberately, e.g. monthly, to pick up security fixes.)
FROM debian:bookworm-slim@sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587

LABEL org.opencontainers.image.title="wisecow" \
      org.opencontainers.image.description="Cow wisdom web server (fortune | cowsay over HTTP)" \
      org.opencontainers.image.source="https://github.com/Praneethrayalu/wisecow-devops"

# Runtime dependencies only: fortune-mod + fortunes-min (the wisdom), cowsay (the cow, a perl script)
# and socat (the TCP listener that forks one handler per connection). Apt lists are removed in the
# same layer so they never end up in the image.
RUN apt-get update \
 && apt-get install -y --no-install-recommends fortune-mod fortunes-min cowsay socat \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 10001 --user-group --no-create-home --shell /usr/sbin/nologin wisecow

# fortune and cowsay live in /usr/games on Debian
ENV PATH="/usr/games:${PATH}" \
    SRVPORT=4499

WORKDIR /app
# Owned by root and read-only for the app user: the running process cannot modify its own code.
COPY wisecow.sh /app/wisecow.sh
RUN chmod 0555 /app/wisecow.sh

USER 10001:10001
EXPOSE 4499

# Real health check: send an HTTP request and require a "200" status line (exercises fortune | cowsay too).
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD printf 'GET / HTTP/1.1\r\nHost: localhost\r\n\r\n' | socat -T 4 - TCP:127.0.0.1:4499 | head -n 1 | grep -q '^HTTP/1.1 200 ' || exit 1

STOPSIGNAL SIGTERM
CMD ["/app/wisecow.sh"]
