#!/usr/bin/env python3
"""Generate a QR code image pointing to a client's Google review link.

Usage:
    python3 generate-review-qr.py --config configs/<client-slug>.json [--output-dir outputs/]

Reads the review_link from the per-client config and produces a PNG QR code.
Fails loudly if review_link contains a FILL: placeholder.
"""

import argparse
import json
import os
import sys

import qrcode
from qrcode.image.styledpil import StyledPilImage
from qrcode.image.styles.moduledrawers import RoundedModuleDrawer


def load_config(config_path: str) -> dict:
    with open(config_path) as f:
        return json.load(f)


def validate_config(config: dict, config_path: str) -> None:
    required = ["client_slug", "business_name", "review_link"]
    missing = [k for k in required if k not in config]
    if missing:
        print(f"FAIL: config {config_path} missing required keys: {missing}", file=sys.stderr)
        sys.exit(1)

    if config["review_link"].startswith("FILL:"):
        print(
            f"FAIL: review_link in {config_path} is a placeholder — "
            f"operator must provide the real GBP review URL before generating QR.\n"
            f"  Current value: {config['review_link']}",
            file=sys.stderr,
        )
        sys.exit(1)


def generate_qr(review_link: str, output_path: str, business_name: str) -> None:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=4,
    )
    qr.add_data(review_link)
    qr.make(fit=True)

    img = qr.make_image(
        image_factory=StyledPilImage,
        module_drawer=RoundedModuleDrawer(),
    )
    img.save(output_path)
    print(f"OK: QR code saved to {output_path}")
    print(f"  Business: {business_name}")
    print(f"  Review link: {review_link}")


def main():
    parser = argparse.ArgumentParser(description="Generate review QR code from client config")
    parser.add_argument("--config", required=True, help="Path to client config JSON")
    parser.add_argument("--output-dir", default=None, help="Output directory (default: outputs/<client_slug>/)")
    args = parser.parse_args()

    config = load_config(args.config)
    validate_config(config, args.config)

    slug = config["client_slug"]
    output_dir = args.output_dir or os.path.join(os.path.dirname(__file__), "outputs", slug)
    os.makedirs(output_dir, exist_ok=True)

    output_path = os.path.join(output_dir, f"{slug}-review-qr.png")
    generate_qr(config["review_link"], output_path, config["business_name"])


if __name__ == "__main__":
    main()
