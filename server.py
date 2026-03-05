import asyncio
import json
import ssl
import sys
import logging
import argparse
import secrets
import string
import os
import traceback
import itertools
import socket
import subprocess
import base64

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("NVDARemote")

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
        # Strong key generation
        alphabet = string.ascii_letters + string.digits
        for _ in range(100):
            key = "".join(secrets.choice(alphabet) for _ in range(8))
            if key not in self.channels:
                return key
        return "".join(secrets.choice(alphabet) for _ in range(12))

    async def handle_client(self, reader, writer):
        addr = writer.get_extra_info('peername')
        logger.info(f"Accepted connection from {addr}")

        sock = writer.get_extra_info('socket')
        if sock:
            try:
                # 1. TCP_NODELAY: Low Latency (Instant Audio)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                # 2. SO_KEEPALIVE: The Silent Heartbeat (No JSON Pings!)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                # Linux specific: Set keepalive parameters (idle 60s, interval 10s, 3 fails)
                if sys.platform == 'linux':
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 60)
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10)
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
            except (OSError, AttributeError):
                pass

        client = Client(reader, writer, self)
        self.clients.add(client)
        client.start_tasks()

        try:
            while True:
                line = await reader.readline()

                if not line: 
                    logger.info(f"Client {client.id} from {addr} closed connection.")
                    break 
                
                if self.args.max_msg_size > 0 and len(line) > self.args.max_msg_size:
                    logger.warning(f"Client {client.id} exceeded max message size.")
                    break

                await client.process_message(line)

        except ConnectionResetError:
            pass
        except Exception as e:
            if self.args.tracebacks:
                logger.error(f"Error client {client.id}:\n{traceback.format_exc()}")
            else:
                logger.error(f"Error client {client.id}: {e}")
        finally:
            await client.cleanup()
            self.clients.discard(client)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
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
        self.connection_type = "unknown"

    def start_tasks(self):
        self.writer_task = asyncio.create_task(self.write_loop())

    async def write_loop(self):
        try:
            while True:
                data = await self.out_queue.get()
                self.out_queue.task_done()
                if data is None:
                    self.writer.close()
                    break
                self.writer.write(data)
                await self.writer.drain()
        except Exception:
            pass

    async def enqueue(self, data):
        try:
            self.out_queue.put_nowait(data)
        except asyncio.QueueFull:
            self.writer.close()

    async def disconnect(self):
        await self.out_queue.put(None)

    async def process_message(self, line):
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return

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
        await self.disconnect()

    async def do_join(self, data):
        new_channel = data.get('channel')
        self.connection_type = data.get('connection_type', 'unknown')
        
        if not new_channel:
            await self.send_error("invalid_parameters")
            return

        if self.channel_id and self.channel_id in self.server.channels:
             self.server.channels[self.channel_id].discard(self)
             if not self.server.channels[self.channel_id]:
                 del self.server.channels[self.channel_id]

        self.channel_id = new_channel
        if self.channel_id not in self.server.channels:
            logger.info(f"Channel '{self.channel_id}' created by Client {self.id}")
            self.server.channels[self.channel_id] = set()
        self.server.channels[self.channel_id].add(self)

        peers = [c for c in self.server.channels[self.channel_id] if c != self]
        
        response = {
            "type": "channel_joined",
            "channel": new_channel,
            "user_ids": [c.id for c in peers],
            "clients": [{"id": c.id, "connection_type": c.connection_type} for c in peers]
        }
        await self.send_json(response)
        
        if self.server.args.motd:
            await self.send_json({
                "type": "motd", 
                "motd": self.server.args.motd,
                "force_display": self.server.args.motd_force
            })

        await self.broadcast({
            "type": "client_joined", 
            "client": {"id": self.id, "connection_type": self.connection_type}
        }, include_self=False)

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

        if self.channel_id and self.channel_id in self.server.channels:
            # Broadcast departure before removing from channel
            await self.broadcast({"type": "client_left", "user_id": self.id}, include_self=False)
            self.server.channels[self.channel_id].discard(self)
            if not self.server.channels[self.channel_id]:
                logger.info(f"Channel '{self.channel_id}' destroyed (empty).")
                del self.server.channels[self.channel_id]

def generate_certificate():
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=int(os.environ.get("NVDA_REMOTE_PORT", 6837)))
    parser.add_argument("--certfile", default=os.environ.get("NVDA_REMOTE_CERTFILE", "server.pem"))
    parser.add_argument("--keyfile", default=os.environ.get("NVDA_REMOTE_KEYFILE", "server.pem"))
    parser.add_argument("--motd", default=os.environ.get("NVDA_REMOTE_MOTD"))
    parser.add_argument("--motd-force", action="store_true", default=str_to_bool(os.environ.get("NVDA_REMOTE_MOTD_FORCE", "False")))
    parser.add_argument("--debug", action="store_true", default=str_to_bool(os.environ.get("NVDA_REMOTE_DEBUG", "False")))
    parser.add_argument("--tracebacks", action="store_true", default=str_to_bool(os.environ.get("NVDA_REMOTE_TRACEBACKS", "False")))
    parser.add_argument("--max-msg-size", type=int, default=int(os.environ.get("NVDA_REMOTE_MAX_MSG_SIZE", 1048576)))
    parser.add_argument("--generate-cert", action="store_true")

    args = parser.parse_args()
    logger.setLevel(logging.DEBUG if args.debug else logging.INFO)

    if args.generate_cert:
        generate_certificate()
        sys.exit(0)

    cert_content = os.environ.get("NVDA_REMOTE_CERT_CONTENT")
    if cert_content:
        try:
            with open(args.certfile, "wb") as f:
                f.write(base64.b64decode(cert_content))
        except Exception:
            logger.error("Failed to decode NVDA_REMOTE_CERT_CONTENT")
            sys.exit(1)

    ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    try:
        ssl_ctx.load_cert_chain(certfile=args.certfile, keyfile=args.keyfile)
    except FileNotFoundError:
        generate_certificate()
        ssl_ctx.load_cert_chain(certfile=args.certfile, keyfile=args.keyfile)
    ssl_ctx.options |= ssl.OP_NO_TLSv1 | ssl.OP_NO_TLSv1_1 

    server_instance = AsyncServer(args)
    server = await asyncio.start_server(server_instance.handle_client, '0.0.0.0', args.port, ssl=ssl_ctx)
    logger.info(f"Serving on 0.0.0.0:{args.port}")
    
    async with server:
        await server.serve_forever()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass