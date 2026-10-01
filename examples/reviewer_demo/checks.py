"""Independent checks. TraceBridge is not allowed to edit this file."""
import sys
from app import accepted

if sys.argv[1] == "boundary":
    if not accepted(5):
        print("QUOTA_BOUNDARY: five items must be accepted")
        raise SystemExit(1)
else:
    assert accepted(0) and accepted(4) and not accepted(-1) and not accepted(6)
print("CHECK_PASSED")
