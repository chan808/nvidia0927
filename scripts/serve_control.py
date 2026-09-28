"""Run the private-owner control plane; NVIDIA credentials stay on this host."""
import argparse
import os
from pathlib import Path
import secrets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--init-token-file", type=Path)
    args = parser.parse_args()
    if args.init_token_file:
        path = args.init_token_file
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as stream:
            stream.write(secrets.token_urlsafe(48))
        if os.name != "nt":
            os.chmod(path, 0o600)
        print("Operator token file created:", path.resolve())
        return
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    import uvicorn
    uvicorn.run("tracebridge.control_plane:app_from_env", factory=True, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
