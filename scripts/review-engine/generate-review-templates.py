#!/usr/bin/env python3
"""Generate brand-voiced SMS + email review-request templates from client config.

Usage:
    python3 generate-review-templates.py --config configs/<client-slug>.json [--output-dir outputs/]

Produces ready-to-copy text files with {first_name}, {service}, {city} placeholders
for the owner/tech to fill at send time. Also produces a "filled example" with sample values.

Fails loudly if review_link contains a FILL: placeholder.
"""

import argparse
import json
import os
import sys


def load_config(config_path: str) -> dict:
    with open(config_path) as f:
        return json.load(f)


def validate_config(config: dict, config_path: str) -> None:
    required = [
        "client_slug", "business_name", "owner_name", "owner_first_name",
        "owner_phone", "review_link", "brand_voice", "primary_services",
    ]
    missing = [k for k in required if k not in config]
    if missing:
        print(f"FAIL: config {config_path} missing required keys: {missing}", file=sys.stderr)
        sys.exit(1)

    if config["review_link"].startswith("FILL:"):
        print(
            f"FAIL: review_link in {config_path} is a placeholder — "
            f"operator must provide the real GBP review URL before generating templates.\n"
            f"  Current value: {config['review_link']}",
            file=sys.stderr,
        )
        sys.exit(1)


SMS_TEMPLATE = """Hi {{first_name}}, thanks for choosing {business_name} for your {{service}} today! If we did a good job, a 30-second Google review would mean a lot: {review_link}

{sign_off}"""

EMAIL_TEMPLATE = """Subject: Thank you from {business_name}

Hi {{first_name}},

It was a pleasure handling your {{service}} in {{city}}. If you were happy with the work, would you leave us a quick Google review? It genuinely helps us reach more neighbors: {review_link}

Thanks again,
{owner_name}
{business_name} — {owner_phone}"""

VERBAL_SCRIPT = """VERBAL ASK SCRIPT (at job completion):

"If you were happy with the work, a quick Google review really helps a small local business like ours — I'll text you the link."

(This sets up the SMS follow-up and doubles conversion. Ask EVERY customer — no review-gating.)"""

RESPONSE_POSITIVE = """POSITIVE REVIEW RESPONSE TEMPLATE:

Thank you so much, {{first_name}}! Glad we could help with your {{service}} in {{city}}. We appreciate you trusting {business_name} — it means a lot to our team.

{sign_off}"""

RESPONSE_NEGATIVE = """NEGATIVE REVIEW RESPONSE TEMPLATE:

{{first_name}}, thank you for your feedback. We take every concern seriously and want to make this right. Please reach out to us directly so we can understand what happened and find a solution.

{sign_off}"""


def render_template(template: str, config: dict) -> str:
    return template.format(
        business_name=config["business_name"],
        owner_name=config["owner_name"],
        owner_first_name=config["owner_first_name"],
        owner_phone=config["owner_phone"],
        review_link=config["review_link"],
        sign_off=config["brand_voice"]["sign_off_style"],
    )


def render_filled_example(template_text: str, config: dict) -> str:
    sample_service = config["primary_services"][0] if config["primary_services"] else "service"
    sample_city = config.get("service_area", "your area").split(",")[0].strip()
    return template_text.replace("{first_name}", "Alex").replace(
        "{service}", sample_service
    ).replace("{city}", sample_city)


def write_file(path: str, content: str) -> None:
    with open(path, "w") as f:
        f.write(content)
    print(f"  Written: {path}")


def main():
    parser = argparse.ArgumentParser(description="Generate review-request templates from client config")
    parser.add_argument("--config", required=True, help="Path to client config JSON")
    parser.add_argument("--output-dir", default=None, help="Output directory (default: outputs/<client_slug>/)")
    args = parser.parse_args()

    config = load_config(args.config)
    validate_config(config, args.config)

    slug = config["client_slug"]
    output_dir = args.output_dir or os.path.join(os.path.dirname(__file__), "outputs", slug)
    os.makedirs(output_dir, exist_ok=True)

    print(f"Generating review-request templates for: {config['business_name']}")

    templates = {
        "sms-template.txt": SMS_TEMPLATE,
        "email-template.txt": EMAIL_TEMPLATE,
        "verbal-ask-script.txt": VERBAL_SCRIPT,
        "response-positive-template.txt": RESPONSE_POSITIVE,
        "response-negative-template.txt": RESPONSE_NEGATIVE,
    }

    for filename, tmpl in templates.items():
        rendered = render_template(tmpl, config)
        path = os.path.join(output_dir, f"{slug}-{filename}")
        write_file(path, rendered)

    # Write a filled example for the owner to see what the SMS/email looks like
    sms_rendered = render_template(SMS_TEMPLATE, config)
    email_rendered = render_template(EMAIL_TEMPLATE, config)
    example_content = (
        f"=== FILLED EXAMPLE (for {config['business_name']}) ===\n"
        f"These use sample values: first_name=Alex, service={config['primary_services'][0]}, "
        f"city={config.get('service_area', 'your area').split(',')[0].strip()}\n\n"
        f"--- SMS ---\n{render_filled_example(sms_rendered, config)}\n\n"
        f"--- EMAIL ---\n{render_filled_example(email_rendered, config)}\n"
    )
    example_path = os.path.join(output_dir, f"{slug}-filled-example.txt")
    write_file(example_path, example_content)

    # Policy reminder card
    policy = config.get("policy", {})
    policy_text = (
        "REVIEW REQUEST POLICY GUARDRAILS\n"
        "================================\n\n"
        f"No incentives for reviews: {'YES — enforced' if policy.get('no_incentives') else 'NOT SET — configure in config'}\n"
        f"No review-gating (ask every customer): {'YES — enforced' if policy.get('no_review_gating') else 'NOT SET — configure in config'}\n"
        f"Real customers only: {'YES — enforced' if policy.get('real_customers_only') else 'NOT SET — configure in config'}\n\n"
        "Violating any of these risks Google flagging or removing your reviews.\n"
        "The verbal ask + SMS follow-up sequence is designed to be compliant.\n"
    )
    policy_path = os.path.join(output_dir, f"{slug}-policy-guardrails.txt")
    write_file(policy_path, policy_text)

    print(f"\nDone. {len(templates) + 2} files generated in {output_dir}")


if __name__ == "__main__":
    main()
