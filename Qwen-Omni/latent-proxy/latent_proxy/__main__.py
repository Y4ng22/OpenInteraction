"""Command line entry: python3 -m latent_proxy --store ../history/latents"""

from __future__ import annotations

import argparse
import os

from .extractors import StubExtractor
from .server import ProxyConfig, create_server, log
from .store import FileLatentStore

DEFAULT_UPSTREAM = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def main() -> None:
    parser = argparse.ArgumentParser(prog="latent_proxy", description=__doc__)
    parser.add_argument("--host", default=os.environ.get("LATENT_PROXY_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("LATENT_PROXY_PORT", "8765")))
    parser.add_argument(
        "--upstream",
        default=os.environ.get("LATENT_PROXY_UPSTREAM", DEFAULT_UPSTREAM),
        help="OpenAI-compatible base URL of the background model server",
    )
    parser.add_argument(
        "--store",
        default=os.environ.get("LATENT_PROXY_STORE_DIR", ""),
        required=not os.environ.get("LATENT_PROXY_STORE_DIR"),
        help="latent store directory; must match QWEN_AUDIO_LATENT_STORE_DIR",
    )
    parser.add_argument("--stub-tokens", type=int, default=16)
    parser.add_argument("--stub-dim", type=int, default=64)
    parser.add_argument(
        "--final-delay-ms",
        type=int,
        default=0,
        help="test only: hold each final Task answer this long (voice e2e steering)",
    )
    args = parser.parse_args()

    config = ProxyConfig(
        upstream=args.upstream,
        store=FileLatentStore(args.store),
        extractor=StubExtractor(tokens=args.stub_tokens, dim=args.stub_dim),
        final_delay_s=max(0, args.final_delay_ms) / 1000,
    )
    server = create_server(config, args.host, args.port)
    log(f"listening on http://{args.host}:{args.port}/v1 -> {args.upstream}")
    log(f"store: {config.store.root} (extractor: {config.extractor.model})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
