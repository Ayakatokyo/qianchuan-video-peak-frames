#!/usr/bin/env python3
import argparse
import sys

from commercial_report_lib import CommercialReportError, generate_report


def build_parser():
    parser = argparse.ArgumentParser(
        description="Generate Qianchuan material audience commercial report."
    )
    parser.add_argument("--top-materials-json", required=True)
    parser.add_argument("--peak-output-dir", required=True)
    parser.add_argument("--diagnosis-records-dir", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-html", required=True)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        generate_report(
            args.top_materials_json,
            args.peak_output_dir,
            args.diagnosis_records_dir,
            args.output_json,
            args.output_html,
        )
    except CommercialReportError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(f"analysis_json={args.output_json}")
    print(f"report_html={args.output_html}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
