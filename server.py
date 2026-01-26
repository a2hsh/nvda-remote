import asyncio
import json
import ssl
import sys
import logging
import argparse
import random
import string
import os
import traceback
import itertools
import socket
import subprocess
import base64
from collections import deque

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("NVDARemote")

# --- 1. Linux Resource Limit Fix ---
if sys.platform != 'win32':
    try:
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        resource.setrlimit(resource.RLIMIT_NOFILE, (hard, hard))
        logger.info(f"System resource limit increased: {soft} -> {hard}")
    except Exception as e:
        logger.warning(f"Could not increase file descriptor limit: {e}")

def str_to_bool(value):
    if isinstance(value, bool): return value
    return str(value).lower() in {'true', '1', 'yes', 'y', 't'}

class AsyncServer:
    def __init__(self, args):
        self.channels = {} 
        self.clients = set()
        self.args = args
        self.client_id_counter = itertools.count(1)

    def generate_unique_key(self):
        for _ in range(100):
            key = "".join(random.choices(string.digits, k=6))
            if key not in self.channels:
                return key
        return "".join(random.choices(string.digits, k=8))

    async def handle_client(self, reader, writer):
        # --- LOGGING FEATURE 1: Connection & SSL Info ---
        addr = writer.get_extra_info('peername')
        ssl_obj = writer.get_extra_info('ssl_object')
        cipher_info = f" ({ssl_obj.version()} / {ssl_obj.cipher()[0]})" if ssl_obj else ""
        
        logger.info(f"Accepted connection from {addr}{cipher_info}")

        # Optimization: Disable Nagle's algorithm
        sock = writer.get_extra_info('socket')
        if sock:
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except (OSError, AttributeError):
                pass

        client = Client(reader, writer, self)
        self.clients.add(client)
        
        # --- LOGGING FEATURE 2: Global Client Count ---
        logger.info(f"Total Clients Connected: {len(self.clients)}")
        
        client.start_tasks()

        try:
            while True:
                try:
                    line = await asyncio.wait_for(
                        reader.readline(), 
                        timeout=self.args.timeout
                    )
                except asyncio.TimeoutError:
                    logger.info(f"Client {client.id} from {addr} timed out (idle).")
                    break

                if not line: 
                    logger.info(f"Client {client.id} from {addr} closed connection.")
                    break 
                
                if self.args.max_msg_size > 0 and len(line) > self.args.max_msg_size:
                    logger.warning(f"Client {client.id} exceeded max message size.")
                    break

                await client.process_message(line)

        except ConnectionResetError:
            logger.info(f"Client {client.id} connection reset by peer.")
        except Exception as e:
            if self.args.tracebacks:
                logger.error(f"Error client {client.id}:\n{traceback.format_exc()}")
            else:
                logger.error(f"Error client {client.id}: {e}")
        finally:
            await client.cleanup()
            self.clients.discard(client)
            # Log disconnect count
            logger.info(f"Client {client.id} disconnected. Total Clients: {len(self.clients)}")
            
            try:
                writer.close()
                await writer.wait_closed()
            except:
                pass

class Client:
    def __init__(self, reader, writer, server):
        self.reader = reader
        self.writer = writer
        self.server = server
        self.channel_id = None
        self.id = next(server.client_id_counter)
        self.protocol_version = 1 
        self.out_queue = asyncio.Queue(maxsize=200) 
        self.writer_task = None
        self.ping_task = None

    def start_tasks(self):
        self.writer_task = asyncio.create_task(self.write_loop())
        self.ping_task = asyncio.create_task(self.keep_alive_loop())

    async def write_loop(self):
        try:
            while True:
                data = await self.out_queue.get()
                self.writer.write(data)
                await self.writer.drain()
                self.out_queue.task_done()
        except Exception:
            pass 

    async def enqueue(self, data):
        try:
            self.out_queue.put_nowait(data)
        except asyncio.QueueFull:
            logger.warning(f"Client {self.id} send queue full. Disconnecting.")
            self.writer.close()

    async def keep_alive_loop(self):
        while True:
            await asyncio.sleep(self.server.args.ping_interval)
            try:
                await self.enqueue((json.dumps({"type": "ping"}) + "\n").encode('utf-8'))
            except:
                break

    async def process_message(self, line):
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return

        # Debug logs show raw JSON (Only if NVDA_REMOTE_DEBUG=True)
        logger.debug(f"RECV {self.id}: {line.strip()}")

        msg_type = data.get('type')
        if not msg_type: return

        if msg_type == 'join':
            await self.do_join(data)
        elif msg_type == 'protocol_version':
            self.protocol_version = data.get('version', 1)
        elif msg_type == 'generate_key':
            await self.do_generate_key()
        elif msg_type == 'ping':
            pass
        elif self.channel_id:
            await self.broadcast(data)
        else:
            await self.send_error("not_joined")

    async def do_generate_key(self):
        new_key = self.server.generate_unique_key()
        await self.send_json({"type": "generate_key", "key": new_key})

    async def do_join(self, data):
        new_channel = data.get('channel')
        if not new_channel:
            await self.send_error("invalid_parameters")
            return

        if self.channel_id and self.channel_id in self.server.channels:
             self.server.channels[self.channel_id].discard(self)
             if not self.server.channels[self.channel_id]:
                 # --- LOGGING FEATURE 3: Channel Lifecycle ---
                 logger.info(f"Channel '{self.channel_id}' destroyed (empty).")
                 del self.server.channels[self.channel_id]

        self.channel_id = new_channel
        if self.channel_id not in self.server.channels:
            # --- LOGGING FEATURE 3: Channel Lifecycle ---
            logger.info(f"Channel '{self.channel_id}' created by Client {self.id}")
            self.server.channels[self.channel_id] = set()
        self.server.channels[self.channel_id].add(self)

        peers = [c for c in self.server.channels[self.channel_id] if c != self]
        
        response = {
            "type": "channel_joined",
            "channel": new_channel,
            "user_ids": [c.id for c in peers],
            "clients": [{"id": c.id} for c in peers]
        }
        await self.send_json(response)
        
        if self.server.args.motd:
            await self.send_json({
                "type": "motd", 
                "motd": self.server.args.motd,
                "force_display": self.server.args.motd_force
            })

        await self.broadcast({"type": "client_joined", "client": {"id": self.id}}, include_self=False)

    async def broadcast(self, data, include_self=False):
        if not self.channel_id or self.channel_id not in self.server.channels:
            return
        
        if "origin" not in data:
            data["origin"] = self.id

        msg_v2 = (json.dumps(data) + "\n").encode('utf-8')
        
        v1_data = data.copy()
        for field in ["origin", "client", "clients"]:
            v1_data.pop(field, None)
        msg_v1 = (json.dumps(v1_data) + "\n").encode('utf-8')

        for client in self.server.channels[self.channel_id]:
            if client == self and not include_self:
                continue
            
            if client.protocol_version <= 1:
                await client.enqueue(msg_v1)
            else:
                await client.enqueue(msg_v2)

    async def send_json(self, data):
        await self.enqueue((json.dumps(data) + "\n").encode('utf-8'))

    async def send_error(self, error_msg):
        await self.send_json({"type": "error", "error": error_msg})

    async def cleanup(self):
        if self.writer_task: self.writer_task.cancel()
        if self.ping_task: self.ping_task.cancel()

        if self.channel_id and self.channel_id in self.server.channels:
            self.server.channels[self.channel_id].discard(self)
            await self.broadcast({"type": "client_left", "user_id": self.id}, include_self=False)
            if not self.server.channels[self.channel_id]:
                # --- LOGGING FEATURE 3: Channel Lifecycle ---
                logger.info(f"Channel '{self.channel_id}' destroyed (empty).")
                del self.server.channels[self.channel_id]

def generate_certificate():
    """Generates a self-signed certificate using OpenSSL."""
    logger.info("Generating new self-signed certificate (server.pem)...")
    cmd = [
        "openssl", "req", "-new", "-newkey", "rsa:4096", "-days", "3650",
        "-nodes", "-x509",
        "-subj", "/C=US/ST=Denial/L=Springfield/O=Dis/CN=www.example.com",
        "-keyout", "server.pem", "-out", "server.pem"
    ]
    try:
        subprocess.run(["openssl", "version"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(cmd, check=True)
        logger.info("Certificate generated successfully.")
    except Exception as e:
        logger.error(f"Error generating certificate: {e}")
        sys.exit(1)

async def main():
    parser = argparse.ArgumentParser(description="NVDA Remote Async Server")
    
    # Configuration
    parser.add_argument("--port", type=int, default=int(os.environ.get("NVDA_REMOTE_PORT", 6837)))
    parser.add_argument("--certfile", default=os.environ.get("NVDA_REMOTE_CERTFILE", "server.pem"))
    parser.add_argument("--keyfile", default=os.environ.get("NVDA_REMOTE_KEYFILE", "server.pem"))
    parser.add_argument("--motd", default=os.environ.get("NVDA_REMOTE_MOTD"))
    parser.add_argument("--motd-force", action="store_true", default=str_to_bool(os.environ.get("NVDA_REMOTE_MOTD_FORCE", "False")))
    parser.add_argument("--debug", action="store_true", default=str_to_bool(os.environ.get("NVDA_REMOTE_DEBUG", "False")))
    parser.add_argument("--tracebacks", action="store_true", default=str_to_bool(os.environ.get("NVDA_REMOTE_TRACEBACKS", "False")))
    parser.add_argument("--ping-interval", type=int, default=int(os.environ.get("NVDA_REMOTE_PING_INTERVAL", 60)))
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("NVDA_REMOTE_TIMEOUT", 300)))
    parser.add_argument("--max-msg-size", type=int, default=int(os.environ.get("NVDA_REMOTE_MAX_MSG_SIZE", 1048576)))
    
    # Helper commands
    parser.add_argument("--generate-cert", action="store_true", help="Generate a self-signed certificate and exit")

    args = parser.parse_args()
    logger.setLevel(logging.DEBUG if args.debug else logging.INFO)

    if args.generate_cert:
        generate_certificate()
        sys.exit(0)

    cert_content = os.environ.get("NVDA_REMOTE_CERT_CONTENT")
    if cert_content:
        logger.info("Found certificate content in environment variables. Writing to file.")
        try:
            with open("server.pem", "wb") as f:
                f.write(base64.b64decode(cert_content))
        except Exception as e:
            logger.error(f"Failed to decode certificate from environment: {e}")
            sys.exit(1)

    ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    try:
        ssl_ctx.load_cert_chain(certfile=args.certfile, keyfile=args.keyfile)
    except FileNotFoundError:
        logger.error(f"Certificates not found. Did you forget to generate them or set NVDA_REMOTE_CERT_CONTENT?")
        sys.exit(1)

    server_instance = AsyncServer(args)
    
    # Force IPv4 binding (Fixes the Dual Stack issue)
    server = await asyncio.start_server(
        server_instance.handle_client, '0.0.0.0', args.port, ssl=ssl_ctx
    )
    logger.info(f"Serving on 0.0.0.0:{args.port} (IPv4 Only)")
    
    async with server:
        await server.serve_forever()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass