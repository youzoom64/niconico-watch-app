import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from app import tracker


STEPS = [
    "step07_image_generator",
    "step08_conversation_generator",
    "step09_screenshot_generator",
    "step10_comment_processor",
    "step11_special_user_html_generator",
    "step12_html_generator",
    "step13_index_generator",
    "step14_modern_list_generator",
    "step15_lolipop_uploader",
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lv", required=True)
    parser.add_argument("--account-id", required=True)
    args = parser.parse_args()
    tracker.run_legacy_archiver_steps(
        args.lv,
        account_id=args.account_id,
        steps=STEPS,
        force_overwrite_existing_html=True,
    )


if __name__ == "__main__":
    main()
