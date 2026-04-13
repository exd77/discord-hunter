import argparse
import base64
import json
import os
from pathlib import Path

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
)


def build_payload(args):
    return {
        "os": args.os,
        "browser": args.browser,
        "device": args.device,
        "system_locale": args.system_locale,
        "browser_user_agent": args.user_agent,
        "browser_version": args.browser_version,
        "os_version": args.os_version,
        "referrer": args.referrer,
        "referring_domain": args.referring_domain,
        "referrer_current": args.referrer_current,
        "referring_domain_current": args.referring_domain_current,
        "release_channel": args.release_channel,
        "client_build_number": args.build_number,
        "client_event_source": None,
        "design_id": 0,
    }


def encode_payload(payload):
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return base64.b64encode(raw.encode()).decode()


def maybe_update_env(env_path: Path, build_number: str, encoded: str):
    if not env_path.exists():
        return False

    content = env_path.read_text(encoding="utf-8")
    lines = content.splitlines()
    updated = []
    seen_build = False
    seen_super = False

    for line in lines:
        if line.startswith("DISCORD_BUILD_NUMBER="):
            updated.append(f"DISCORD_BUILD_NUMBER={build_number}")
            seen_build = True
        elif line.startswith("DISCORD_SUPER_PROPERTIES="):
            updated.append(f"DISCORD_SUPER_PROPERTIES={encoded}")
            seen_super = True
        else:
            updated.append(line)

    if not seen_build:
        updated.append(f"DISCORD_BUILD_NUMBER={build_number}")
    if not seen_super:
        updated.append(f"DISCORD_SUPER_PROPERTIES={encoded}")

    env_path.write_text("\n".join(updated) + "\n", encoding="utf-8")
    return True


def main():
    parser = argparse.ArgumentParser(description="Generate Discord X-Super-Properties header value")
    parser.add_argument("--os", default="Linux")
    parser.add_argument("--browser", default="Chrome")
    parser.add_argument("--device", default="")
    parser.add_argument("--system-locale", default="en-US")
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    parser.add_argument("--browser-version", default="134.0.0.0")
    parser.add_argument("--os-version", default="6.8.0")
    parser.add_argument("--referrer", default="")
    parser.add_argument("--referring-domain", default="")
    parser.add_argument("--referrer-current", default="")
    parser.add_argument("--referring-domain-current", default="")
    parser.add_argument("--release-channel", default="stable")
    parser.add_argument("--build-number", default=os.getenv("DISCORD_BUILD_NUMBER", "9999"))
    parser.add_argument("--write-env", action="store_true", help="update .env in current directory")
    args = parser.parse_args()

    payload = build_payload(args)
    encoded = encode_payload(payload)

    print("Decoded JSON:")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    print("\nBase64 X-Super-Properties:")
    print(encoded)

    if args.write_env:
        env_path = Path(".env")
        if maybe_update_env(env_path, str(args.build_number), encoded):
            print(f"\nUpdated {env_path}")
        else:
            print("\n.env not found, skipped writing")


if __name__ == "__main__":
    main()
