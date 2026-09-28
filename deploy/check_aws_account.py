"""Read-only check of an explicit AWS profile, account, and active Free plan."""

import argparse
from datetime import datetime, timezone
import json
import os
import re
import subprocess
import sys


def verify_account(profile, expected_account, query):
    if not profile or profile == "default":
        raise ValueError("Use a dedicated named AWS profile for the new account")
    if not re.fullmatch(r"[0-9]{12}", expected_account):
        raise ValueError("Expected account ID must contain 12 digits")
    identity = query(["sts", "get-caller-identity"], "ap-northeast-2", profile)
    if identity.get("Account") != expected_account:
        raise ValueError("Selected AWS profile does not match the expected account; stop here")
    plan = query(["freetier", "get-account-plan-state"], "us-east-1", profile)
    if plan.get("accountId") != expected_account:
        raise ValueError("Free Tier response belongs to a different account")
    if plan.get("accountPlanType") != "FREE" or plan.get("accountPlanStatus") != "ACTIVE":
        raise ValueError("Target account is not an active Free plan")
    credit = plan.get("accountPlanRemainingCredits", {})
    if credit.get("unit") != "USD" or float(credit.get("amount", 0)) <= 0:
        raise ValueError("Free plan has no confirmed positive USD credit balance")
    expiration = plan.get("accountPlanExpirationDate", "")
    if datetime.fromisoformat(expiration.replace("Z", "+00:00")) <= datetime.now(timezone.utc):
        raise ValueError("Free plan has expired")
    return {"account_matches": True, "profile": profile, "plan": "FREE", "status": "ACTIVE",
            "remaining_credit_usd": credit["amount"], "expires_at": expiration,
            "resources_created": False}


def aws_query(arguments, region, profile):
    environment = os.environ.copy()
    environment.update(AWS_MAX_ATTEMPTS="1", AWS_EC2_METADATA_DISABLED="true", AWS_PAGER="")
    command = ["aws", *arguments, "--profile", profile, "--region", region,
               "--output", "json", "--no-cli-pager", "--cli-connect-timeout", "5", "--cli-read-timeout", "10"]
    completed = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=30)
    if completed.returncode:
        error = re.search(r"An error occurred \(([^)]+)\)", completed.stderr)
        raise RuntimeError("AWS read-only check failed: " + (error.group(1) if error else "CLI, credentials, or network unavailable"))
    return json.loads(completed.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--expected-account", required=True)
    args = parser.parse_args()
    try:
        result = verify_account(args.profile, args.expected_account, aws_query)
    except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
