# NVDA Remote Server

A lightweight, single-file relay server for [NVDA Remote](https://nvdaremote.com) -- the remote access add-on for the NVDA screen reader. Run your own private server and keep your remote sessions under your control.

## Why run your own server?

The default public NVDA Remote servers work, but they're shared infrastructure. Running your own server means:

- **Privacy** -- your keystrokes and speech never touch a shared relay
- **Reliability** -- no dependency on community-maintained servers
- **Control** -- configure TLS, message limits, and MOTD on your terms
- **Low latency** -- deploy in the region closest to you and your peers

This is especially valuable for blind system administrators who need reliable, private remote access to machines on their network.

## Quick start

### Deploy on Railway

The fastest way to get a server running in the cloud:

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/template/from-repo?repoUrl=https%3A%2F%2Fgithub.com%2Fa2hsh%2Fnvda-remote)

After deploying, set the `PORT` variable in Railway to `6837` (or configure the NVDA Remote client to use your Railway-assigned port). You'll also need to set `NVDA_REMOTE_CERT_CONTENT` with your base64-encoded TLS certificate (see [TLS certificates](#tls-certificates) below).

### Run with Docker

```bash
docker build -t nvda-remote .
docker run -d -p 6837:6837 --name nvda-remote nvda-remote
```

The server will auto-generate a self-signed certificate on first run if none is found.

### Run directly

Requires Python 3.8+ and OpenSSL.

```bash
python server.py
```

That's it. No dependencies beyond the Python standard library.

## Configuration

All options can be set via command-line arguments or environment variables.

| Argument | Environment Variable | Default | Description |
|---|---|---|---|
| `--port` | `NVDA_REMOTE_PORT` | `6837` | Port to listen on |
| `--certfile` | `NVDA_REMOTE_CERTFILE` | `server.pem` | Path to TLS certificate file |
| `--keyfile` | `NVDA_REMOTE_KEYFILE` | `server.pem` | Path to TLS key file |
| `--motd` | `NVDA_REMOTE_MOTD` | *(none)* | Message of the day shown on join |
| `--motd-force` | `NVDA_REMOTE_MOTD_FORCE` | `false` | Force-display the MOTD |
| `--max-msg-size` | `NVDA_REMOTE_MAX_MSG_SIZE` | `1048576` | Max message size in bytes (0 = unlimited) |
| `--debug` | `NVDA_REMOTE_DEBUG` | `false` | Enable debug logging |
| `--tracebacks` | `NVDA_REMOTE_TRACEBACKS` | `false` | Log full tracebacks on errors |
| `--generate-cert` | -- | -- | Generate a self-signed certificate and exit |

### TLS certificates

The server requires TLS. On first run, it will auto-generate a self-signed certificate (`server.pem`) using OpenSSL. For production, you have several options:

**Provide your own certificate file:**
```bash
python server.py --certfile /path/to/cert.pem --keyfile /path/to/key.pem
```

**Generate a fresh self-signed certificate:**
```bash
python server.py --generate-cert
```

#### Certificate via environment variable

On platforms like Railway where you can't upload files and the filesystem is ephemeral, you can inject a certificate entirely through an environment variable. The server will decode it and write the PEM file at startup -- no file uploads needed.

**Step 1: Generate a certificate locally**

```bash
python server.py --generate-cert
```

This creates `server.pem` in the current directory (a combined certificate + private key file).

**Step 2: Base64-encode the certificate**

```bash
# Linux / macOS
base64 -w0 server.pem

# Windows (PowerShell)
[Convert]::ToBase64String([IO.File]::ReadAllBytes("server.pem"))
```

Copy the output -- this is your certificate as a single string with no line breaks.

**Step 3: Set the environment variable**

On Railway, go to your project's **Variables** tab and add:

| Variable | Value |
|---|---|
| `NVDA_REMOTE_CERT_CONTENT` | *(paste the base64 string from step 2)* |

You can also set it with the Railway CLI:

```bash
railway variables set NVDA_REMOTE_CERT_CONTENT="$(base64 -w0 server.pem)"
```

Or in Docker:

```bash
docker run -d -p 6837:6837 \
  -e NVDA_REMOTE_CERT_CONTENT="$(base64 -w0 server.pem)" \
  nvda-remote
```

When the server starts, it decodes `NVDA_REMOTE_CERT_CONTENT` and writes it to the certificate path (default `server.pem`). This happens before the TLS listener starts, so the server is ready immediately.

## Connecting with NVDA Remote

1. In NVDA, go to **Tools > Remote > Connect**
2. Select **Control another machine** or **Allow this machine to be controlled**
3. Enter your server address: `yourserver.example.com:6837`
4. Enter or generate a key
5. Click **Connect**

Both users must connect to the same server with the same key.

## Architecture

This is a single-file asyncio server (~300 lines of Python) that acts as a message relay:

- Clients connect over TLS and join a **channel** identified by a shared key
- Messages from one client are **broadcast** to all other clients in the same channel
- Supports both NVDA Remote protocol v1 and v2 clients simultaneously
- Uses **TCP keepalive** for dead connection detection (no application-level ping overhead)
- Per-client write queues with backpressure (queue full = connection closed)
- Channels are created on first join and destroyed when the last client leaves

## License

MIT -- see [LICENSE](LICENSE) for details.
