"""Query China's official customs tariff and VAT service for an explicit HS code."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from app.tools.china_customs import lookup_rates


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hs-code", required=True)
    parser.add_argument("--goods-name", default="")
    args = parser.parse_args()
    rates = lookup_rates(args.hs_code, goods_name=args.goods_name)
    print(json.dumps(asdict(rates), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
