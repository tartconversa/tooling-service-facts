"""列出已交付的原始资料。"""
import json
from pathlib import Path


def iter_deliveries(source_dir):
    for path in sorted(source_dir.glob("*.json")):
        with path.open(encoding="utf-8") as incoming:
            yield json.load(incoming)


def main():
    source_dir = Path(__file__).resolve().parent / "sources"
    for delivery in iter_deliveries(source_dir):
        print(f"{delivery['intake']:02d} {delivery['table']} {len(delivery['records'])}")


if __name__ == "__main__":
    main()
