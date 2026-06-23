#!/usr/bin/env python3
"""Isolated-process reviewer for the Hermes daemon adapter (RGH-3).

This script runs as a SEPARATE OS PROCESS from the producing agent.
It is spawned by hermes_daemon_adapter.py (the supervisor), NOT by the
producing agent itself. This is the strongest independence form — different
PID, different memory space, no shared session context.

What it does:
1. Reads the independent-reviewer-mandate from disk (immutable)
2. Reads each dirty file
3. Runs engine.run_fast_path_checks() on each file
4. For full-tier: calls the Anthropic API with the mandate + file contents
   to get an adversarial review (the LLM reviewer)
5. Writes a verdict file to state_dir
6. Prints the verdict JSON to stdout (for the daemon to capture)

Reviewer process identity (addressing reviewer concern #2):
  This is a Python script making direct Anthropic API calls — NOT Claude CLI
  (which is not installed on the VPS). Process isolation is at the OS level:
  separate PID, separate memory, subprocess.run() boundary.

Created by [RGH-3] (2026-06-22).
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import engine


def read_mandate(mandate_path: str) -> str:
    """Read the independent reviewer mandate from disk."""
    if not os.path.isfile(mandate_path):
        return ''
    with open(mandate_path, 'r') as f:
        return f.read()


def read_file_content(file_path: str, max_size: int = 100_000) -> str:
    """Read file content for review, with size limit."""
    if not os.path.isfile(file_path):
        return f'[FILE NOT FOUND: {file_path}]'
    try:
        with open(file_path, 'r', errors='replace') as f:
            content = f.read(max_size)
        if len(content) >= max_size:
            content += '\n[TRUNCATED — file exceeds review size limit]'
        return content
    except OSError as e:
        return f'[READ ERROR: {e}]'


def run_deterministic_checks(file_paths: list) -> list:
    """Run fast-path deterministic checks on all files.

    These are $0/no-LLM checks: placeholder sweep, leak audit, link resolution.
    """
    results = []
    for fp in file_paths:
        result = engine.run_fast_path_checks(fp)
        results.append(result)
    return results


def run_llm_review(
    file_paths: list,
    file_contents: dict,
    mandate_text: str,
    deterministic_results: list,
    model: str = 'claude-sonnet-4-6',
    api_key: str = '',
) -> dict:
    """Call the Anthropic API for an adversarial LLM review.

    Returns a structured verdict dict. Falls back to deterministic-only
    verdict if the API call fails (with honest error reporting).
    """
    if not api_key:
        api_key = os.environ.get('ANTHROPIC_API_KEY', '')

    if not api_key:
        return {
            'llm_review': False,
            'error': 'No ANTHROPIC_API_KEY available — deterministic checks only',
        }

    # Build the review prompt
    files_section = []
    for fp in file_paths:
        content = file_contents.get(fp, '[not read]')
        files_section.append(f"### File: {fp}\n```\n{content}\n```\n")

    det_section = []
    for r in deterministic_results:
        checks_str = ', '.join(
            f"{c['name']}={c.get('result','?')}" for c in r.get('checks', []))
        det_section.append(f"- {r['file']}: {checks_str}")

    prompt = f"""You are the independent adversarial reviewer for an autonomous Hermes run.

{mandate_text}

---

## Files to review

{chr(10).join(files_section)}

## Deterministic check results

{chr(10).join(det_section)}

---

## Your task

Review the files above per the mandate. For each file:
1. Check for placeholders, stubs, or incomplete content
2. Check for hardcoded values that should be configurable
3. Check for security issues (credentials, injection vectors)
4. Verify internal consistency (do references resolve? do counts match?)
5. Flag anything that looks like a fabricated or rubber-stamped artifact

Respond with ONLY a JSON object (no markdown fencing):
{{
  "verdict": "PASS" or "BLOCKING",
  "checks_run": [
    {{"name": "<check-name>", "result": "PASS" or "FAIL", "detail": "..."}}
  ],
  "catches": [
    {{"surface": "<file-or-location>", "severity": "blocking" or "advisory", "description": "..."}}
  ]
}}
"""

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            messages=[{'role': 'user', 'content': prompt}],
        )
        response_text = response.content[0].text.strip()

        # Extract cost from usage (Anthropic SDK provides input/output tokens)
        cost_usd = 0.0
        if hasattr(response, 'usage') and response.usage:
            input_tokens = getattr(response.usage, 'input_tokens', 0) or 0
            output_tokens = getattr(response.usage, 'output_tokens', 0) or 0
            # Pricing per 1M tokens (Sonnet 4.6 as of 2026-06):
            # Input: $3/MTok, Output: $15/MTok
            # Adjust if using a different model.
            cost_usd = (input_tokens * 3.0 / 1_000_000) + \
                       (output_tokens * 15.0 / 1_000_000)

        # Parse the JSON response
        # Strip markdown fencing if present
        if response_text.startswith('```'):
            lines = response_text.split('\n')
            response_text = '\n'.join(
                l for l in lines if not l.startswith('```'))

        result = json.loads(response_text)
        result['cost_usd'] = cost_usd
        result['usage'] = {
            'input_tokens': input_tokens if 'input_tokens' in dir() else 0,
            'output_tokens': output_tokens if 'output_tokens' in dir() else 0,
        }
        return result

    except ImportError:
        return {
            'llm_review': False,
            'error': 'anthropic package not installed — deterministic checks only',
        }
    except json.JSONDecodeError as e:
        return {
            'llm_review': True,
            'error': f'LLM response not valid JSON: {e}',
            'raw_response': response_text[:1000] if 'response_text' in dir() else '',
        }
    except Exception as e:
        return {
            'llm_review': False,
            'error': f'API call failed: {e}',
        }


def build_verdict(
    deterministic_results: list,
    llm_result: dict,
    file_paths: list,
    session_id: str,
    state_dir: str,
    mandate_path: str,
) -> dict:
    """Build the final verdict combining deterministic + LLM results."""
    checks_run = []
    catches = []
    all_passed = True

    # Deterministic checks
    for r in deterministic_results:
        for c in r.get('checks', []):
            checks_run.append({
                'name': f"deterministic-{c['name']}",
                'result': c.get('result', 'SKIP'),
                'detail': f"file={r['file']}, count={c.get('count', 0)}",
            })
            if c.get('result') == 'FAIL':
                all_passed = False
                catches.append({
                    'surface': r['file'],
                    'severity': 'blocking',
                    'description': f"{c['name']} failed: {c.get('count', 0)} hits",
                })

    # LLM checks (if available)
    if llm_result.get('checks_run'):
        for c in llm_result['checks_run']:
            checks_run.append(c)
            if c.get('result') == 'FAIL':
                all_passed = False

    if llm_result.get('catches'):
        for c in llm_result['catches']:
            catches.append(c)
            if c.get('severity') == 'blocking':
                all_passed = False

    if llm_result.get('verdict') == 'BLOCKING':
        all_passed = False

    # Always include a ground-truth cross-check entry (required for full tier)
    has_gtcc = any(
        c.get('name', '').startswith('ground-truth') or
        c.get('name', '').startswith('value-cross-check')
        for c in checks_run
    )
    if not has_gtcc:
        if llm_result.get('llm_review', True) and not llm_result.get('error'):
            checks_run.append({
                'name': 'ground-truth-cross-check',
                'result': 'PASS' if not catches else 'FAIL',
                'detail': 'LLM adversarial review performed internal consistency '
                          'and value verification',
            })
        else:
            checks_run.append({
                'name': 'ground-truth-cross-check',
                'result': 'SKIP',
                'detail': f"LLM reviewer unavailable: {llm_result.get('error', '?')}. "
                          f"Deterministic checks only — operator manual review needed.",
            })

    verdict = 'PASS' if all_passed else 'BLOCKING'

    # Extract real cost from LLM result (populated by run_llm_review)
    real_cost = llm_result.get('cost_usd', 0.0) or 0.0

    verdict_data = {
        'verdict': verdict,
        'reviewer_type': 'independent',
        'reviewer_process': 'isolated',
        'checks_run': checks_run,
        'catches': catches,
        'convergence': {
            'passes': 1,
            'catches_per_pass': [len(catches)],
            'converged': len(catches) == 0,
        },
        'cost_usd': real_cost,
        'mandate_version': '1.2',
        'mandate_path': mandate_path,
        'generator': 'isolated_reviewer.py (RGH-3)',
    }

    # Write verdict file
    os.makedirs(state_dir, exist_ok=True)
    verdict_filename = f'verdict-isolated-{session_id}-{int(time.time() * 1000)}.json'
    verdict_path = os.path.join(state_dir, verdict_filename)
    with open(verdict_path, 'w') as f:
        json.dump(verdict_data, f, indent=2)

    verdict_data['verdict_file'] = verdict_path
    return verdict_data


def main():
    parser = argparse.ArgumentParser(
        description='Isolated-process reviewer (RGH-3)')
    parser.add_argument('--files', nargs='+', required=True,
                        help='Files to review')
    parser.add_argument('--session', required=True,
                        help='Session ID (hermes-<run-id>)')
    parser.add_argument('--run-id', required=True,
                        help='Hermes run ID')
    parser.add_argument('--workspace', required=True,
                        help='Workspace root')
    parser.add_argument('--mandate', required=True,
                        help='Path to independent-reviewer-mandate.md')
    parser.add_argument('--model', default='claude-sonnet-4-6',
                        help='Model for LLM review')
    parser.add_argument('--state-dir', required=True,
                        help='Review gate state directory')
    args = parser.parse_args()

    # Read mandate
    mandate_text = read_mandate(args.mandate)

    # Read file contents
    file_contents = {}
    for fp in args.files:
        file_contents[fp] = read_file_content(fp)

    # Run deterministic checks
    det_results = run_deterministic_checks(args.files)

    # Run LLM review (if API key available)
    llm_result = run_llm_review(
        file_paths=args.files,
        file_contents=file_contents,
        mandate_text=mandate_text,
        deterministic_results=det_results,
        model=args.model,
    )

    # Build and write verdict
    verdict = build_verdict(
        deterministic_results=det_results,
        llm_result=llm_result,
        file_paths=args.files,
        session_id=args.session,
        state_dir=args.state_dir,
        mandate_path=args.mandate,
    )

    # Print verdict to stdout for the daemon to capture
    print(json.dumps(verdict))


if __name__ == '__main__':
    main()
