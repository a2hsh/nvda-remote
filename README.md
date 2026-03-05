# NVDA Remote Server

A lightweight, single-file relay server for [NVDA Remote Access](https://www.nvaccess.org/post/nvda-2025-1/) -- the built-in remote control feature in NVDA 2025.1 and later. Run your own private server and keep your remote sessions under your control.

> Remote Access was originally the [NVDA Remote](https://nvdaremote.com) add-on, developed by Tyler Spivey and Christopher Toth. Starting with NVDA 2025.1, it ships as a built-in feature -- no add-on required. This server is compatible with both the built-in feature and the legacy add-on.

## Why run your own server?

The default public NVDA Remote servers work, but they're shared infrastructure. Running your own server means:

- **Privacy** -- your keystrokes and speech never touch a shared relay
- **Reliability** -- no dependency on community-maintained servers
- **Control** -- configure TLS, message limits, and MOTD on your terms
- **Low latency** -- deploy in the region closest to you and your peers

This is especially valuable for blind system administrators who need reliable, private remote access to machines on their network.

## Quick start

### Deploy on Railway

The fastest way to get a server running in the cloud. The full process takes about 5 minutes.

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/template/from-repo?repoUrl=https%3A%2F%2Fgithub.com%2Fa2hsh%2Fnvda-remote)

#### Phase 1: Template initialization

**Step 1: Click the Deploy button**

Click the "Deploy on Railway" button above. This opens Railway's template creation page in a new tab.

**Step 2: Authenticate and clone**

Railway will ask you to connect your GitHub account if you aren't logged in already. It will prompt you to name the new repository it creates for you.

- You can leave the default name or change it to something like `nvda-rockstorm-server`.
- Check the box to make the repository **Private**. Even though we use environment variables for the certificate, privacy is a good default.

**Step 3: Configure environment variables**

Because of the `railway.json` file, Railway will present you with a form asking for your environment variables before it attempts to deploy.

| Variable | What to enter |
|---|---|
| `NVDA_REMOTE_MOTD` | A message shown to users on connect (optional). |
| `NVDA_REMOTE_CERT_CONTENT` | Leave blank to auto-generate a self-signed certificate on startup, or paste a base64-encoded PEM string (see [Certificate via environment variable](#certificate-via-environment-variable) below). |
| `NVDA_REMOTE_DEBUG` | `false` (unless you want raw JSON in the logs). |
| `NVDA_REMOTE_PORT` | `6837` |

**Step 4: Hit Deploy**

Click the **Deploy** button at the bottom of the form. Railway will create the project, pull the Docker image, and start building.

#### Phase 2: TCP network routing

This is the one step the template cannot do for you. NVDA Remote uses raw TCP over TLS -- not HTTP -- so you need to enable Railway's TCP proxy.

**Step 1: Open the service settings**

Once the project dashboard loads, you'll see a block representing your deployed service. Click on it.

**Step 2: Navigate to Networking**

In the panel that opens, select the **Settings** tab and scroll down to the **Networking** section.

**Step 3: Enable the TCP proxy**

Under **Public Networking**, find the **TCP Proxy** option and activate it.

**Step 4: Map the port**

Railway will ask for the **Service Port** (also called Target Port). Enter exactly `6837`. This tells Railway to forward public traffic directly to port 6837 inside your container.

**Step 5: Capture your server address**

After applying the port, Railway will generate your public connection address. It will look something like:

```
proxy.rlwy.net:12345
```

Copy this address. The part before the colon is your **host**, and the number after the colon is your **public port**. This is what you'll enter in the NVDA Remote client to connect.

#### Custom domains on Railway

You can point a custom domain at your Railway TCP proxy using a DNS CNAME record, but there's an important limitation to understand.

**HTTP vs. TCP routing:** When Railway hosts a regular website, it routes traffic by domain name, so custom domains work seamlessly on port 443. But NVDA Remote uses raw TCP. Railway handles this by putting you on a shared server with a random dedicated port (like `51106`). DNS CNAME records only map names to names -- they can't remap ports.

**Setting up the CNAME:**

If Railway gave you `shortline.proxy.rlwy.net:51106`, create a DNS record like this:

| Type | Name | Target |
|---|---|---|
| CNAME | `remote` | `shortline.proxy.rlwy.net` |

Note: drop the port from the target -- DNS breaks if you include it.

Your users would then connect with:
- **Host:** `remote.yourdomain.com`
- **Port:** `51106`

This gives you a branded hostname, but users still need to type the port number.

**Want a completely clean address?** If your goal is `remote.yourdomain.com` with no port (just the default `6837`), Railway's TCP proxy can't do that because it doesn't give you a dedicated IP. For a true port-free custom domain, run this on a VPS (DigitalOcean, Linode, Hetzner -- as cheap as $5/month) where you get your own IPv4 address and full control over which port listens.

### Run with Docker

Prebuilt multi-arch images (amd64 + arm64) are published to GitHub Container Registry on every push to `main`.

**Using Docker Compose (recommended):**

```bash
curl -O https://raw.githubusercontent.com/a2hsh/nvda-remote/main/docker-compose.yml
docker compose up -d
```

Edit `docker-compose.yml` to set your environment variables (certificate, MOTD, etc.) before starting.

**Using Docker directly:**

```bash
docker run -d -p 6837:6837 --name nvda-remote ghcr.io/a2hsh/nvda-remote:latest
```

The server will auto-generate a self-signed certificate on first run if none is found.

**Building locally:**

```bash
docker build -t nvda-remote .
docker run -d -p 6837:6837 --name nvda-remote nvda-remote
```

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

## Connecting with NVDA

As of NVDA 2025.1, Remote Access is built in. No add-on needed.

1. In NVDA, go to **Tools > Remote > Connect**
2. Select **Control another machine** or **Allow this machine to be controlled**
3. Enter your server address -- for example `proxy.rlwy.net:12345` (Railway TCP proxy), `remote.yourdomain.com:51106` (custom domain on Railway), or `yourserver.example.com:6837` (self-hosted)
4. Enter or generate a key
5. Click **Connect**

Both users must connect to the same server with the same key.

> Still on an older version of NVDA? The legacy [NVDA Remote add-on](https://nvdaremote.com) works with this server too.

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
