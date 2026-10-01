"""The product contract accepts between zero and five items, inclusive."""
ERROR_CODE = "QUOTA_BOUNDARY"


def accepted(count):
    return 0 <= count < 5
