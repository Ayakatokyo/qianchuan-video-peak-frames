#!/usr/bin/env python3
import argparse
import sys

from diagnostic_analysis_lib import DiagnosticAnalysisError, prepare_diagnostic_analysis


def build_parser():
    parser = argparse.ArgumentParser(
        description="Prepare Qianchuan diagnostic AI-analysis facts."
    )
    parser.add_argument("--top-materials-json", required=True)
    parser.add_argument("--diagnosis-records-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--prompt-template")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        data_path, prompt_path, _analysis = prepare_diagnostic_analysis(**vars(args))
    except DiagnosticAnalysisError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"diagnostic_analysis_data={data_path}")
    print(f"diagnostic_analysis_prompt={prompt_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
