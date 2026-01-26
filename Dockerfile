# Use a lightweight Python image
FROM python:3.11-alpine

# Install OpenSSL (Required for --generate-cert)
RUN apk add --no-cache openssl

WORKDIR /app

# Copy the server script
COPY server.py .

# NOTE: server.pem is NOT copied here if you use the Environment Variable method.
# If you ignore it in git, uncomment the line below ONLY for local testing.
# COPY server.pem .

EXPOSE 6837

CMD ["python", "server_async.py"]
